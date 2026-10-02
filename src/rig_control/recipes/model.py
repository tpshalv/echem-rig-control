"""Immutable, device-ID based recipe description.

A recipe is an ordered list of steps.  There are four kinds:

- ``SetStep`` writes one or more settings, in the order listed, and may
  then hold (wait) before the next step.
- ``WaitStep`` holds for a duration, marked as a settle or a measurement.
- ``LoopStep`` sets one setting to each of its values in turn and runs its
  own steps after each value.
- ``RepeatStep`` runs its own steps a fixed number of times.

Loops nest by containing other loops, so the hierarchy is the data
structure itself.  A numeric setting may be reached by a ``Ramp`` (a
staircase with a hold at each value) instead of a jump, e.g. for break-in.
Every step may carry a human-readable ``name`` (e.g.
"Break in"), and a loop may name each pass (e.g. "100 mA"), so the recipe
and the recorded events read naturally later.  Recipes deliberately store no connection configuration:
a setting is always addressed by a profile/device ID and a supported
setting name.
"""
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from typing import TypeAlias

from rig_control.devices.power_supply import PowerSupplyOperatingMode

RecipeValue: TypeAlias = float | bool | str
RECIPE_FORMAT_VERSION = 2


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value


def _value(value: object, name: str = "Value") -> RecipeValue:
    if isinstance(value, bool) or isinstance(value, str):
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"{name} text cannot be blank")
        return value
    if not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise TypeError(f"{name} must be finite number, Boolean, or text")
    return float(value)


