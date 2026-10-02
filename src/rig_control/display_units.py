"""Show and accept measurements in a chosen unit, without changing control.

Instruments keep working in their own units: an Alicat configured in SLPM is
commanded in SLPM, and a pressure controller in PSIA is commanded in PSIA.
This layer only converts at the edges — what a screen displays, and what a
typed value means before it becomes a command.

A conversion is refused rather than guessed. An unknown unit is shown as the
instrument reported it, and a typed value in a unit that cannot be converted
never reaches a device.

Recorded and exported data keep the instrument's own units, so a preference
changed mid-experiment cannot alter the meaning of anything already written.
"""

from dataclasses import dataclass

from rig_control.devices.mass_flow_controller import mass_flow_unit_factor
from rig_control.devices.pressure_controller import absolute_unit_factor

#: Selectable on the settings screen. "native" leaves the instrument's own
#: unit alone. Absolute pressure only: this software controls absolute-pressure
#: loops, and offering a gauge entry here would reintroduce the ambiguity that
#: the BPR work deliberately removed.
NATIVE = "native"
PRESSURE_UNITS = (NATIVE, "bara", "psia", "kpaa")
FLOW_UNITS = (NATIVE, "SCCM", "SLPM")
#: Currents are always stored and commanded in A; mA is display and entry only.
CURRENT_UNITS = ("mA", "A")


def _converted(
    value: float,
    from_unit: str,
    to_unit: str,
    factor: "callable[[str], float]",
) -> tuple[float, str]:
    if to_unit == NATIVE or not from_unit:
        return value, from_unit
    if from_unit.strip().casefold() == to_unit.strip().casefold():
        return value, to_unit
    try:
        scale = factor(from_unit) / factor(to_unit)
    except ValueError:
        # One of the units is not one we can convert. Showing the
        # instrument's own number and unit is always truthful.
        return value, from_unit
    return value * scale, to_unit


@dataclass(frozen=True, slots=True)
class DisplayUnits:
    """The units a screen shows and accepts, per quantity."""

    pressure: str = NATIVE
    flow: str = NATIVE
    current: str = "mA"

    def __post_init__(self) -> None:
        for name, allowed in (("pressure", PRESSURE_UNITS), ("flow", FLOW_UNITS), ("current", CURRENT_UNITS)):
            value = getattr(self, name)
            if not isinstance(value, str) or value.strip().casefold() not in {
                item.casefold() for item in allowed
            }:
                raise ValueError(
                    f"Display {name} unit must be one of: {', '.join(allowed)}"
                )
            object.__setattr__(self, name, _canonical(value, allowed))

    def show_pressure(self, value: float, unit: str) -> tuple[float, str]:
        """Convert one absolute pressure for display."""

        return _converted(value, unit, self.pressure, absolute_unit_factor)

    def show_flow(self, value: float, unit: str) -> tuple[float, str]:
        """Convert one mass-flow rate for display."""

        return _converted(value, unit, self.flow, mass_flow_unit_factor)

    def entry_pressure_unit(self, native_unit: str) -> str:
        """Return the unit a typed pressure is in, given the instrument's own.

        The value is sent with this unit attached, so the number and its unit
        can never be separated on the way to a device.
        """

        return self.show_pressure(1.0, native_unit)[1]

    def entry_flow_unit(self, native_unit: str) -> str:
        return self.show_flow(1.0, native_unit)[1]


def _canonical(value: str, allowed: tuple[str, ...]) -> str:
    for item in allowed:
        if item.casefold() == value.strip().casefold():
            return item
    return value
