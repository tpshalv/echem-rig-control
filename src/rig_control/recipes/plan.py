"""The recipe as it will actually run: one in-order walk that everything shares.

``plan()`` expands loops and ramps into what is written and what is waited
for, in order.  The runner executes it; the timeline, time estimate and
supply safety checks read it.  They cannot disagree, because there is only
one walk.

Ramps start from the last value the recipe set for that setting, which this
walk knows exactly because recipes have no branching.  A ramp with a fixed
``start`` jumps there first instead.  If nothing earlier sets the value and
no fixed start is given, the start is unknown and planning stops with
``RampStartUnknown``: the recipe never reads the instrument to guess.

A shuffled loop's order comes from ``seed``, the run's order seed.  The
same seed always gives the same orders, so a run, its timeline, estimate
and checks agree, and a run can be repeated exactly.  Each loop draws its
own order from the seed and where it is, so changing one loop leaves the
others' orders alone.
"""
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from random import Random
from typing import TypeAlias

from rig_control.devices.power_supply import PowerSupplyOperatingMode
from rig_control.recipes.model import (
    CURRENT_DENSITY_SETTING, POWER_SUPPLY_MODE_SETTINGS, POWER_SUPPLY_OUTPUT_SETTING, Assignment, LoopOrder, LoopStep, Ramp, Recipe, RecipeStep,
    RepeatStep, SetStep, WaitStep, iter_steps, power_supply_mode_of, ramp_holds, ramp_path,
)

#: Where an item comes from: for each nesting level, (step index, pass index).
#: For a loop the pass index is the value's place in the list, not in the run,
#: so it still names the same value when the loop is shuffled.
Trace: TypeAlias = tuple[tuple[int, int], ...]
ValueText = Callable[[str, object, str], str]


def value_text(setting: str, value: object, unit: str) -> str:
    """``0.1 A`` style text in the recipe's own (canonical) units."""
    shown = ("On" if value else "Off") if isinstance(value, bool) else (
        f"{value:g}" if isinstance(value, float) else str(value))
    return f"{shown} {unit}" if unit else shown


def loop_position_label(step: LoopStep | RepeatStep, iteration: int, value: object = None,
                        text: ValueText = value_text, listed: int | None = None) -> str:
    """Human label for one loop iteration; ``iteration`` counts from 0 in run order.

    ``listed`` is the value's place in the loop's list, which picks its pass
    name; it differs from ``iteration`` only when the loop is shuffled.
    """
    count = f"({iteration + 1}/{step.iterations})"
    if isinstance(step, RepeatStep):
        return f"{step.name or 'repeat'} {count}"
    title = step.name or step.setting.replace("_", " ")
    name = step.pass_name(iteration if listed is None else listed)
    if name:
        return f"{title}: {name} {count}"
    return f"{title} {text(step.setting, value, step.unit)} {count}"


def ramp_position_label(target: Assignment, value: float, index: int, count: int,
                        text: ValueText = value_text) -> str:
    """e.g. ``ramp 160 mA (3/5)``; ``index`` counts from 0."""
    return f"ramp {text(target.setting, value, target.unit)} ({index + 1}/{count})"


@dataclass(frozen=True, slots=True)
class PlannedSet:
    """Settings written together, in order, with no wait between."""
    writes: tuple[Assignment, ...]
    position: tuple[str, ...]
    trace: Trace
    name: str = ""
    #: True for one step of a ramp rather than a step's own settings.
    ramping: bool = False


@dataclass(frozen=True, slots=True)
class PlannedWait:
    wait: WaitStep
    position: tuple[str, ...]
    trace: Trace


@dataclass(frozen=True, slots=True)
class PlannedRamp:
    """Marks the start of a ramp; its steps follow as PlannedSet/PlannedWait items."""
    target: Assignment
    ramp: Ramp
    start: float
    steps: int
    position: tuple[str, ...]
    trace: Trace


@dataclass(frozen=True, slots=True)
class PlannedLoopOrder:
    """Marks the start of a shuffled loop: the order its values will run in."""
    loop: LoopStep
    #: Indexes into ``loop.values``, in run order.
    order: tuple[int, ...]
    position: tuple[str, ...]
    trace: Trace

    @property
    def values(self) -> tuple[object, ...]:
        return tuple(self.loop.values[i] for i in self.order)


