from dataclasses import dataclass
from typing import Final

from rig_control.devices.keithley_2260b.driver import Keithley2260B
from rig_control.devices.keithley_2280s.configuration import _validate_rig_limits
from rig_control.devices.keithley_2280s.protocol import (
    Keithley2280SProtocol,
    KeithleyIdentity,
)
from rig_control.models import Measurement


@dataclass(frozen=True, slots=True)
class HardwareLimits:
    """Rated model capabilities, independent of profile and run limits."""

    maximum_voltage: float
    maximum_current: float
    maximum_power: float


HARDWARE_LIMITS: Final = HardwareLimits(32.0, 6.0, 192.0)


class Keithley2280S(Keithley2260B):
    """2280S-32-6 using the 2260B lifecycle, caching and read retries.

    Power validation uses voltage setpoint times current limit, bounding the
    possible output power rather than relying on a measured load current.
    As with the 2260B, this assumes exclusive control of instrument settings.
    """

    _protocol = Keithley2280SProtocol
    _model_name = "Keithley 2280S-32-6"

    @property
    def hardware_limits(self) -> HardwareLimits:
        return HARDWARE_LIMITS

    def _accepts_identity(self, identity: KeithleyIdentity) -> bool:
        model = identity.model.upper().removeprefix("MODEL ").strip()
        return model == "2280S-32-6" and "KEITHLEY" in identity.manufacturer.upper()

    def _configure_instrument(self) -> None:
        # Default readings include units and other fields. Keep the shared
        # numeric parser by explicitly selecting only the reading element.
        self._transport.write(self._protocol.MEASUREMENT_FORMAT)
        # A saved falling delay must not defer the safe-state output-off.
        self._transport.write(self._protocol.DISABLE_OUTPUT_DELAY)

    def _validate_operating_point(self, voltage: float, current: float) -> None:
        _validate_rig_limits(self.limits)
        self._protocol._format_number(voltage)
        self._protocol._format_number(current)
        super()._validate_operating_point(voltage, current)
        for name, value, unit, configured, rated in (
            ("Voltage", voltage, "V", self.limits.maximum_voltage,
             self.hardware_limits.maximum_voltage),
            ("Current", current, "A", self.limits.maximum_current,
             self.hardware_limits.maximum_current),
            ("Power", voltage * current, "W", self.limits.maximum_power,
             self.hardware_limits.maximum_power),
        ):
            if value > rated:
                raise ValueError(
                    f"{name} {value} {unit} exceeds hardware maximum {rated} {unit}"
                )
            if value > configured:
                raise ValueError(
                    f"{name} {value} {unit} exceeds configured maximum "
                    f"{configured} {unit}"
                )

    def set_output_enabled(self, enabled: bool) -> None:
        self._require_ready()
        if enabled:
            self._validate_operating_point(self.voltage_setpoint, self.current_limit)
        super().set_output_enabled(enabled)

    def _require_measurement_ready(self) -> None:
        self._require_ready()
        if not self.output_enabled:
            raise RuntimeError("Keithley 2280S measurements require output enabled")

    def measure_voltage(self) -> Measurement:
        self._require_measurement_ready()
        return super().measure_voltage()

    def measure_current(self) -> Measurement:
        self._require_measurement_ready()
        return super().measure_current()
