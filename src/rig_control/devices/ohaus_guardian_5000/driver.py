from threading import RLock

from rig_control.devices.base import Device
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.devices.ohaus_guardian_5000.configuration import (
    MODEL_SPECS, GuardianLimits, ModelSpec, finite_number, normalize_model,
)
from rig_control.devices.ohaus_guardian_5000.protocol import (
    GuardianIdentity, GuardianProtocol, GuardianProtocolError, OperatingMode,
    parse_temperatures, parse_timer,
)
from rig_control.models import DeviceStatus, Measurement
from rig_control.transports.serial_text import SerialTextTransport


class OhausGuardian5000(Device, MeasurementSource):
    """One G52 instrument, with capabilities selected only after MODEL discovery."""

    def __init__(self, device_id: str, transport: SerialTextTransport,
                 limits: GuardianLimits | None = None) -> None:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("Device ID cannot be empty")
        if limits is not None and not isinstance(limits, GuardianLimits):
            raise TypeError("Rig limits must be GuardianLimits")
        self._device_id = device_id
        self._transport = transport
        self._protocol = GuardianProtocol(transport)
        self._limits = limits or GuardianLimits()
        self._run_limits = GuardianLimits()
        self._lock = RLock()
        self._status = DeviceStatus.DISCONNECTED
        self._clear_cache()

    def _clear_cache(self) -> None:
        self._identity: GuardianIdentity | None = None
        self._spec: ModelSpec | None = None
        self._mode: OperatingMode | None = None
        self._target_temperature: float | None = None
        self._target_speed: float | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def identity(self) -> GuardianIdentity | None:
        return self._identity

    @property
    def model_spec(self) -> ModelSpec | None:
        return self._spec

    @property
    def limits(self) -> GuardianLimits:
        return self._limits

    @property
    def run_limits(self) -> GuardianLimits:
        return self._run_limits

    @property
    def target_temperature(self) -> float | None:
        return self._target_temperature

    @property
    def target_speed(self) -> float | None:
        return self._target_speed

    @property
    def operating_mode(self) -> OperatingMode | None:
        return self._mode

    @property
    def heating_enabled(self) -> bool | None:
        if self._mode is None or self._mode is OperatingMode.ERROR:
            return None
        return self._mode in {1, 2, 4, 5}

    @property
    def stirring_enabled(self) -> bool | None:
        if self._mode is None or self._mode is OperatingMode.ERROR:
            return None
        return self._mode in {3, 4, 5}

    @property
    def capabilities(self) -> frozenset[str]:
        if self._spec is None:
            return frozenset()
        return frozenset(name for name in ("heating", "stirring")
                         if getattr(self._spec, name))

    def connect(self) -> None:
        with self._lock:
            if self._transport.is_open:
                raise RuntimeError("Guardian is already connected")
            self._clear_cache()
            self._status = DeviceStatus.CONNECTING
            try:
                self._transport.open()
                model = self._protocol.query("MODEL")
                self._spec = MODEL_SPECS[normalize_model(model)]
                self._identity = GuardianIdentity(
                    model, self._protocol.query("SERIAL"), self._protocol.query("VERSION"),
                )
                self.refresh_state()
            except Exception:
                try:
                    self._transport.close()
                finally:
                    self._clear_cache()
                    self._status = DeviceStatus.DISCONNECTED
                raise

    def disconnect(self) -> None:
        with self._lock:
            try:
                if self._transport.is_open and self._spec is not None:
                    self.enter_safe_state()
            finally:
                try:
                    self._transport.close()
                finally:
                    self._clear_cache()
                    self._status = DeviceStatus.DISCONNECTED

    def _require_connected(self, capability: str | None = None) -> ModelSpec:
        if not self._transport.is_open or self._spec is None:
            raise RuntimeError("Guardian is not connected")
        if capability is not None and not getattr(self._spec, capability):
            raise ValueError(f"Detected Guardian model does not support {capability}")
        return self._spec

    def _validate(self, value: float, capability: str) -> float:
        spec = self._require_connected(capability)
        value = finite_number(value, capability)
        if capability == "heating":
            maximum = spec.maximum_temperature
            minimum = 0
            ceilings = (self._limits.maximum_temperature, self._run_limits.maximum_temperature)
            if value * 2 != int(value * 2):
                raise ValueError("Temperature setpoints use 0.5 degC increments")
        else:
            minimum, maximum = spec.minimum_speed, spec.maximum_speed
            ceilings = (self._limits.maximum_speed, self._run_limits.maximum_speed)
            if value != int(value):
                raise ValueError("Stir speed must be a whole number of rpm")
        if not minimum <= value <= maximum:
            raise ValueError(f"{capability} setpoint exceeds hardware range {minimum}..{maximum}")
        if any(limit is not None and value > limit for limit in ceilings):
            raise ValueError(f"{capability} setpoint exceeds rig or run limit")
        return value

    def refresh_state(self) -> OperatingMode:
        with self._lock:
            spec = self._require_connected()
            try:
                mode = self._protocol.mode()
                temperature = self._protocol.number("TARGET_TEMPERATURE") if spec.heating else None
                speed = self._protocol.number("TARGET_SPEED") if spec.stirring else None
                if (not spec.heating and mode in {1, 2, 4, 5}
                        or not spec.stirring and mode in {3, 4, 5}):
                    raise GuardianProtocolError("Operating mode conflicts with model capabilities")
                # Preserve actual readbacks, including out-of-policy settings;
                # never silently change an already-running instrument on connect.
                self._mode = mode
                self._target_temperature, self._target_speed = temperature, speed
                if temperature is not None:
                    self._validate(temperature, "heating")
                if speed is not None:
                    self._validate(speed, "stirring")
                self._status = DeviceStatus.FAULTED if mode is OperatingMode.ERROR else DeviceStatus.READY
                return mode
            except Exception:
                self._mode = None
                self._target_temperature = None
                self._target_speed = None
                self._status = DeviceStatus.FAULTED
                raise

    def set_run_limits(self, limits: GuardianLimits) -> None:
        if not isinstance(limits, GuardianLimits):
            raise TypeError("Run limits must be GuardianLimits")
        with self._lock:
            previous = self._run_limits
            self._run_limits = limits
            try:
                if self._transport.is_open:
                    self.refresh_state()
            except Exception:
                self._run_limits = previous
                raise

    def _set_target(self, command: str, value: float, capability: str) -> None:
        value = self._validate(value, capability)
        if self._status is not DeviceStatus.READY:
            raise RuntimeError("Guardian must have a successful state refresh before setting targets")
        try:
            self._protocol.write(command, f"{value:g}")
            actual = self._protocol.number(command)
            self._validate(actual, capability)
            if actual != value:
                raise GuardianProtocolError(f"{command} readback {actual} differs from requested {value}")
        except Exception:
            if capability == "heating":
                self._target_temperature = None
            else:
                self._target_speed = None
            self._status = DeviceStatus.FAULTED
            raise
        if capability == "heating":
            self._target_temperature = actual
        else:
            self._target_speed = actual

    def set_target_temperature(self, temperature: float) -> None:
        with self._lock:
            self._set_target("TARGET_TEMPERATURE", temperature, "heating")

    def set_target_speed(self, rpm: float) -> None:
        with self._lock:
            self._set_target("TARGET_SPEED", rpm, "stirring")

    def _switch(self, capability: str, enabled: bool) -> None:
        with self._lock:
            self._require_connected(capability)
            if enabled:
                self.refresh_state()  # Check front-panel changes against all limits.
                if self._mode is OperatingMode.ERROR:
                    raise RuntimeError("Guardian reports an instrument error")
            command = ("START_" if enabled else "STOP_") + ("HEAT" if capability == "heating" else "STIR")
            try:
                self._protocol.write(command)
                self._mode = self._protocol.mode()
                actual = self.heating_enabled if capability == "heating" else self.stirring_enabled
                if actual is not enabled:
                    raise GuardianProtocolError(f"{command} state was not confirmed")
            except Exception:
                self._mode = None
                self._status = DeviceStatus.FAULTED
                raise

    def start_heating(self) -> None:
        self._switch("heating", True)

    def stop_heating(self) -> None:
        self._switch("heating", False)

    def start_stirring(self) -> None:
        self._switch("stirring", True)

    def stop_stirring(self) -> None:
        self._switch("stirring", False)

    def enter_safe_state(self) -> None:
        with self._lock:
            spec = self._require_connected()
            errors = []
            for supported, stop in ((spec.heating, self.stop_heating), (spec.stirring, self.stop_stirring)):
                if supported:
                    try:
                        stop()
                    except Exception as error:
                        errors.append(error)
            if errors:
                raise ExceptionGroup("Guardian safe state could not be confirmed", errors)

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        with self._lock:
            spec = self._require_connected()
            try:
                self.refresh_state()
                if self._mode is OperatingMode.ERROR:
                    raise RuntimeError("Guardian reports an instrument error")
                readings = []
                if spec.heating:
                    plate, probe = parse_temperatures(self._protocol.query("MEASURED_TEMPERATURE"))
                    if self._mode in {2, 5} and probe is None:
                        raise GuardianProtocolError("Probe mode did not return a probe temperature")
                    readings.append(DeviceMeasurement("temperature", Measurement(plate, "degC")))
                    if probe is not None:
                        readings.append(DeviceMeasurement("probe_temperature", Measurement(probe, "degC")))
                if spec.stirring:
                    readings.append(DeviceMeasurement("stir_speed", Measurement(self._protocol.number("MEASURED_SPEED"), "rpm")))
                return tuple(readings)
            except Exception:
                self._status = DeviceStatus.FAULTED
                raise

    def read_timer(self) -> int:
        """Return current timer in seconds."""
        with self._lock:
            self._require_connected()
            return parse_timer(self._protocol.query("TIMER"))

    def set_timer(self, seconds: int) -> None:
        # Manual: 1 minute to 99 hours 59 minutes. Do not invent a zero=off write.
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise TypeError("Timer must be an integer number of seconds")
        if not 60 <= seconds <= 359940 or seconds % 60:
            raise ValueError("Timer must be whole minutes from 1 to 5999")
        with self._lock:
            self._require_connected()
            self._protocol.write("TIMER", f"{seconds // 3600:02}:{seconds // 60 % 60:02}:00")

    def reset_timer(self) -> None:
        with self._lock:
            self._require_connected()
            self._protocol.write("TIMER_RESET")

    def read_error_code(self) -> str:
        with self._lock:
            self._require_connected()
            code = self._protocol.error_code()
            if code != "0":
                self._status = DeviceStatus.FAULTED
            return code