PlanItem: TypeAlias = PlannedSet | PlannedWait | PlannedRamp | PlannedLoopOrder


class RampStartUnknown(ValueError):
    pass


def loop_order(step: LoopStep, seed: int, where: tuple[int, ...], occurrence: Trace) -> tuple[int, ...]:
    """The order a loop runs its values in, as indexes into ``step.values``.

    ``where`` is the loop's index path in the recipe and ``occurrence`` the
    passes of the loops around it, so a loop shuffled each time gets a new
    order at every outer pass, and one shuffled once reuses its order.
    Text seeds hash the same in every process, so an order is repeatable.
    """
    order = list(range(len(step.values)))
    if step.order is LoopOrder.SHUFFLE_ONCE:
        Random(f"{seed}|{where}").shuffle(order)
    elif step.order is LoopOrder.SHUFFLE_EACH:
        Random(f"{seed}|{where}|{occurrence}").shuffle(order)
    return tuple(order)


def has_shuffled_loops(steps: tuple[RecipeStep, ...]) -> bool:
    return any(isinstance(s, LoopStep) and s.order.shuffled for s in iter_steps(steps))


def plan(steps: tuple[RecipeStep, ...], *, text: ValueText = value_text, seed: int = 0) -> Iterator[PlanItem]:
    """Everything the recipe writes and waits for, in order.  Lazy, so a
    caller can stop early on a very long recipe.  ``seed`` sets the order of
    shuffled loops and changes nothing else."""
    yield from _walk(steps, (), (), {}, text, seed)


def _walk(steps, position, trace, last: dict, text, seed: int) -> Iterator[PlanItem]:
    for index, step in enumerate(steps):
        here_trace = (*trace, (index, 0))
        if isinstance(step, SetStep):
            # Settings are written together first; a ramp with a fixed start
            # jumps there with them.  Then each ramp runs, then the hold.
            instant = []
            for a in step.assignments:
                if a.ramp is None:
                    instant.append(a)
                elif a.ramp.start is not None:
                    instant.append(replace(a, value=a.ramp.start, ramp=None))
            if instant or step.name:
                yield PlannedSet(tuple(instant), position, here_trace, step.name)
                last.update((a.key, a.value) for a in instant)
            for a in step.assignments:
                if a.ramp is not None:
                    yield from _ramp(a, a.ramp, _start(a, a.ramp, last, step.name), position, here_trace, last, text)
            if step.hold_wait is not None:
                yield PlannedWait(step.hold_wait, position, here_trace)
        elif isinstance(step, WaitStep):
            yield PlannedWait(step, position, here_trace)
        elif isinstance(step, LoopStep):
            order = loop_order(step, seed, (*(i for i, _ in trace), index), trace)
            if step.order.shuffled:
                yield PlannedLoopOrder(step, order, position, (*trace, (index, order[0])))
            for i, listed in enumerate(order):
                value = step.values[listed]
                pass_trace = (*trace, (index, listed))
                here = (*position, loop_position_label(step, i, value, text, listed))
                target = step.assignment(value)
                if step.ramp is None:
                    yield PlannedSet((target,), here, pass_trace)
                    last[target.key] = value
                else:
                    if i == 0 and step.ramp.start is not None:
                        yield PlannedSet((step.assignment(step.ramp.start),), here, pass_trace)
                        last[target.key] = step.ramp.start
                    yield from _ramp(target, step.ramp, _start(target, step.ramp, last, step.name or "loop"),
                                     here, pass_trace, last, text)
                yield from _walk(step.steps, here, pass_trace, last, text, seed)
        else:
            for i in range(step.count):
                yield from _walk(step.steps, (*position, loop_position_label(step, i)), (*trace, (index, i)),
                                 last, text, seed)


