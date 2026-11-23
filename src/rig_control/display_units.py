"""Show and accept measurements in a chosen unit, without changing control.

Instruments keep working in their own units: an Alicat configured in SLPM is
commanded in SLPM, and a pressure controller in PSIA is commanded in PSIA.
Supplies are commanded in amperes and densities are stated in mA/cm².  This
layer only converts at the edges — what a screen or an export shows, and
what a typed value means before it becomes a command.

There is one display unit per quantity, set once in Settings and used on
every screen, so a number is never shown in mA in one place and A in
another.  A unit typed after a number always wins over the box's own
(``250 mA``, ``0.5 slpm``, ``14.7 psia``).

A conversion is refused rather than guessed. An unknown unit is shown as the
instrument reported it, and a typed value in a unit that cannot be converted
— or that belongs to another quantity — never reaches a device.  Bare
``psi`` and ``bar`` are refused when typed, because they could mean gauge.

The recorded log keeps the instrument's own units, so a preference changed
mid-experiment cannot alter the meaning of anything already written.
"""

from dataclasses import dataclass
from enum import StrEnum
import re

from rig_control.devices.mass_flow_controller import mass_flow_unit_factor
from rig_control.devices.pressure_controller import absolute_unit_factor

#: Selectable on the settings screen. "native" leaves the instrument's own
#: unit alone. Absolute pressure only: this software controls absolute-pressure
#: loops, and offering a gauge entry here would reintroduce the ambiguity that
#: the BPR work deliberately removed.
NATIVE = "native"
PRESSURE_UNITS = (NATIVE, "bara", "psia", "kpaa", "mbara")
FLOW_UNITS = (NATIVE, "SCCM", "SLPM")
#: Currents are always stored and commanded in A; mA is display and entry only.
CURRENT_UNITS = ("mA", "A")
#: Densities are stated in mA/cm² everywhere inside; A/cm² is display and entry only.
DENSITY_UNITS = ("mA/cm²", "A/cm²")

#: How many amperes, and how many mA/cm², one of each unit is.
_CURRENT_FACTORS = {"a": 1.0, "ma": 0.001}
_DENSITY_FACTORS = {"ma/cm²": 1.0, "ma/cm2": 1.0, "a/cm²": 1000.0, "a/cm2": 1000.0}
#: Typed pressure units must say absolute.
_TYPED_PRESSURE = {"bara", "psia", "kpaa", "mbara", "paa"}


#: Which recorded channels are which quantity, for the screens, graphs and exports
#: alike.  Volumetric flow and the totaliser are different quantities and are
#: left alone, as are differential pressures.
ABSOLUTE_PRESSURE_CHANNELS = frozenset({"absolute_pressure", "pressure_setpoint_absolute"})
MASS_FLOW_CHANNELS = frozenset({"mass_flow", "setpoint", "excess_flow"})
CURRENT_CHANNELS = frozenset({"current", "current_limit"})
#: rig_control.calibrated_channels.CURRENT_DENSITY_CHANNEL (not imported: drivers only here).
DENSITY_CHANNEL = "current_density"
#: The recording metadata entry that fixes a run's export units when it starts.
EXPORT_UNITS_KEY = "export_units"


class Quantity(StrEnum):
    PRESSURE = "pressure"
    FLOW = "flow"
    CURRENT = "current"
    DENSITY = "current density"


def _factor(quantity: Quantity, unit: str) -> float:
    """How many of the quantity's base unit (Pa, SCCM, A, mA/cm²) one ``unit`` is."""
    key = unit.strip().casefold()
    if quantity is Quantity.PRESSURE:
        return absolute_unit_factor(key)
    if quantity is Quantity.FLOW:
        return mass_flow_unit_factor(key)
    table = _CURRENT_FACTORS if quantity is Quantity.CURRENT else _DENSITY_FACTORS
    try:
        return table[key]
    except KeyError:
        raise ValueError(f"Unsupported {quantity.value} unit {unit!r}") from None


def reading_quantity(channel: str, unit: str) -> "Quantity | None":
    """The quantity a recorded channel is, if it is one shown in a chosen unit."""
    if channel == DENSITY_CHANNEL:
        return Quantity.DENSITY
    if channel in CURRENT_CHANNELS and unit == "A":
        return Quantity.CURRENT
    if channel in ABSOLUTE_PRESSURE_CHANNELS:
        return Quantity.PRESSURE
    if channel in MASS_FLOW_CHANNELS:
        return Quantity.FLOW
    return None


def convert(value: float, from_unit: str, to_unit: str, quantity: Quantity) -> float:
    """Convert between two known units of one quantity; raises for anything else."""
    if from_unit.strip().casefold() == to_unit.strip().casefold():
        return float(value)
    return _clean(float(value) * _factor(quantity, from_unit) / _factor(quantity, to_unit))


def _clean(value: float) -> float:
    """Drop float noise, e.g. 0.07 * 1000 = 70.00000000000001."""
    return float(f"{value:.12g}")