def _name(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Step name must be text")
    return value.strip()


def _duration(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not isfinite(float(value)) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


class WaitPurpose(StrEnum):
    """Why a wait exists, so recorded data can tell settling from measuring."""
    SETTLE = "settle"
    MEASURE = "measure"


class RampKind(StrEnum):
    STEP_SIZE = "step_size"      # steps of a fixed size
    STEP_COUNT = "step_count"    # a number of equal steps
    SETPOINTS = "setpoints"      # listed values (Set steps only)
    RATE = "rate"                # a rate, as fine steps every ``dwell_seconds``


#: A runaway ramp (e.g. 1 µA steps over 10 A) is refused rather than run.
MAX_RAMP_POINTS = 10_000


def _clean(value: float) -> float:
    """Drop float noise, e.g. 0.1 + 0.2 = 0.30000000000000004."""
    return float(f"{value:.12g}")


@dataclass(frozen=True, slots=True)
class Ramp:
    """Reach a numeric target as a staircase instead of a jump.

    Every value the ramp moves through on the way is held for
    ``dwell_seconds``; the target itself is not, so the step's own hold (or
    the loop's steps) follows straight on from it.  Values are in the
    setting's own (canonical) units; ``rate`` is units per second.

    A ramp normally starts from the last value the recipe set for that
    setting, worked out by ``rig_control.recipes.plan``.  ``start`` is an
    optional fixed start: the setting jumps there first, then ramps (in a
    loop, for its first value only).  Up or down depends only on the two
    endpoints.
    """
    kind: RampKind
    dwell_seconds: float
    start: float | None = None
    step: float = 0.0
    count: int = 0
    setpoints: tuple[float, ...] = ()
    rate: float = 0.0
    purpose: WaitPurpose = WaitPurpose.SETTLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RampKind(self.kind))
        object.__setattr__(self, "dwell_seconds", _duration(self.dwell_seconds, "Ramp hold"))
        object.__setattr__(self, "purpose", WaitPurpose(self.purpose))
        if self.kind is RampKind.SETPOINTS:
            if not isinstance(self.setpoints, tuple) or not self.setpoints:
                raise ValueError("A setpoint ramp needs one or more setpoints")
            object.__setattr__(self, "setpoints", tuple(_finite(v, "Ramp setpoint") for v in self.setpoints))
            object.__setattr__(self, "start", None)
            return
        if self.start is not None:
            object.__setattr__(self, "start", _finite(self.start, "Ramp start"))
        if self.kind is RampKind.STEP_SIZE and not _finite(self.step, "Ramp step") > 0:
            raise ValueError("Ramp step size must be more than 0")
        if self.kind is RampKind.STEP_COUNT and (isinstance(self.count, bool) or not isinstance(self.count, int)
                                                 or self.count < 1):
            raise ValueError("Number of ramp steps must be a whole number of at least 1")
        if self.kind is RampKind.RATE:
            if not _finite(self.rate, "Ramp rate") > 0:
                raise ValueError("Ramp rate must be more than 0")
            if self.dwell_seconds <= 0:
                raise ValueError("A rate ramp needs an update interval of more than 0 s")

    @property
    def needs_start(self) -> bool:
        """Whether the path depends on where the ramp starts (a setpoint list does not)."""
        return self.kind is not RampKind.SETPOINTS


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def ramp_holds(path: tuple[float, ...]) -> int:
    """How many values of a ramp path are held: all but the target at the end."""
    return max(len(path) - 1, 0)


def ramp_path(ramp: Ramp, start: float, target: float) -> tuple[float, ...]:
    """Every value the ramp writes, in order, ending at ``target`` (see ``ramp_holds``).

    ``start`` is where it begins (already written); it is not repeated,
    except that a setpoint ramp holds at every listed setpoint, the first
    included.  Steps never overshoot: the last one is shortened to land on
    the target.
    """
    if ramp.kind is RampKind.SETPOINTS:
        path = [v for v in ramp.setpoints]
        if not path or path[-1] != target:
            path.append(target)
        return tuple(_clean(v) for v in path)
    if _clean(start) == _clean(target):
        return ()
    if ramp.kind is RampKind.STEP_COUNT:
        return tuple(_clean(start + (target - start) * k / ramp.count) for k in range(1, ramp.count + 1))
    size = ramp.step if ramp.kind is RampKind.STEP_SIZE else ramp.rate * ramp.dwell_seconds
    count = int(abs(target - start) / size - 1e-9) + 1
    if count > MAX_RAMP_POINTS:
        raise ValueError(f"That ramp has {count} steps; the limit is {MAX_RAMP_POINTS}")
    direction = 1.0 if target > start else -1.0
    return tuple(_clean(start + direction * size * k) for k in range(1, count)) + (_clean(target),)


@dataclass(frozen=True, slots=True)
class Assignment:
    """One setting and its value. ``unit`` is part of its portable recipe meaning.

    With a ``ramp``, the value is reached as a staircase rather than a jump.
    """
    device_id: str
    setting: str
    value: RecipeValue
    unit: str = ""
    ramp: Ramp | None = None

    def __post_init__(self) -> None:
        _text(self.device_id, "Device ID")
        _text(self.setting, "Setting")
        object.__setattr__(self, "value", _value(self.value))
        if not isinstance(self.unit, str):
            raise TypeError("Setting unit must be text")
        if self.ramp is not None:
            if not isinstance(self.ramp, Ramp):
                raise TypeError("A ramp must be a Ramp or None")
            if not isinstance(self.value, float):
                raise ValueError(f"{self.setting} is not a number, so it cannot be ramped")
            if self.ramp.start is not None:
                ramp_path(self.ramp, self.ramp.start, self.value)  # refuses runaway ramps now

    @property
    def key(self) -> tuple[str, str]:
        return self.device_id, self.setting


@dataclass(frozen=True, slots=True)
class SetStep:
    """Write one or more settings, top to bottom, with no wait between them.

    Order matters: e.g. a supply's limit must come before its output is
    turned on.
    """
    assignments: tuple[Assignment, ...]
    name: str = ""
    #: Optional wait after writing, e.g. "hotplate to 50 °C, settle 10 min".
    #: It only delays the next step; the settings stay either way.
    hold: "WaitStep | None" = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name))
        if self.hold is not None and not isinstance(self.hold, WaitStep):
            raise TypeError("A Set step's hold must be a WaitStep or None")
        if self.hold is not None and self.hold.duration_seconds == 0:
            object.__setattr__(self, "hold", None)
        if not isinstance(self.assignments, tuple) or not self.assignments:
            raise ValueError("A Set step needs at least one setting")
        if not all(isinstance(a, Assignment) for a in self.assignments):
            raise TypeError("Set step entries must be Assignments")
        keys = [a.key for a in self.assignments]
        for key in keys:
            if keys.count(key) > 1:
                raise ValueError(f"{key[0]}.{key[1]} is set twice in one step")

    @classmethod
    def one(cls, device_id: str, setting: str, value: RecipeValue, unit: str = "", name: str = "") -> "SetStep":
        return cls((Assignment(device_id, setting, value, unit),), name)

    @property
    def hold_wait(self) -> "WaitStep | None":
        """The hold as it runs and is recorded: named after the step unless it has its own name."""
        if self.hold is None:
            return None
        return replace(self.hold, name=self.hold.name or self.name)


