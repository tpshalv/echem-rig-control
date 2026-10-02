"""What a recipe step can set, and how the builder shows and reads values.

No Tk here, so everything in this module is tested directly.
"""
from dataclasses import dataclass
from math import isclose
import re

from rig_control.recipes import Assignment
from rig_control.devices.power_supply import PowerSupplyOperatingMode
from rig_control.recipes.model import power_supply_mode_of
from rig_control.rig_profile import DeviceCapability


@dataclass(frozen=True, slots=True)
class SettingSpec:
    name: str
    label: str
    unit: str = ""
    #: "number", "bool" (On/Off) or "choice" (one of ``choices``).
    kind: str = "number"
    choices: tuple[str, ...] = ()


ON_OFF = ("On", "Off")
SETTINGS: dict[DeviceCapability, tuple[SettingSpec, ...]] = {
    DeviceCapability.MASS_FLOW_CONTROLLER: (SettingSpec("flow", "Flow setpoint", "native unit"),),
    DeviceCapability.BACK_PRESSURE_CONTROLLER: (SettingSpec("pressure", "Pressure setpoint", "bara"),),
    # The setting chosen states the supply mode; there is no separate mode.
    DeviceCapability.DC_POWER_SUPPLY: (
        SettingSpec("current_setpoint", "Current setpoint (CC)", "A"),
        SettingSpec("voltage_limit", "Voltage limit (CC)", "V"),
        SettingSpec("voltage", "Voltage setpoint (CV)", "V"),
        SettingSpec("current_limit", "Current limit (CV)", "A"),
        SettingSpec("output_enabled", "Output", kind="bool"),
    ),
    DeviceCapability.TEMPERATURE_CONTROLLER: (SettingSpec("temperature_setpoint", "Temperature setpoint", "°C"),),
    DeviceCapability.PERISTALTIC_PUMP: (
        SettingSpec("pump_speed", "Pump speed", "rpm"),
        SettingSpec("pump_direction", "Pump direction", kind="choice", choices=("forward", "reverse")),
        SettingSpec("pump_running", "Pump running", kind="bool"),
    ),
    DeviceCapability.HOTPLATE_STIRRER: (
        SettingSpec("hotplate_temperature", "Hotplate temperature", "°C"),
        SettingSpec("hotplate_speed", "Stir speed", "rpm"),
        SettingSpec("hotplate_heating", "Heating", kind="bool"),
        SettingSpec("hotplate_stirring", "Stirring", kind="bool"),
    ),
}
_SPECS_BY_NAME = {spec.name: spec for specs in SETTINGS.values() for spec in specs}
TIME_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0}
MAX_RANGE_VALUES = 10_000


def spec_for(setting: str) -> SettingSpec:
    return _SPECS_BY_NAME.get(setting, SettingSpec(setting, setting.replace("_", " ")))


def short_label(spec: SettingSpec) -> str:
    """The label without its CC/CV suffix, for places that show the mode once per device."""
    return spec.label.removesuffix(" (CC)").removesuffix(" (CV)")


def default_value(spec: SettingSpec) -> object:
    return True if spec.kind == "bool" else spec.choices[0] if spec.kind == "choice" else 0.0


def unit_for(spec: SettingSpec) -> str:
    """The unit saved with the recipe; "native unit" is only a hint for flow."""
    return "" if spec.unit == "native unit" else spec.unit


# --- Durations ------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:g} s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.3g} min"
    hours, minutes = divmod(round(minutes), 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def format_clock(seconds: float) -> str:
    """Elapsed time as ``h:mm:ss`` for the timeline."""
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}"