def _start(target: Assignment, ramp: Ramp, last: dict, where: str) -> float:
    if not ramp.needs_start:
        return float(last.get(target.key, target.value))  # type: ignore[arg-type]  # A setpoint list ignores it.
    if target.key not in last:
        place = f" in {where!r}" if where else ""
        raise RampStartUnknown(
            f"The ramp of {target.device_id}.{target.setting}{place} has nothing to start from: nothing "
            "earlier in the recipe sets it. Set it in an earlier step, or give the ramp a fixed start.")
    return float(last[target.key])  # type: ignore[arg-type]


def _ramp(target, ramp: Ramp, start: float, position, trace, last: dict, text) -> Iterator[PlanItem]:
    path = ramp_path(ramp, start, target.value)  # type: ignore[arg-type]
    yield PlannedRamp(target, ramp, start, len(path), position, trace)
    holds = ramp_holds(path)
    for k, value in enumerate(path):
        here = (*position, ramp_position_label(target, value, k, len(path), text))
        yield PlannedSet((replace(target, value=value, ramp=None),), here, trace, ramping=True)
        last[target.key] = value
        if k < holds:  # The target itself is not held; the step carries on.
            yield PlannedWait(WaitStep(ramp.dwell_seconds, ramp.purpose, "Ramp hold"), here, trace)


def values_before(steps: tuple[RecipeStep, ...], key: tuple[int, ...], *,
                  seed: int = 0) -> dict[tuple[str, str], object]:
    """The value of every setting just before the step at ``key`` (its index path) first runs.

    Used by the editor to show where a ramp will start.  Planning stops at a
    ramp with an unknown start; what was known up to then is returned.
    """
    known: dict[tuple[str, str], object] = {}
    try:
        for item in plan(steps, seed=seed):
            if tuple(i for i, _ in item.trace[:len(key)]) == key:
                break
            if isinstance(item, PlannedSet):
                known.update((a.key, a.value) for a in item.writes)
    except ValueError:
        pass
    return known


# --- Checks -------------------------------------------------------------------------

def _mode_text(mode: PowerSupplyOperatingMode) -> str:
    return mode.value.replace("_", " ")


def power_supply_problems(recipe: Recipe, *, seed: int = 0) -> tuple[str, ...]:
    """Unsafe power-supply sequences, in the order they would happen.

    - Output may only turn on once the current mode's setpoint and limit
      have both been written.
    - Switching mode (writing the other mode's setting) while the output is
      on is refused, because the stale register would briefly act as the
      new mode's limit.  Turn the output off, set both, then turn it on.

    Checked in ``seed``'s order; the runner checks the order it will run.
    """
    mode: dict[str, PowerSupplyOperatingMode] = {}
    written: dict[str, set[str]] = {}
    output_on: dict[str, bool] = {}
    try:
        writes = [w for item in plan(recipe.steps, seed=seed) if isinstance(item, PlannedSet) for w in item.writes]
    except ValueError:
        return ()  # Reported by recipe_problems as its own problem.
    for step in writes:
        device = step.device_id
        step_mode = power_supply_mode_of(step.setting)
        if step_mode is not None:
            if mode.get(device) is not step_mode:
                if output_on.get(device):
                    return (f"{device} switches to {_mode_text(step_mode)} while its output is on; "
                            "turn the output off first",)
                mode[device] = step_mode
                written[device] = set()
            written[device].add("current_setpoint" if step.setting == CURRENT_DENSITY_SETTING else step.setting)
        elif step.setting == POWER_SUPPLY_OUTPUT_SETTING:
            if step.value is True and not output_on.get(device):
                current = mode.get(device)
                if current is None:
                    return (f"{device} output turns on before any setpoint or limit is set",)
                missing = [n for n in POWER_SUPPLY_MODE_SETTINGS[current] if n not in written[device]]
                if missing:
                    return (f"{device} output turns on in {_mode_text(current)} mode before "
                            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} set",)
            output_on[device] = step.value is True
    return ()


def recipe_problems(recipe: Recipe, *, seed: int = 0) -> tuple[str, ...]:
    """Everything that stops a recipe from running as written (in ``seed``'s order), first problem first."""
    try:
        for _item in plan(recipe.steps, seed=seed):
            pass
    except ValueError as error:
        return (str(error),)
    return power_supply_problems(recipe, seed=seed)
