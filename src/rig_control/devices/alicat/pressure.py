"""Pressure-only adapter for a manually configured, inverse-control Alicat MC."""

from dataclasses import replace
from math import isclose
from decimal import Decimal, localcontext, ROUND_FLOOR

from rig_control.devices.alicat.configuration import AlicatMfcConfiguration
from rig_control.devices.alicat.control_support import AlicatVerifiedControl
from rig_control.devices.alicat.measurements import state_measurements
from rig_control.devices.alicat.protocol import AlicatProtocolClient
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.pressure_controller import PressureController, PressurePolicy, absolute_unit_factor
from rig_control.models import DeviceStatus, Measurement
from rig_control.read_retry import retry_read


class AlicatBackPressureController(AlicatVerifiedControl, PressureController):
    def __init__(self, configuration: AlicatMfcConfiguration, protocol: AlicatProtocolClient,
                 *, pressure_policy: PressurePolicy | None = None, event_sink=None) -> None:
        if not configuration.is_bpr:
            raise ValueError("Alicat BPR requires a back-pressure-controller role")
        self._configuration = configuration
        self._protocol = protocol
        self._pressure_policy = pressure_policy or PressurePolicy()
        self._status = DeviceStatus.DISCONNECTED
        self._state = None
        self.pressure_setpoint_pa = None
        self.valve_hold = None
        self._initialize_verification(event_sink)

    @property
    def device_id(self):
        return self._configuration.device_id

    @property
    def unit_address(self):
        return self._configuration.unit_address

    @property
    def status(self):
        return self._status

    @property
    def current_state(self):
        return self._state

    @property
    def pressure_policy(self):
        return self._pressure_policy

    @property
    def maximum_pressure_pa(self):
        maximum = min(self._configuration.maximum_pressure_bara * 100_000, self.pressure_policy.maximum_pa)
        observed = self.control_configuration
        if observed is not None and observed.loop_variable == 34 and observed.maximum_setpoint is not None:
            try:
                maximum = min(maximum, observed.maximum_setpoint * absolute_unit_factor(observed.setpoint_unit))
            except ValueError:
                pass  # Verification blocks writes; the UI can still show its configured ceiling.
        return maximum

    def connect(self):
        if self.status is not DeviceStatus.DISCONNECTED:
            raise RuntimeError("Alicat BPR is already connected")
        self._status = DeviceStatus.CONNECTING
        try:
            self._protocol.connect()
            self._status = DeviceStatus.READY
            self._inspect_control()
        except Exception:
            self._protocol.disconnect()
            self._status = DeviceStatus.FAULTED
            raise

    def disconnect(self):
        self._protocol.disconnect()
        self._status = DeviceStatus.DISCONNECTED
        self.control_ready = False
        self._state = None
        self.pressure_setpoint_pa = None
        self.valve_hold = None
        self.verification_message = "Disconnected; verification required"

    def _require_verified(self, *, allow_faulted=False):
        readable = {DeviceStatus.READY, DeviceStatus.FAULTED} if allow_faulted else {DeviceStatus.READY}
        if self.status not in readable:
            raise RuntimeError("Alicat BPR is not connected and ready")
        self.verify_control()
        observed = self.control_configuration
        # Bounds come from LR, or the read-only FPF sensor range on 9v firmware.
        if observed.maximum_setpoint is None or observed.minimum_setpoint is None:
            self.control_ready = False
            self.verification_message = "Control blocked: instrument pressure range could not be verified"
            raise RuntimeError(self.verification_message)
        return observed

    def _read_pressure_setpoint(self, observed):
        value, unit = self._protocol.read_setpoint(self.unit_address)
        if unit.casefold() != observed.setpoint_unit.casefold():
            self.control_ready = False
            raise RuntimeError("Setpoint units changed during verification")
        self.pressure_setpoint_pa = value * absolute_unit_factor(unit)
        return value

    def set_pressure_setpoint(self, value, unit="bara"):
        requested_pa = self.pressure_policy.to_absolute_pa(value, unit)
        self.pressure_policy.validate(requested_pa, self.maximum_pressure_pa)
        observed = self._require_verified()
        self.pressure_policy.validate(requested_pa, self.maximum_pressure_pa)
        factor = absolute_unit_factor(observed.setpoint_unit)
        with localcontext() as context:
            context.prec = 12
            context.rounding = ROUND_FLOOR
            native = float(Decimal(str(requested_pa)) / Decimal(str(factor)))
        self.pressure_policy.validate(native * factor, self.maximum_pressure_pa)
        if native < observed.minimum_setpoint:
            raise ValueError("Pressure is below the instrument's minimum setpoint")
        try:
            self._protocol.set_pressure_setpoint(self.unit_address, native)
            self.verify_control()
            confirmed = self._read_pressure_setpoint(self.control_configuration)
            if not isclose(confirmed, native, rel_tol=0, abs_tol=self._configuration.setpoint_tolerance):
                raise RuntimeError(f"Pressure setpoint not accepted: requested {native:g}, read back {confirmed:g}")
            self.pressure_policy.validate(self.pressure_setpoint_pa, self.maximum_pressure_pa)
        except Exception:
            self.control_ready = False
            self._status = DeviceStatus.FAULTED
            raise

    def read_measurements(self):
        observed = self._require_verified(allow_faulted=True)
        state = retry_read(lambda: self._protocol.read_state(self.unit_address), attempts=3, initial_delay_seconds=0.05)
        self._read_pressure_setpoint(observed)
        self._state = state
        self.valve_hold = "HLD" in state.status_codes
        readings = list(state_measurements(state, include_setpoint=False))
        # The commissioned frame explicitly describes absolute pressure units.
        pressure_pa = state.absolute_pressure * absolute_unit_factor(state.pressure_unit)
        readings = [replace(item, measurement=replace(item.measurement, value=pressure_pa / 100_000, unit="bara"))
                    if item.channel == "absolute_pressure" else item for item in readings]
        readings.append(DeviceMeasurement("pressure_setpoint_absolute", Measurement(
            self.pressure_setpoint_pa / 100_000, "bara", state.timestamp, state.quality)))
        readings.append(DeviceMeasurement("gauge_pressure", Measurement(
            pressure_pa / 100_000 - self.pressure_policy.atmospheric_reference_bara, "barg", state.timestamp, state.quality)))
        readings.append(DeviceMeasurement("valve_hold", Measurement(
            float(self.valve_hold), "", state.timestamp, state.quality)))
        return tuple(readings)

    def enter_safe_state(self):
        if self.status is DeviceStatus.DISCONNECTED:
            return
        # A close command remains appropriate after a mode mismatch, but never
        # send it to an unidentified/replaced instrument on the same address.
        observed = self._protocol.read_control_configuration(self.unit_address)
        if not self._configuration.expected_serial or observed.serial_number != self._configuration.expected_serial:
            raise RuntimeError("BPR shutdown blocked: serial identity mismatch")
        self._state = self._protocol.hold_closed(self.unit_address)
        self.valve_hold = True

    def resume_regulation(self):
        observed = self._require_verified()
        value = self._read_pressure_setpoint(observed)
        self.pressure_policy.validate(self.pressure_setpoint_pa, self.maximum_pressure_pa)
        if value < observed.minimum_setpoint:
            raise ValueError("Existing setpoint is below the instrument minimum")
        self._state = self._protocol.cancel_hold(self.unit_address)
        self.valve_hold = False
