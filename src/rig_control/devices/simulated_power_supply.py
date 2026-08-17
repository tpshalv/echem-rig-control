from rig_control.devices.power_supply import PowerSupply, PowerSupplyLimits
from rig_control.models import DeviceStatus, Measurement


class SimulatedPowerSupply(PowerSupply):
    """Safe software-only model of a programmable DC power supply."""

    def __init__(
        self,
        device_id: str,
        limits: PowerSupplyLimits,
    ) -> None:
        self._device_id = device_id
        self._limits = limits
        self._status = DeviceStatus.DISCONNECTED
        self._voltage_setpoint = 0.0
        self._current_limit = 0.0
        self._output_enabled = False
        self._measured_voltage = 0.0
        self._measured_current = 0.0

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
    def voltage_setpoint(self) -> float:
        return self._voltage_setpoint

    @property
    def current_limit(self) -> float:
        return self._current_limit

    @property
    def output_enabled(self) -> bool:
        return self._output_enabled

    def connect(self) -> None:
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        self.enter_safe_state()
        self._status = DeviceStatus.DISCONNECTED

    def set_voltage(self, voltage: float) -> None:
        self._require_ready()
        self._validate_operating_point(voltage, self._current_limit)
        self._voltage_setpoint = voltage

    def set_current_limit(self, current: float) -> None:
        self._require_ready()
        self._validate_operating_point(self._voltage_setpoint, current)
        self._current_limit = current

    def set_output_enabled(self, enabled: bool) -> None:
        self._require_ready()

        if not isinstance(enabled, bool):
            raise TypeError("Output enabled state must be a Boolean")

        self._output_enabled = enabled

        if not enabled:
            self._measured_voltage = 0.0
            self._measured_current = 0.0

    def measure_voltage(self) -> Measurement:
        self._require_ready()
        return Measurement(self._measured_voltage, "V")

    def measure_current(self) -> Measurement:
        self._require_ready()
        return Measurement(self._measured_current, "A")

    def set_simulated_measurement(
        self,
        *,
        voltage: float,
        current: float,
    ) -> None:
        """Set the values returned by simulated measurement methods."""

        self._require_ready()
        self._measured_voltage = voltage
        self._measured_current = current

    def enter_safe_state(self) -> None:
        """Disable output and clear setpoints."""

        self._output_enabled = False
        self._voltage_setpoint = 0.0
        self._current_limit = 0.0
        self._measured_voltage = 0.0
        self._measured_current = 0.0

    def _require_ready(self) -> None:
        if self.status is not DeviceStatus.READY:
            raise RuntimeError(
                f"Power supply {self.device_id!r} is not ready"
            )

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