def _converted(value: float, from_unit: str, to_unit: str, quantity: Quantity) -> tuple[float, str]:
    if to_unit == NATIVE or not from_unit:
        return value, from_unit
    if from_unit.strip().casefold() == to_unit.strip().casefold():
        return value, to_unit
    try:
        return convert(value, from_unit, to_unit, quantity), to_unit
    except ValueError:
        # One of the units is not one we can convert. Showing the
        # instrument's own number and unit is always truthful.
        return value, from_unit


_TYPED = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$")


def split_typed(text: str) -> tuple[float, str]:
    """``"250 mA"`` -> ``(250.0, "mA")``; the unit is empty when none was typed."""
    match = _TYPED.match(text)
    if match is None:
        raise ValueError(f"{text.strip()!r} is not a number (optionally followed by a unit)")
    return float(match.group(1)), match.group(2)


@dataclass(frozen=True, slots=True)
class DisplayUnits:
    """The units every screen and export shows and accepts, per quantity."""

    pressure: str = "psia"
    flow: str = "SCCM"
    current: str = "mA"
    density: str = "mA/cm²"

    def __post_init__(self) -> None:
        for name, allowed in (("pressure", PRESSURE_UNITS), ("flow", FLOW_UNITS), ("current", CURRENT_UNITS),
                              ("density", DENSITY_UNITS)):
            value = getattr(self, name)
            if not isinstance(value, str) or _canonical(value, allowed) not in allowed:
                raise ValueError(
                    f"Display {name} unit must be one of: {', '.join(allowed)}"
                )
            object.__setattr__(self, name, _canonical(value, allowed))

    def unit_for(self, quantity: Quantity) -> str:
        return {Quantity.PRESSURE: self.pressure, Quantity.FLOW: self.flow, Quantity.CURRENT: self.current,
                Quantity.DENSITY: self.density}[quantity]

    def show(self, value: float, unit: str, quantity: Quantity) -> tuple[float, str]:
        """Convert one value for display; a unit that can't be converted is shown as it is."""
        return _converted(value, unit, self.unit_for(quantity), quantity)

    def show_reading(self, channel: str, value, unit: str) -> tuple[object, str]:
        """One recorded reading in the display unit for its channel; others unchanged."""
        quantity = reading_quantity(channel, unit)
        if quantity is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, unit
        return self.show(float(value), unit, quantity)

    def as_dict(self) -> dict[str, str]:
        return {"pressure": self.pressure, "flow": self.flow, "current": self.current, "density": self.density}

    def show_pressure(self, value: float, unit: str) -> tuple[float, str]:
        """Convert one absolute pressure for display."""

        return self.show(value, unit, Quantity.PRESSURE)

    def show_flow(self, value: float, unit: str) -> tuple[float, str]:
        """Convert one mass-flow rate for display."""

        return self.show(value, unit, Quantity.FLOW)

    def show_current(self, amps: float) -> tuple[float, str]:
        return self.show(amps, "A", Quantity.CURRENT)

    def show_density(self, ma_per_cm2: float) -> tuple[float, str]:
        return self.show(ma_per_cm2, "mA/cm²", Quantity.DENSITY)

    def entry_pressure_unit(self, native_unit: str) -> str:
        """Return the unit a typed pressure is in, given the instrument's own.

        The value is sent with this unit attached, so the number and its unit
        can never be separated on the way to a device.
        """

        return self.show_pressure(1.0, native_unit)[1]

    def entry_flow_unit(self, native_unit: str) -> str:
        return self.show_flow(1.0, native_unit)[1]

    def read(self, text: str, quantity: Quantity, to_unit: str, *, native_unit: str = "") -> float:
        """A typed value, in ``to_unit``.

        A unit typed after the number wins; otherwise the number is in this
        quantity's display unit (or ``native_unit`` when the display unit is
        "native").  A unit of another quantity, or one that could mean gauge
        pressure, is refused.
        """
        value, typed = split_typed(text)
        if typed:
            unit = self._typed_unit(typed, quantity)
        else:
            unit = self.unit_for(quantity)
            if unit == NATIVE:
                unit = native_unit or to_unit
        return convert(value, unit, to_unit, quantity)

    @staticmethod
    def _typed_unit(typed: str, quantity: Quantity) -> str:
        key = typed.strip().casefold()
        if quantity is Quantity.PRESSURE and key not in _TYPED_PRESSURE:
            if key in {"psi", "bar", "kpa", "mbar", "pa", "psig", "barg"}:
                raise ValueError(f"Write an absolute pressure unit (psia, bara, kPaa or mbara), not {typed!r}")
            raise ValueError(f"{typed!r} is not a pressure unit; use psia, bara, kPaa or mbara")
        try:
            _factor(quantity, key)
        except ValueError:
            accepted = {Quantity.FLOW: "SCCM or SLPM", Quantity.CURRENT: "mA or A",
                        Quantity.DENSITY: "mA/cm² or A/cm²"}.get(quantity, "")
            raise ValueError(f"{typed!r} is not a {quantity.value} unit; use {accepted}") from None
        return typed.strip()


def _canonical(value: str, allowed: tuple[str, ...]) -> str:
    folded = value.strip().casefold().replace("cm2", "cm²")
    for item in allowed:
        if item.casefold() == folded:
            return item
    return value
