from rig_control.devices.keithley_2260b.protocol import (
    Keithley2260BProtocol,
    KeithleyIdentity,
)
from rig_control.devices.power_supply import PowerSupply, PowerSupplyLimits
from rig_control.models import DeviceStatus, Measurement
from rig_control.transports.scpi import ScpiTransport


class Keithley2260B(PowerSupply):
    """Driver for a Keithley 2260B programmable DC power supply."""

    def __init__(
        self,
        device_id: str,
        limits: PowerSupplyLimits,
        transport: ScpiTransport,
    ) -> None:
        self._device_id = device_id
        self._limits = limits
        self._transport = transport
        self._status = DeviceStatus.DISCONNECTED
        self._identity: KeithleyIdentity | None = None
        self._voltage_setpoint = 0.0
        self._current_limit = 0.0
        self._output_enabled = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def limits(self) -> PowerSupplyLimits:
        return self._limits

    @property
    def identity(self) -> KeithleyIdentity | None:
        """Return the identity reported by the connected instrument."""

        return self._identity

    @property
    def voltage_setpoint(self) -> float:
        return self._voltage_setpoint

    @property
    def current_limit(self) -> float:
        return self._current_limit

    @property
    def output_enabled(self) -> bool:
        return self._output_enabled

    def connect(self) -> None:
        """Connect and read the instrument's actual current state."""

        if self._transport.is_open:
            raise RuntimeError(
                f"Power supply {self.device_id!r} is already connected"
            )

        try:
            self._transport.open()

            identity_response = self._transport.query(
                Keithley2260BProtocol.IDENTIFY_QUERY
            )
            identity = Keithley2260BProtocol.parse_identity(
                identity_response
            )

            if "2260B" not in identity.model.upper():
                raise RuntimeError(
                    "Connected instrument is not a Keithley 2260B: "
                    f"manufacturer={identity.manufacturer!r}, "
                    f"model={identity.model!r}, "
                    f"serial_number={identity.serial_number!r}"
                )

            voltage_response = self._transport.query(
                Keithley2260BProtocol.VOLTAGE_SETPOINT_QUERY
            )
            current_response = self._transport.query(
                Keithley2260BProtocol.CURRENT_LIMIT_QUERY
            )
            output_response = self._transport.query(
                Keithley2260BProtocol.OUTPUT_STATE_QUERY
            )

            voltage = Keithley2260BProtocol.parse_number(
                voltage_response
            )
            current = Keithley2260BProtocol.parse_number(
                current_response
            )
            output_enabled = Keithley2260BProtocol.parse_boolean(
                output_response
            )

            self._validate_operating_point(voltage, current)

            self._identity = identity
            self._voltage_setpoint = voltage
            self._current_limit = current
            self._output_enabled = output_enabled
            self._status = DeviceStatus.READY

        except Exception:
            self._transport.close()
            self._identity = None
            self._status = DeviceStatus.DISCONNECTED
            raise

    def disconnect(self) -> None:
        """Request a safe state and close the connection."""

        if not self._transport.is_open:
            self._clear_local_state()
            return

        try:
            self.enter_safe_state()
        finally:
            self._transport.close()
            self._clear_local_state()

    def set_voltage(self, voltage: float) -> None:
        self._require_ready()
        self._validate_operating_point(voltage, self._current_limit)

        command = Keithley2260BProtocol.set_voltage(voltage)
        self._transport.write(command)
        self._voltage_setpoint = float(voltage)

    def set_current_limit(self, current: float) -> None:
        self._require_ready()
        self._validate_operating_point(self._voltage_setpoint, current)

        command = Keithley2260BProtocol.set_current_limit(current)
        self._transport.write(command)
        self._current_limit = float(current)

    def set_output_enabled(self, enabled: bool) -> None:
        self._require_ready()

        command = Keithley2260BProtocol.set_output_enabled(enabled)
        self._transport.write(command)
        self._output_enabled = enabled

    def measure_voltage(self) -> Measurement:
        self._require_ready()

        response = self._transport.query(
            Keithley2260BProtocol.MEASURE_VOLTAGE_QUERY
        )
        value = Keithley2260BProtocol.parse_number(response)

        return Measurement(value, "V")

    def measure_current(self) -> Measurement:
        self._require_ready()

        response = self._transport.query(
            Keithley2260BProtocol.MEASURE_CURRENT_QUERY
        )
        value = Keithley2260BProtocol.parse_number(response)

        return Measurement(value, "A")

    def enter_safe_state(self) -> None:
        """Disable output and reduce both setpoints to zero."""

        if not self._transport.is_open:
            self._voltage_setpoint = 0.0
            self._current_limit = 0.0
            self._output_enabled = False
            return

        # Output is disabled first so that clearing the setpoints cannot
        # unexpectedly affect an energised experiment.
        self._transport.write(
            Keithley2260BProtocol.set_output_enabled(False)
        )
        self._output_enabled = False

        self._transport.write(
            Keithley2260BProtocol.set_voltage(0.0)
        )
        self._voltage_setpoint = 0.0

        self._transport.write(
            Keithley2260BProtocol.set_current_limit(0.0)
        )
        self._current_limit = 0.0

    def _require_ready(self) -> None:
        if (
            self.status is not DeviceStatus.READY
            or not self._transport.is_open
        ):
            raise RuntimeError(
                f"Power supply {self.device_id!r} is not ready"
            )

    def _clear_local_state(self) -> None:
        self._identity = None
        self._voltage_setpoint = 0.0
        self._current_limit = 0.0
        self._output_enabled = False
        self._status = DeviceStatus.DISCONNECTED

    def _validate_operating_point(
        self,
        voltage: float,
        current: float,
    ) -> None:
        if voltage < 0:
            raise ValueError("Voltage cannot be negative")

        if current < 0:
            raise ValueError("Current cannot be negative")

        if voltage > self.limits.maximum_voltage:
            raise ValueError(
                f"Voltage {voltage} V exceeds configured maximum "
                f"{self.limits.maximum_voltage} V"
            )

        if current > self.limits.maximum_current:
            raise ValueError(
                f"Current {current} A exceeds configured maximum "
                f"{self.limits.maximum_current} A"
            )

        requested_power = voltage * current

        if requested_power > self.limits.maximum_power:
            raise ValueError(
                f"Operating point could permit {requested_power} W, "
                f"exceeding configured maximum "
                f"{self.limits.maximum_power} W"
            )