_DURATION_UNITS = {
    **dict.fromkeys(("", "s", "sec", "secs", "second", "seconds"), 1.0),
    **dict.fromkeys(("m", "min", "mins", "minute", "minutes"), 60.0),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), 3600.0),
    **dict.fromkeys(("d", "day", "days"), 86400.0),
}
_DURATION_PART = re.compile(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*([a-zA-Z]*)")


def parse_duration(text: str) -> float:
    """Seconds from text like ``90``, ``90 s``, ``2.5 min`` or ``1 h 30 min``.

    A bare number is seconds.  Reads back anything ``format_duration`` writes.
    """
    if not text.strip():
        raise ValueError("Enter a duration, e.g. 90 s, 10 min or 1 h 30 min")
    total, position = 0.0, 0
    while position < len(text.rstrip()):
        match = _DURATION_PART.match(text, position)
        if match is None:
            raise ValueError(f"{text.strip()!r} is not a duration, e.g. 90 s, 10 min or 1 h 30 min")
        number, unit = match.groups()
        if unit.casefold() not in _DURATION_UNITS:
            raise ValueError(f"Unknown time unit {unit!r}; use s, min, h or d")
        total += float(number) * _DURATION_UNITS[unit.casefold()]
        position = match.end()
    return total


# --- Values ---------------------------------------------------------------------

def format_value(value: object, spec: SettingSpec | None = None) -> str:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def parse_value(text: str, spec: SettingSpec) -> object:
    text = text.strip()
    if not text:
        raise ValueError("Enter a value")
    if spec.kind == "bool":
        folded = text.casefold()
        if folded in {"on", "true", "1", "yes"}:
            return True
        if folded in {"off", "false", "0", "no"}:
            return False
        raise ValueError(f"{spec.label} must be On or Off")
    if spec.kind == "choice":
        if text not in spec.choices:
            raise ValueError(f"{spec.label} must be one of: {', '.join(spec.choices)}")
        return text
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a number") from None


def parse_value_list(text: str, spec: SettingSpec) -> tuple[object, ...]:
    if not text.strip():
        raise ValueError("Enter one or more values, separated by commas")
    return tuple(parse_value(p, spec) for p in text.replace(";", ",").split(","))


def expand_range(start: float, stop: float, step: float) -> tuple[float, ...]:
    """``start`` to ``stop`` inclusive; ``step`` may be negative to count down."""
    if step == 0:
        raise ValueError("Step cannot be zero")
    if (stop - start) * step < 0:
        raise ValueError("Step goes the wrong way; use a negative step to count down")
    count = int((stop - start) / step + 1e-9) + 1
    if count > MAX_RANGE_VALUES:
        raise ValueError(f"That range has {count} values; the limit is {MAX_RANGE_VALUES}")
    return tuple(round(start + i * step, 12) for i in range(count))


def as_range(values: tuple[object, ...]) -> tuple[float, float, float] | None:
    """``(start, stop, step)`` when numeric values are evenly spaced, else None."""
    if len(values) < 3 or not all(isinstance(v, float) for v in values):
        return None
    step = values[1] - values[0]  # type: ignore[operator]
    if step == 0 or not all(isclose(b - a, step, rel_tol=1e-9, abs_tol=1e-12)  # type: ignore[operator]
                            for a, b in zip(values, values[1:])):
        return None
    return values[0], values[-1], step  # type: ignore[return-value]


_NUMBER = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
_RANGE_TEXT = re.compile(rf"\s*{_NUMBER}\s+to\s+{_NUMBER}\s+step\s+{_NUMBER}\s*", re.IGNORECASE)


def format_loop_values(values: tuple[object, ...]) -> str:
    """Evenly spaced numbers as ``1 to 4 step 1``, anything else as a list."""
    span = as_range(values)
    if span:
        return " ".join((f"{span[0]:g}", "to", f"{span[1]:g}", "step", f"{span[2]:g}"))
    return ", ".join(format_value(v) for v in values)


def parse_loop_values(text: str, spec: SettingSpec) -> tuple[object, ...]:
    """Read ``1, 2, 4`` or ``1 to 4 step 1``; what ``format_loop_values`` writes."""
    match = _RANGE_TEXT.fullmatch(text)
    if match and spec.kind == "number":
        return expand_range(*(float(g) for g in match.groups()))
    return parse_value_list(text, spec)


def parse_pass_names(text: str) -> tuple[str, ...]:
    """Comma-separated pass names; blank text means none."""
    return tuple(part.strip() for part in text.split(",")) if text.strip() else ()


# --- Building Set steps -----------------------------------------------------------

def device_settings(capability: DeviceCapability, mode: PowerSupplyOperatingMode | None = None) -> tuple[SettingSpec, ...]:
    """The settings a device card lists, in the order they are written.

    For a supply only the chosen mode's setpoint and limit are listed, then
    Output last, so a card can never turn the output on before its limit.
    """
    specs = SETTINGS.get(capability, ())
    if capability is not DeviceCapability.DC_POWER_SUPPLY:
        return specs
    mode = mode or PowerSupplyOperatingMode.CONSTANT_CURRENT
    return tuple(s for s in specs if power_supply_mode_of(s.name) in (None, mode))


def group_by_device(assignments: tuple[Assignment, ...]) -> list[tuple[str, list[Assignment]]]:
    """Assignments grouped per device, devices in the order they first appear."""
    groups: dict[str, list[Assignment]] = {}
    for a in assignments:
        groups.setdefault(a.device_id, []).append(a)
    return list(groups.items())


def group_mode(assignments: list[Assignment]) -> PowerSupplyOperatingMode | None:
    """The CC/CV mode a device's settings state (the last one, if both)."""
    modes = [power_supply_mode_of(a.setting) for a in assignments if power_supply_mode_of(a.setting)]
    return modes[-1] if modes else None


def next_device(used: set[str], roles):
    """The first role not yet in the step, or None."""
    return next((r for r in roles if r.device_id not in used), None)


# --- Display units -------------------------------------------------------------------

def _clean(value: float) -> float:
    """Drop float noise from a unit conversion, e.g. 0.07 * 1000 = 70.00000000000001."""
    return float(f"{value:.12g}")


#: A current unit typed straight after a number: ``250mA``, ``0.25 A``.
_CURRENT_UNIT = re.compile(r"([\d.])\s*(mA|A)\b", re.IGNORECASE)
CURRENT_UNITS = ("mA", "A")


class ValueDisplay:
    """Shows and reads recipe values in the builder's display units.

    Recipes keep amperes, the canonical unit the supply is commanded in and
    recordings use.  With ``current_unit="mA"`` the builder shows and accepts
    milliamps, converting only here, at the edge of the UI.
    """

    def __init__(self, current_unit: str = "mA") -> None:
        if current_unit not in ("mA", "A"):
            raise ValueError("Current display unit must be mA or A")
        self.current_unit = current_unit

    def _scale(self, spec: SettingSpec) -> float:
        return 1000.0 if spec.unit == "A" and self.current_unit == "mA" else 1.0

    def unit(self, spec: SettingSpec) -> str:
        """The unit label shown beside a value (may be a hint like "native unit")."""
        return "mA" if self._scale(spec) != 1.0 else spec.unit

    def suffix(self, spec: SettingSpec) -> str:
        """The unit written after a value in running text; empty for a hint."""
        unit = self.unit(spec)
        return "" if unit == "native unit" else unit

    def to_display(self, value: object, spec: SettingSpec) -> object:
        if isinstance(value, float):
            return _clean(value * self._scale(spec))
        return value

    def from_display(self, value: object, spec: SettingSpec) -> object:
        if isinstance(value, float):
            return _clean(value / self._scale(spec))
        return value

    def show(self, value: object, spec: SettingSpec) -> str:
        return format_value(self.to_display(value, spec))

    def _typed_unit(self, text: str, spec: SettingSpec) -> tuple[str, "ValueDisplay"]:
        """Honour a unit typed after the number(s), e.g. ``0.25 A`` in an mA box.

        A typed unit always wins over the box's own unit.  Mixing mA and A in
        one entry is refused rather than guessed.
        """
        if spec.unit != "A":
            return text, self
        typed = {m.group(2).casefold() for m in _CURRENT_UNIT.finditer(text)}
        if not typed:
            return text, self
        if len(typed) > 1:
            raise ValueError("Use one unit, mA or A, for all the values in a box")
        return _CURRENT_UNIT.sub(r"\1", text), ValueDisplay("mA" if typed == {"ma"} else "A")

    def read(self, text: str, spec: SettingSpec) -> object:
        text, units = self._typed_unit(text, spec)
        return units.from_display(parse_value(text, spec), spec)

    def show_values(self, values: tuple[object, ...], spec: SettingSpec) -> str:
        return format_loop_values(tuple(self.to_display(v, spec) for v in values))

    def read_values(self, text: str, spec: SettingSpec) -> tuple[object, ...]:
        text, units = self._typed_unit(text, spec)
        return tuple(units.from_display(v, spec) for v in parse_loop_values(text, spec))

    def convert_text(self, text: str, spec: SettingSpec, to: "ValueDisplay", *, many: bool = False) -> str:
        """Re-show typed text in another unit (250 → 0.25 when mA → A), keeping the quantity.

        Text that does not read as a value yet is left as it is.
        """
        try:
            if many:  # Stays a comma list: it is going back into a list box.
                return ", ".join(to.show(v, spec) for v in self.read_values(text, spec))
            return to.show(self.read(text, spec), spec)
        except ValueError:
            return text

    def text(self, setting: str, value: object, unit: str) -> str:
        """Value and unit for running text such as the timeline."""
        spec = spec_for(setting)
        suffix = self.suffix(spec) if self._scale(spec) != 1.0 else unit
        shown = self.show(value, spec)
        return f"{shown} {suffix}" if suffix else shown


# --- Ramps ------------------------------------------------------------------------------

#: How a ramp is described in the editor, in the order offered.
RAMP_METHODS = {
    "step_size": "in steps of",
    "step_count": "in a number of equal steps",
    "setpoints": "via listed setpoints",
    "rate": "at a rate",
}


def describe_ramp(ramp, spec: SettingSpec, units: "ValueDisplay", start: float | None = None) -> str:
    """e.g. ``ramp from 20 mA in 10 mA steps, 30 s each``.

    ``start`` is where the ramp begins when it has no fixed start (worked
    out from the recipe); a fixed start reads ``jump to 0 mA, then ramp …``.
    """
    unit = units.suffix(spec)
    def shown(value: float) -> str:
        return f"{units.show(value, spec)} {unit}".strip()
    hold = f", {format_duration(ramp.dwell_seconds)} each" if ramp.dwell_seconds else ""
    kind = ramp.kind.value
    if kind == "setpoints":
        values = ", ".join(units.show(v, spec) for v in ramp.setpoints)
        return f"ramp via {values} {unit}".rstrip() + hold
    origin = (f"jump to {shown(ramp.start)}, then ramp" if ramp.start is not None
              else f"ramp from {shown(start)}" if start is not None else "ramp")
    if kind == "step_size":
        return f"{origin} in {shown(ramp.step)} steps{hold}"
    if kind == "step_count":
        return f"{origin} in {ramp.count} step{'s' if ramp.count != 1 else ''}{hold}"
    per_minute = units.show(ramp.rate * 60.0, spec)
    return f"{origin} at {per_minute} {unit}/min".replace("  ", " ") + (
        f", updated every {format_duration(ramp.dwell_seconds)}" if ramp.dwell_seconds else "")


# --- End state ------------------------------------------------------------------------

END_ACTION_TEXT = {"safe": "Safe state (off)", "leave": "Leave as it is", "set": "Set to…"}
#: Devices a new recipe leaves running at the end, so stopping a run does not
#: stop the gas flow or shut the outlet (which can draw liquid back to the MFC).
LEFT_RUNNING_BY_DEFAULT = (DeviceCapability.MASS_FLOW_CONTROLLER, DeviceCapability.BACK_PRESSURE_CONTROLLER)


def default_end_state(roles):
    """New recipes: MFCs and back-pressure controllers left as they are, everything else off."""
    from rig_control.recipes import EndAction, EndDevice, EndState
    return EndState(tuple(EndDevice(r.device_id, EndAction.LEAVE) for r in roles
                          if r.capability in LEFT_RUNNING_BY_DEFAULT))


def _assignments_text(assignments, units: "ValueDisplay") -> str:
    parts = []
    for a in assignments:
        spec = spec_for(a.setting)
        suffix = units.suffix(spec)
        parts.append(f"{short_label(spec)} {units.show(a.value, spec)}{' ' + suffix if suffix else ''}")
    return ", ".join(parts)


def end_state_summary(end, roles, units: "ValueDisplay") -> list[str]:
    """Short lines a person can check at a glance, e.g. ``Left as they are: CO2 MFC``."""
    names = {r.device_id: r.friendly_name for r in roles}
    leave = [names.get(d.device_id, d.device_id) for d in end.devices if d.action.value == "leave"]
    lines = []
    if leave:
        lines.append(("Left as they are: " if len(leave) > 1 else "Left as it is: ") + ", ".join(leave))
    lines += [f"{names.get(d.device_id, d.device_id)}: {_assignments_text(d.assignments, units)}"
              for d in end.devices if d.action.value == "set"]
    lines.append("Everything else: off (safe state)" if lines else "Everything off (safe state)")
    return lines


def end_state_warnings(end, roles) -> list[str]:
    """Combinations worth a second look before a run."""
    from rig_control.recipes import EndAction
    warnings = []
    by_capability = lambda capability: [r for r in roles if r.capability is capability]
    flowing = [r.friendly_name for r in by_capability(DeviceCapability.MASS_FLOW_CONTROLLER)
               if (d := end.for_device(r.device_id)).action is EndAction.LEAVE
               or (d.action is EndAction.SET and any(a.setting == "flow" and a.value for a in d.assignments))]
    closing = [r.friendly_name for r in by_capability(DeviceCapability.BACK_PRESSURE_CONTROLLER)
               if end.for_device(r.device_id).action is EndAction.SAFE]
    if flowing and closing:
        warnings.append(f"{', '.join(flowing)} may keep flowing while {', '.join(closing)} closes its valve "
                        "(safe state): the cell can pressurise. Leave the back-pressure controller as it is, "
                        "or stop the flow.")
    for r in by_capability(DeviceCapability.DC_POWER_SUPPLY):
        d = end.for_device(r.device_id)
        if d.action is EndAction.LEAVE:
            warnings.append(f"{r.friendly_name} is left as it is: its output stays on if it was on.")
        elif d.action is EndAction.SET and any(a.setting == "output_enabled" and a.value is True for a in d.assignments):
            warnings.append(f"{r.friendly_name} output is turned ON in the end state.")
    return warnings