@dataclass(frozen=True, slots=True)
class WaitStep:
    """Hold every setting and output for ``duration_seconds``."""
    duration_seconds: float
    purpose: WaitPurpose = WaitPurpose.MEASURE
    name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "duration_seconds", _duration(self.duration_seconds, "Wait duration"))
        object.__setattr__(self, "purpose", WaitPurpose(self.purpose))
        object.__setattr__(self, "name", _name(self.name))

    @property
    def title(self) -> str:
        return self.name or self.purpose.value.capitalize()


@dataclass(frozen=True, slots=True)
class LoopStep:
    """For each value in order, set the setting, then run ``steps``.

    Repeated values are intentional and kept.  ``pass_names`` is either empty
    or one name per value, e.g. ``("break in", "100 mA", "200 mA")``.
    """
    device_id: str
    setting: str
    values: tuple[RecipeValue, ...]
    unit: str = ""
    steps: tuple["RecipeStep", ...] = ()
    name: str = ""
    pass_names: tuple[str, ...] = ()
    #: Ramp to each value instead of jumping, each from the value before;
    #: the first from the last value set before the loop (or ``ramp.start``).
    ramp: Ramp | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name))
        if self.ramp is not None:
            if not isinstance(self.ramp, Ramp):
                raise TypeError("A ramp must be a Ramp or None")
            if self.ramp.kind is RampKind.SETPOINTS:
                raise ValueError("A loop cannot ramp via a list of setpoints; use steps or a rate")
        if not isinstance(self.pass_names, tuple):
            raise TypeError("Pass names must be a tuple")
        object.__setattr__(self, "pass_names", tuple(_name(n) for n in self.pass_names))
        if self.pass_names and len(self.pass_names) != len(self.values):
            raise ValueError(f"Give one pass name per value ({len(self.values)}), "
                             f"or none; got {len(self.pass_names)}")
        _text(self.device_id, "Device ID")
        _text(self.setting, "Setting")
        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("A loop needs one or more values")
        object.__setattr__(self, "values", tuple(_value(v, "Loop value") for v in self.values))
        if self.ramp is not None:
            if not all(isinstance(v, float) for v in self.values):
                raise ValueError(f"{self.setting} is not a number, so it cannot be ramped")
        if not isinstance(self.unit, str):
            raise TypeError("Loop unit must be text")
        _check_steps(self.steps)

    @property
    def iterations(self) -> int:
        return len(self.values)

    def assignment(self, value: RecipeValue) -> Assignment:
        return Assignment(self.device_id, self.setting, value, self.unit)

    def pass_name(self, iteration: int) -> str:
        return self.pass_names[iteration] if self.pass_names else ""


@dataclass(frozen=True, slots=True)
class RepeatStep:
    """Run ``steps`` ``count`` times without changing any setting."""
    count: int
    steps: tuple["RecipeStep", ...] = ()
    name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name))
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ValueError("Repeat count must be a whole number of at least 1")
        _check_steps(self.steps)

    @property
    def iterations(self) -> int:
        return self.count


RecipeStep: TypeAlias = SetStep | WaitStep | LoopStep | RepeatStep
_STEP_TYPES = (SetStep, WaitStep, LoopStep, RepeatStep)


def _check_steps(steps: object) -> None:
    if not isinstance(steps, tuple) or not all(isinstance(s, _STEP_TYPES) for s in steps):
        raise TypeError("Recipe steps have invalid entries")


class EndAction(StrEnum):
    """What one device does when the run ends or is stopped."""
    SAFE = "safe"      # its own safe state (off)
    LEAVE = "leave"    # keep whatever the recipe last set
    SET = "set"        # write the listed settings


@dataclass(frozen=True, slots=True)
class EndDevice:
    device_id: str
    action: EndAction
    #: Written for ``EndAction.SET``, in order; never ramped, so Stop is never delayed.
    assignments: tuple[Assignment, ...] = ()

    def __post_init__(self) -> None:
        _text(self.device_id, "Device ID")
        object.__setattr__(self, "action", EndAction(self.action))
        if not isinstance(self.assignments, tuple) or not all(isinstance(a, Assignment) for a in self.assignments):
            raise TypeError("End-state settings must be Assignments")
        if self.action is EndAction.SET:
            if not self.assignments:
                raise ValueError(f"{self.device_id}: choose at least one setting, or use safe state")
            if any(a.device_id != self.device_id for a in self.assignments):
                raise ValueError(f"{self.device_id}: end-state settings must be for that device")
            if any(a.ramp is not None for a in self.assignments):
                raise ValueError("End-state settings cannot ramp; Stop must never be delayed")
        elif self.assignments:
            object.__setattr__(self, "assignments", ())


