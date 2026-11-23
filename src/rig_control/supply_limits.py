"""Everyday limits on what a power supply may be asked for (see decisions/0028).

Every current or voltage written to a supply, by anyone (manual control, a
recipe, an override), must pass every limit that applies:

- the instrument's own maximum (rig profile);
- the cable ceiling: 45 A, or the High current mode ceiling (decisions/0014);
- the everyday maximum current and voltage (Settings): the most this rig is
  normally used at, so a slip such as 25 typed for 25 mA is refused;
- the maximum current density (Settings) times the cell's active area, once
  an area is entered.

The current and density limits are both applied, so a wrong area can only
ever tighten the protection, never loosen it.  A supply's current and voltage
registers swap roles between constant current and constant voltage, so the
limits are on the registers: whatever the setting is called in that mode.

The everyday values are also the default compliance limits: the voltage
limit in constant current and the current limit in constant voltage.  A run
may set a lower one, never a higher one.

Commands from the safety system are never limited: they only switch off.
"""
from collections.abc import Callable
from dataclasses import dataclass

from rig_control.app_settings import (
    DEFAULT_MAXIMUM_CURRENT_AMPS, DEFAULT_MAXIMUM_CURRENT_DENSITY, DEFAULT_MAXIMUM_VOLTAGE_VOLTS,
    STANDARD_CABLE_CEILING_AMPS,
)
from rig_control.control.commands import CommandSource, SetPowerSupplyCurrentLimit, SetPowerSupplyVoltage
_MA_PER_A = 1000.0


@dataclass(frozen=True, slots=True)
class SupplyLimits:
    maximum_current_amps: float = DEFAULT_MAXIMUM_CURRENT_AMPS
    maximum_voltage_volts: float = DEFAULT_MAXIMUM_VOLTAGE_VOLTS
    #: mA/cm²; applied as a current once an area is known.
    maximum_current_density: float = DEFAULT_MAXIMUM_CURRENT_DENSITY
    #: The cable ceiling in force: 45 A, or the High current mode ceiling.
    cable_ceiling_amps: float = STANDARD_CABLE_CEILING_AMPS

    def __post_init__(self) -> None:
        for name in ("maximum_current_amps", "maximum_voltage_volts", "maximum_current_density", "cable_ceiling_amps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
                raise ValueError(f"Supply limit {name} must be greater than zero")

    def current_ceiling(self, area_cm2: float | None, instrument_amps: float | None = None) -> tuple[float, str]:
        """The highest current allowed now, and which limit sets it, in words."""
        limits = [(self.maximum_current_amps, f"the everyday maximum of {self.maximum_current_amps:g} A")]
        if area_cm2 is not None:
            amps = self.maximum_current_density * area_cm2 / _MA_PER_A
            limits.append((amps, f"{self.maximum_current_density:g} mA/cm² × {area_cm2:g} cm² = {amps:g} A"))
        limits.append((self.cable_ceiling_amps, f"the cable ceiling of {self.cable_ceiling_amps:g} A"))
        if instrument_amps is not None:
            limits.append((instrument_amps, f"the instrument's maximum of {instrument_amps:g} A"))
        return min(limits, key=lambda item: item[0])

    def voltage_ceiling(self, instrument_volts: float | None = None) -> tuple[float, str]:
        limits = [(self.maximum_voltage_volts, f"the everyday maximum of {self.maximum_voltage_volts:g} V")]
        if instrument_volts is not None:
            limits.append((instrument_volts, f"the instrument's maximum of {instrument_volts:g} V"))
        return min(limits, key=lambda item: item[0])

    def default_current_limit(self, area_cm2: float | None, instrument_amps: float | None = None) -> float:
        """The current limit a supply gets on entering constant voltage."""
        return self.current_ceiling(area_cm2, instrument_amps)[0]

    def default_voltage_limit(self, instrument_volts: float | None = None) -> float:
        """The voltage limit a supply gets on entering constant current."""
        return self.voltage_ceiling(instrument_volts)[0]

    def problem(self, command: object, area_cm2: float | None,
                instrument: tuple[float | None, float | None] = (None, None)) -> str | None:
        """Why a command would break a limit, or None.  ``instrument`` is its (amps, volts) maximum."""
        if getattr(command, "source", None) is CommandSource.SAFETY_SYSTEM:
            return None
        if isinstance(command, SetPowerSupplyCurrentLimit):
            ceiling, reason = self.current_ceiling(area_cm2, instrument[0])
            if command.current > ceiling + 1e-12:
                density = (f" ({command.current * _MA_PER_A / area_cm2:g} mA/cm² at {area_cm2:g} cm²)"
                           if area_cm2 else "")
                return (f"{_amps_text(command.current)}{density} for {command.device_id!r} is above {reason}. "
                        "Limits are set under Settings › Power supply.")
        elif isinstance(command, SetPowerSupplyVoltage):
            ceiling, reason = self.voltage_ceiling(instrument[1])
            if command.voltage > ceiling + 1e-12:
                return (f"{command.voltage:g} V for {command.device_id!r} is above {reason}. "
                        "Limits are set under Settings › Power supply.")
        return None


def _amps_text(amps: float) -> str:
    return f"{amps:g} A" if amps >= 1 else f"{amps:g} A ({amps * _MA_PER_A:g} mA)"


class SupplyLimitGuard:
    """Applies ``SupplyLimits`` to commands, with the area and instruments as they are now."""

    def __init__(self, limits: SupplyLimits, area_cm2: Callable[[], float | None],
                 instrument: Callable[[str], tuple[float | None, float | None]]) -> None:
        self.limits, self._area, self._instrument = limits, area_cm2, instrument

    def __call__(self, command: object) -> str | None:
        if not isinstance(command, (SetPowerSupplyCurrentLimit, SetPowerSupplyVoltage)):
            return None
        try:
            instrument = self._instrument(command.device_id)
        except Exception:
            instrument = (None, None)
        return self.limits.problem(command, self._area(), instrument)

    @property
    def area_cm2(self) -> float | None:
        return self._area()

    def instrument(self, device_id: str) -> tuple[float | None, float | None]:
        try:
            return self._instrument(device_id)
        except Exception:
            return (None, None)


def instrument_maximums(device_manager) -> Callable[[str], tuple[float | None, float | None]]:
    def lookup(device_id: str) -> tuple[float | None, float | None]:
        limits = getattr(device_manager.get(device_id), "limits", None)
        return (getattr(limits, "maximum_current", None), getattr(limits, "maximum_voltage", None))
    return lookup
