"""A recipe as lines over time, for drawing: one line per setting it writes.

Read from the same ``plan()`` walk the runner executes, so the picture is
the run: loops unrolled, ramps stepped, shuffled loops in the seeded order.
Values stay in the recipe's own units; the screen converts for display.
"""

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass

from rig_control.recipes.model import RecipeStep, WaitPurpose
from rig_control.recipes.plan import PlannedSet, PlannedWait, plan

#: A step's place in the recipe tree: ``(i,)`` top level, ``(i, j)`` inside it.
StepKey = tuple[int, ...]

#: Stop walking a recipe after this many planned items; the graph says so.
ITEM_LIMIT = 50_000


@dataclass(frozen=True, slots=True)
class SettingLine:
    """One setting's value over time: it holds each value until the next point."""

    device_id: str
    setting: str
    unit: str
    #: (seconds from start, value), only where the value changes.
    points: tuple[tuple[float, object], ...]

    @property
    def varies(self) -> bool:
        """True when the recipe sets more than one value; otherwise it is only held."""
        return len({repr(value) for _t, value in self.points}) > 1

    def value_at(self, seconds: float) -> object | None:
        """The value in force at ``seconds``; None before the recipe first sets it."""
        index = bisect_right([t for t, _ in self.points], seconds) - 1
        return None if index < 0 else self.points[index][1]


@dataclass(frozen=True, slots=True)
class WaitSpan:
    start: float
    end: float
    purpose: WaitPurpose
    #: The step the wait belongs to (a Wait step, or a Set step's hold).
    step: StepKey


@dataclass(frozen=True, slots=True)
class RecipeGraph:
    lines: tuple[SettingLine, ...]
    waits: tuple[WaitSpan, ...]
    #: When each top-level step starts, as (seconds, step index).
    step_starts: tuple[tuple[float, int], ...]
    total_seconds: float
    #: True when the recipe was too long to walk in full.
    truncated: bool = False

    @property
    def varying(self) -> tuple[SettingLine, ...]:
        return tuple(line for line in self.lines if line.varies)

    @property
    def held(self) -> tuple[SettingLine, ...]:
        return tuple(line for line in self.lines if not line.varies)

    def step_at(self, seconds: float) -> StepKey | None:
        """The step running at ``seconds`` (its wait), for picking a step on the graph."""
        if not self.waits:
            return None
        index = bisect_right([span.start for span in self.waits], seconds) - 1
        return self.waits[max(index, 0)].step

    def spans_of(self, step: StepKey) -> tuple[tuple[float, float], ...]:
        """Where ``step`` (and anything inside it) holds, merged into runs."""
        runs: list[list[float]] = []
        for span in self.waits:
            if span.step[: len(step)] != step:
                continue
            if runs and abs(runs[-1][1] - span.start) < 1e-9:
                runs[-1][1] = span.end
            else:
                runs.append([span.start, span.end])
        return tuple((start, end) for start, end in runs)


def recipe_graph(steps: tuple[RecipeStep, ...], *, seed: int = 0,
                 item_limit: int = ITEM_LIMIT) -> RecipeGraph:
    """Raises ``RampStartUnknown`` (a ``ValueError``) if a ramp has nothing to start from."""
    return graph_from_items(plan(steps, seed=seed), item_limit=item_limit)


def graph_from_items(items: Iterable, *, item_limit: int = ITEM_LIMIT) -> RecipeGraph:
    points: dict[tuple[str, str], list[tuple[float, object]]] = {}
    units: dict[tuple[str, str], str] = {}
    waits: list[WaitSpan] = []
    step_starts: list[tuple[float, int]] = []
    elapsed = 0.0
    truncated = False
    for count, item in enumerate(items):
        if count >= item_limit:
            truncated = True
            break
        trace = getattr(item, "trace", ())
        if trace and (not step_starts or step_starts[-1][1] != trace[0][0]):
            step_starts.append((elapsed, trace[0][0]))
        if isinstance(item, PlannedSet):
            for write in item.writes:
                line = points.setdefault(write.key, [])
                units.setdefault(write.key, write.unit)
                if line and line[-1][0] == elapsed:
                    line[-1] = (elapsed, write.value)  # Several writes at one moment: the last counts.
                elif not line or line[-1][1] != write.value:
                    line.append((elapsed, write.value))
        elif isinstance(item, PlannedWait):
            duration = item.wait.duration_seconds
            step = tuple(index for index, _ in item.trace)
            if duration > 0:
                waits.append(WaitSpan(elapsed, elapsed + duration, item.wait.purpose, step))
            elapsed += duration
    lines = tuple(
        SettingLine(device_id, setting, units[(device_id, setting)], tuple(values))
        for (device_id, setting), values in points.items()
    )
    return RecipeGraph(lines, tuple(waits), tuple(step_starts), elapsed, truncated)