@dataclass(frozen=True, slots=True)
class EndState:
    """How the rig is left when the run finishes, is stopped, or fails.

    Any device not listed goes to its safe state (off), so forgetting a
    device is never the dangerous choice.  The rig's global safe state is
    separate: that is for emergencies and always turns everything off.
    """
    devices: tuple[EndDevice, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.devices, tuple) or not all(isinstance(d, EndDevice) for d in self.devices):
            raise TypeError("End-state entries must be EndDevice")
        ids = [d.device_id for d in self.devices]
        if len(ids) != len(set(ids)):
            raise ValueError("A device can appear only once in the end state")

    def for_device(self, device_id: str) -> EndDevice:
        return next((d for d in self.devices if d.device_id == device_id), EndDevice(device_id, EndAction.SAFE))


@dataclass(frozen=True, slots=True)
class Recipe:
    name: str
    steps: tuple[RecipeStep, ...] = ()
    description: str = ""
    version: int = RECIPE_FORMAT_VERSION
    end_state: EndState = EndState()

    def __post_init__(self) -> None:
        _text(self.name, "Recipe name")
        if not isinstance(self.description, str):
            raise TypeError("Recipe description must be text")
        if self.version != RECIPE_FORMAT_VERSION:
            raise ValueError("Unsupported recipe version")
        _check_steps(self.steps)
        if not isinstance(self.end_state, EndState):
            raise TypeError("Recipe end state must be an EndState")


def iter_steps(steps: tuple[RecipeStep, ...]) -> Iterator[RecipeStep]:
    """Every step once, depth first, loops before their contents."""
    for step in steps:
        yield step
        if isinstance(step, (LoopStep, RepeatStep)):
            yield from iter_steps(step.steps)


# --- Power-supply modes ----------------------------------------------------

#: Recipe setting names for each power-supply mode, as ``(setpoint, limit)``.
#: The setting written states the intended mode, so there is no separate
#: mode field.  Both modes drive the same two instrument registers; only the
#: roles differ.
POWER_SUPPLY_MODE_SETTINGS: dict[PowerSupplyOperatingMode, tuple[str, str]] = {
    PowerSupplyOperatingMode.CONSTANT_CURRENT: ("current_setpoint", "voltage_limit"),
    PowerSupplyOperatingMode.CONSTANT_VOLTAGE: ("voltage", "current_limit"),
}
POWER_SUPPLY_OUTPUT_SETTING = "output_enabled"


def power_supply_mode_of(setting: str) -> PowerSupplyOperatingMode | None:
    for mode, names in POWER_SUPPLY_MODE_SETTINGS.items():
        if setting in names:
            return mode
    return None


# --- JSON -------------------------------------------------------------------

def _ramp_to_dict(ramp: Ramp | None) -> dict[str, object] | None:
    if ramp is None:
        return None
    return {"kind": ramp.kind.value, "dwell_seconds": ramp.dwell_seconds, "start": ramp.start, "step": ramp.step,
            "count": ramp.count, "setpoints": list(ramp.setpoints), "rate": ramp.rate, "purpose": ramp.purpose.value}


def _ramp_from_dict(raw: object) -> Ramp | None:
    if raw is None:
        return None
    item = _mapping(raw, "ramp")
    start = item.get("start")
    return Ramp(RampKind(str(item["kind"])), float(item["dwell_seconds"]),  # type: ignore[arg-type]
                None if start is None else float(start), float(item.get("step", 0)),  # type: ignore[arg-type]
                int(item.get("count", 0)), tuple(float(v) for v in _list(item.get("setpoints", []), "setpoints")),  # type: ignore[arg-type]
                float(item.get("rate", 0)), WaitPurpose(str(item.get("purpose", "settle"))))  # type: ignore[arg-type]


def _assignment_to_dict(a: Assignment) -> dict[str, object]:
    return {"device_id": a.device_id, "setting": a.setting, "value": a.value, "unit": a.unit,
            "ramp": _ramp_to_dict(a.ramp)}


