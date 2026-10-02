"""Counts, durations and bounded previews, all read from the one ``plan()``."""
from collections import defaultdict
from dataclasses import dataclass, field

from rig_control.recipes.model import Recipe, RecipeStep, WaitPurpose
from rig_control.recipes.plan import PlannedSet, PlannedWait, ValueText, plan, value_text


@dataclass(frozen=True, slots=True)
class RecipeEstimate:
    measure_waits: int
    settle_waits: int
    duration_seconds: float
    note: str = "Estimate excludes unpredictable communication overhead."


@dataclass(frozen=True, slots=True)
class PreviewRow:
    index: int
    planned_start_seconds: float
    kind: str
    label: str
    #: Loop position, outermost first, e.g. ``("current_setpoint 2 (2/3)",)``.
    position: tuple[str, ...] = ()
    #: Every target set so far, as ``(device_id, setting, value)``.
    parameters: tuple[tuple[str, str, object], ...] = ()


def estimate_recipe(recipe: Recipe) -> RecipeEstimate:
    """Raises ``RampStartUnknown`` if a ramp has nothing to start from."""
    counts = {WaitPurpose.MEASURE: 0, WaitPurpose.SETTLE: 0}
    total = 0.0
    for item in plan(recipe.steps):
        if isinstance(item, PlannedWait):
            counts[item.wait.purpose] += 1
            total += item.wait.duration_seconds
    return RecipeEstimate(counts[WaitPurpose.MEASURE], counts[WaitPurpose.SETTLE], total)


@dataclass(frozen=True, slots=True)
class StepTimes:
    """Planned seconds per step and per loop pass, keyed by the step's index path.

    A top-level step is ``(i,)``; the j-th step inside it is ``(i, j)``.
    Times include every pass, every ramp and every hold.
    """
    totals: dict[tuple[int, ...], float] = field(default_factory=dict)
    passes: dict[tuple[int, ...], list[float]] = field(default_factory=dict)

    def total(self, key: tuple[int, ...]) -> float:
        return self.totals.get(key, 0.0)


def step_times(steps: tuple[RecipeStep, ...]) -> StepTimes:
    """Raises ``RampStartUnknown`` if a ramp has nothing to start from."""
    totals: dict[tuple[int, ...], float] = defaultdict(float)
    passes: dict[tuple[int, ...], dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for item in plan(steps):
        if isinstance(item, PlannedWait):
            seconds = item.wait.duration_seconds
            for depth in range(1, len(item.trace) + 1):
                key = tuple(i for i, _ in item.trace[:depth])
                totals[key] += seconds
                passes[key][item.trace[depth - 1][1]] += seconds
    return StepTimes(dict(totals), {k: [v[i] for i in sorted(v)] for k, v in passes.items()})


def preview_recipe(recipe: Recipe, *, limit: int = 500, offset: int = 0,
                   text: ValueText = value_text) -> tuple[PreviewRow, ...]:
    """Return a bounded page of planned actions, preserving duplicate values.

    ``text`` formats values for labels; the builder passes one that shows its
    display units.  Parameters always keep the recipe's own values.
    Raises ``RampStartUnknown`` if a ramp has nothing to start from.
    """
    if limit <= 0 or offset < 0:
        raise ValueError("Preview limit must be positive and offset non-negative")
    rows: list[PreviewRow] = []
    elapsed = 0.0
    index = 0
    targets: dict[tuple[str, str], object] = {}
    for item in plan(recipe.steps, text=text):
        if len(rows) >= limit:
            break
        if isinstance(item, PlannedSet):
            targets.update((a.key, a.value) for a in item.writes)
            if not item.writes:
                continue
            parts = ", ".join(f"{a.device_id} {a.setting.replace('_', ' ')} = {text(a.setting, a.value, a.unit)}"
                              for a in item.writes)
            label = f"Ramp {parts}" if item.ramping else (f"{item.name}: " if item.name else "") + f"Set {parts}"
            kind = "set"
        elif isinstance(item, PlannedWait):
            label, kind = item.wait.title, item.wait.purpose.value
        else:
            continue
        if index >= offset:
            snapshot = tuple((d, s, v) for (d, s), v in targets.items())
            rows.append(PreviewRow(index, elapsed, kind, label, item.position, snapshot))
        index += 1
        if isinstance(item, PlannedWait):
            elapsed += item.wait.duration_seconds
    return tuple(rows)