def _step_to_dict(step: RecipeStep) -> dict[str, object]:
    if isinstance(step, SetStep):
        hold = None if step.hold is None else {
            "duration_seconds": step.hold.duration_seconds, "purpose": step.hold.purpose.value, "name": step.hold.name}
        return {"kind": "set", "name": step.name, "assignments": [_assignment_to_dict(a) for a in step.assignments],
                "hold": hold}
    if isinstance(step, WaitStep):
        return {"kind": "wait", "name": step.name, "duration_seconds": step.duration_seconds,
                "purpose": step.purpose.value}
    if isinstance(step, LoopStep):
        return {"kind": "loop", "name": step.name, "device_id": step.device_id, "setting": step.setting,
                "values": list(step.values), "pass_names": list(step.pass_names), "unit": step.unit,
                "ramp": _ramp_to_dict(step.ramp),
                "steps": [_step_to_dict(s) for s in step.steps]}
    return {"kind": "repeat", "name": step.name, "count": step.count,
            "steps": [_step_to_dict(s) for s in step.steps]}


def recipe_to_dict(recipe: Recipe) -> dict[str, object]:
    return {"version": recipe.version, "name": recipe.name, "description": recipe.description,
            "steps": [_step_to_dict(s) for s in recipe.steps],
            "end_state": [{"device_id": d.device_id, "action": d.action.value,
                           "assignments": [_assignment_to_dict(a) for a in d.assignments]}
                          for d in recipe.end_state.devices]}


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _assignment_from_dict(raw: object) -> Assignment:
    item = _mapping(raw, "setting")
    return Assignment(str(item["device_id"]), str(item["setting"]), item["value"], str(item.get("unit", "")),
                      _ramp_from_dict(item.get("ramp")))


def _wait_from_dict(item: Mapping[str, object]) -> WaitStep:
    # Early version 2 files called a wait's name its "label".
    return WaitStep(float(item["duration_seconds"]), WaitPurpose(str(item.get("purpose", "measure"))),  # type: ignore[arg-type]
                    str(item.get("name", item.get("label", ""))))


def _step_from_dict(raw: object) -> RecipeStep:
    item = _mapping(raw, "step")
    kind = item.get("kind")
    name = str(item.get("name", ""))
    children = lambda: tuple(_step_from_dict(s) for s in _list(item.get("steps", []), "loop steps"))
    if kind == "set":
        # Early version 2 files held one setting directly on the step.
        raw_assignments = _list(item["assignments"], "set assignments") if "assignments" in item else [item]
        raw_hold = item.get("hold")
        hold = None if raw_hold is None else _wait_from_dict(_mapping(raw_hold, "hold"))
        return SetStep(tuple(_assignment_from_dict(a) for a in raw_assignments), name, hold)
    if kind == "wait":
        return _wait_from_dict(item)
    if kind == "loop":
        return LoopStep(str(item["device_id"]), str(item["setting"]), tuple(_list(item["values"], "loop values")),
                        str(item.get("unit", "")), children(), name,
                        tuple(str(n) for n in _list(item.get("pass_names", []), "pass names")),
                        _ramp_from_dict(item.get("ramp")))
    if kind == "repeat":
        count = item["count"]
        if isinstance(count, float) and count.is_integer():
            count = int(count)
        return RepeatStep(count, children(), name)  # type: ignore[arg-type]
    raise ValueError(f"Unknown recipe step kind {kind!r}")


def recipe_from_dict(data: Mapping[str, object]) -> Recipe:
    version = data.get("version")
    if version != RECIPE_FORMAT_VERSION:
        raise ValueError(
            f"Recipe format version {version!r} is not supported; "
            f"this builder reads version {RECIPE_FORMAT_VERSION}. Rebuild the recipe in the builder."
        )
    # Files saved before end states existed send everything to safe state,
    # which is what they did when they were written.
    end = EndState(tuple(
        EndDevice(str(item["device_id"]), EndAction(str(item["action"])),
                  tuple(_assignment_from_dict(a) for a in _list(item.get("assignments", []), "end-state settings")))
        for item in (_mapping(raw, "end-state device") for raw in _list(data.get("end_state", []), "end_state"))))
    return Recipe(name=str(data["name"]), description=str(data.get("description", "")),
                  steps=tuple(_step_from_dict(s) for s in _list(data.get("steps", []), "steps")), end_state=end)
