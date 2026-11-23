"""Revising a running recipe: what may change, and what then runs.

A revision replaces what the run has still to do; nothing that has run is
redone.  The run finds its place in the revised recipe by step ID (see
``resume_plan``), so steps may be added, removed, moved or changed freely
wherever the run has yet to reach.  The rules are few:

- The step running now finishes as it started.  Changes to it apply from
  its next run (e.g. the next pass of its loop).
- The loops and repeats the run is inside keep their place: a loop keeps
  its setting, its order and the values it has already run (a shuffled
  loop keeps all its values, which make its order); a repeat keeps at
  least the passes it has started.
- A change must be able to take effect.  Changing or adding a step the run
  will not reach again would do nothing, so it is refused; to change a
  setting now, use an override.
- What is left must pass the same checks as a new recipe, judged from what
  has actually been written (e.g. a supply's output is already on).

All of this is worked out from a ``RunSnapshot`` without touching the run;
the runner then swaps the revision in only if the run has not moved on.
"""
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace

from rig_control.recipes.model import (
    Assignment, LoopStep, Recipe, RecipeStep, RepeatStep, SetStep, WaitStep, iter_steps,
)
from rig_control.recipes.plan import (
    PlanItem, PlannedSet, PlannedWait, ResumePointMissing, SupplySequence, Trace, Where, resume_plan,
)


class RevisionRefused(ValueError):
    """The revision cannot be applied; ``problems`` says why, first first."""
    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__(problems[0] if problems else "Revision refused")
        self.problems = problems


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """A running recipe as it stands, copied so it can be read off the run's thread."""
    recipe: Recipe
    seed: int
    #: Items started so far; a revision applies only if this has not changed.
    counter: int
    #: The step run in progress (its items' ``where``), or None before any.
    where: Where | None
    #: The same by index path, which is what a shuffled loop's order depends on.
    trace: Trace
    #: Items of the running step run not started yet, from the plan it started in.
    rest: tuple[PlanItem, ...]
    #: Every setting's last written value, as ``(device_id, setting) -> value``.
    written: Mapping[tuple[str, str], object]
    supply: SupplySequence
    #: Seconds left of the wait in progress.
    wait_left: float = 0.0
    revision: int = 0
    #: Settings pinned by an override, as ``(device_id, setting)``: the recipe's writes to them are skipped.
    pinned: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True, slots=True)
class RevisionChanges:
    """What a revision changes, in words, for the operator to confirm."""
    #: Steps changed that will run again, by title.
    changed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    #: Steps that would have run and now will not (deleted, or moved behind the run).
    removed: tuple[str, ...] = ()
    #: The running step, if it changed: it finishes as it is, then runs as changed.
    running_changed: str = ""
    end_state_changed: bool = False
    #: Seconds left after the revision, from the snapshot.
    remaining_seconds: float = 0.0

    @property
    def any(self) -> bool:
        return bool(self.changed or self.added or self.removed or self.running_changed or self.end_state_changed)


@dataclass(frozen=True, slots=True)
class Revision:
    recipe: Recipe
    seed: int
    #: The snapshot counter it was worked out from.
    based_on: int
    after: Where | None
    changes: RevisionChanges
    #: Every write left to run (rest of the running step, then the revised plan), for device checks.
    writes: tuple[Assignment, ...]

    def items(self, written: Callable[[], Mapping]) -> Iterator[PlanItem]:
        """The revised plan after the running step run; ``written`` is read when it is first needed."""
        return resume_plan(self.recipe.steps, self.after, written, seed=self.seed)


def step_title(step: RecipeStep) -> str:
    if step.name:
        return f"'{step.name}'"
    if isinstance(step, SetStep):
        return "Set " + ", ".join(f"{a.device_id} {a.setting.replace('_', ' ')}" for a in step.assignments)
    if isinstance(step, WaitStep):
        return f"{step.purpose.value.capitalize()} wait of {step.duration_seconds:g} s"
    if isinstance(step, LoopStep):
        return f"Loop over {step.device_id} {step.setting.replace('_', ' ')}"
    return f"Repeat {step.count}×"


def _shallow(step: RecipeStep) -> RecipeStep:
    """A step without its contents, so a loop is compared by its own settings only."""
    return replace(step, steps=()) if isinstance(step, (LoopStep, RepeatStep)) else step


def _chain(steps: tuple[RecipeStep, ...], where: Where) -> list[tuple[RecipeStep, int, int]] | None:
    """The steps along ``where``, outermost first, as ``(step, index in its list, pass)``."""
    chain = []
    for step_id, pass_index in where:
        found = next(((i, s) for i, s in enumerate(steps) if s.id == step_id), None)
        if found is None:
            return None
        index, step = found
        chain.append((step, index, pass_index))
        steps = step.steps if isinstance(step, (LoopStep, RepeatStep)) else ()
    return chain


def structure_problems(recipe: Recipe) -> tuple[str, ...]:
    for step in iter_steps(recipe.steps):
        if isinstance(step, (LoopStep, RepeatStep)) and not step.steps:
            name = "Repeat" if isinstance(step, RepeatStep) else f"Loop over {step.device_id}.{step.setting}"
            return (f"{name} has no steps inside it",)
    return ()


def _place_problems(snapshot: RunSnapshot, new: Recipe) -> list[str]:
    """The loops the run is inside, and the running step, must keep their place."""
    if snapshot.where is None:
        return []
    old_chain = _chain(snapshot.recipe.steps, snapshot.where)
    new_chain = _chain(new.steps, snapshot.where)
    if old_chain is None:  # pragma: no cover - the snapshot's own recipe always has it
        return ["The run's place could not be found in the running recipe"]
    if new_chain is None:
        # The outermost step on the run's path that is no longer where it was.
        missing = next(old for depth, (old, _, _) in enumerate(old_chain)
                       if _chain(new.steps, snapshot.where[:depth + 1]) is None)
        return [f"{step_title(missing)} is running, so it can't be deleted, or moved into or out of a loop"]
    problems = []
    old_indexes = tuple(i for _, i, _ in old_chain)
    for depth, ((old, _, pass_index), (step, _, _)) in enumerate(zip(old_chain, new_chain)):
        title = step_title(old)
        if type(step) is not type(old):
            problems.append(f"{title} is running, so it can't become a different kind of step")
        elif isinstance(old, LoopStep):
            assert isinstance(step, LoopStep)
            if (step.device_id, step.setting, step.unit) != (old.device_id, old.setting, old.unit):
                problems.append(f"{title} is running, so the setting it loops over can't change")
            elif step.order is not old.order:
                problems.append(f"{title} is running, so its order can't change")
            elif old.order.shuffled:
                # Its order comes from its values and its place in the recipe.
                if step.values != old.values:
                    problems.append(f"{title} is running and shuffled, so its values can't change: "
                                    "any change would reshuffle the passes already run")
                elif tuple(i for _, i, _ in new_chain[:depth + 1]) != old_indexes[:depth + 1]:
                    problems.append(f"{title} is running and shuffled, so no step can be added, removed or "
                                    "moved before it, or before a loop it is inside: its order depends on its place")
            elif step.values[:pass_index + 1] != old.values[:pass_index + 1]:
                problems.append(f"{title} is on pass {pass_index + 1}, so its first {pass_index + 1} "
                                f"value{'s' if pass_index else ''} can't change; change or add later values")
        elif isinstance(old, RepeatStep):
            assert isinstance(step, RepeatStep)
            if step.count <= pass_index:
                problems.append(f"{title} is on pass {pass_index + 1}, so it must repeat at least "
                                f"{pass_index + 1} times")
    return problems


def _tail_ids(items) -> set[str]:
    return {step_id for item in items for step_id, _ in item.where}


def prepare_revision(snapshot: RunSnapshot, new: Recipe) -> Revision:
    """Check ``new`` against the run and work out what it changes; raises ``RevisionRefused``."""
    problems = list(structure_problems(new))
    problems += _place_problems(snapshot, new)
    if problems:
        raise RevisionRefused(tuple(problems))

    # What will have been written when the running step finishes.
    rest_writes = [w for item in snapshot.rest if isinstance(item, PlannedSet) for w in item.writes]
    predicted = dict(snapshot.written)
    predicted.update((w.key, w.value) for w in rest_writes)
    try:
        tail = list(resume_plan(new.steps, snapshot.where, lambda: predicted, seed=snapshot.seed))
    except ResumePointMissing as error:  # pragma: no cover - ruled out by the place checks
        raise RevisionRefused((str(error),)) from error
    except ValueError as error:
        raise RevisionRefused((str(error),)) from error
    tail_writes = [w for item in tail if isinstance(item, PlannedSet) for w in item.writes]

    supply = snapshot.supply.copy()
    # Pinned settings are not written, so the checks follow what will actually be sent.
    supply.first_problem(w for w in rest_writes if w.key not in snapshot.pinned)
    problem = supply.first_problem(w for w in tail_writes if w.key not in snapshot.pinned)
    if problem:
        problems.append(problem)
    problems += pinned_loop_problems(new, _tail_ids(tail) | {i for i, _ in snapshot.where or ()}, snapshot.pinned)

    old_steps = {s.id: s for s in iter_steps(snapshot.recipe.steps)}
    new_steps = {s.id: s for s in iter_steps(new.steps)}
    will_run = _tail_ids(tail)
    try:
        would_have_run = _tail_ids(resume_plan(snapshot.recipe.steps, snapshot.where, lambda: predicted,
                                               seed=snapshot.seed))
    except ValueError:  # pragma: no cover - the running recipe always plans
        would_have_run = set(old_steps)
    running_id = snapshot.where[-1][0] if snapshot.where else None

    changed, added, running_changed = [], [], ""
    for step_id, step in new_steps.items():
        old = old_steps.get(step_id)
        if old is not None and _shallow(old) == _shallow(step):
            continue
        title = step_title(step)
        if step_id not in will_run:
            problems.append(
                f"{title} {'is added where the run has already passed' if old is None else 'has already run'} "
                "and won't run again, so this change would do nothing. To change a setting now, use an override.")
        elif old is None:
            added.append(title)
        elif step_id == running_id:
            running_changed = title
        else:
            changed.append(title)
    removed = [step_title(old_steps[i]) for i in old_steps if i in would_have_run and i not in will_run]
    if problems:
        raise RevisionRefused(tuple(problems))

    seconds = snapshot.wait_left + sum(i.wait.duration_seconds for i in (*snapshot.rest, *tail)
                                       if isinstance(i, PlannedWait))
    changes = RevisionChanges(tuple(changed), tuple(added), tuple(removed), running_changed,
                              new.end_state != snapshot.recipe.end_state, seconds)
    if not changes.any:
        raise RevisionRefused(("Nothing has changed from the recipe that is running",))
    return Revision(new, snapshot.seed, snapshot.counter, snapshot.where, changes,
                    tuple(rest_writes + tail_writes))


def pinned_loop_problems(recipe: Recipe, will_run: set[str], pinned) -> list[str]:
    """A loop over a pinned setting would run every pass at the pinned value."""
    return [f"{step_title(s)} steps through {s.device_id} {s.setting.replace('_', ' ')}, which is pinned by "
            "an override, so its passes would all be the same; release the pin first"
            for s in iter_steps(recipe.steps)
            if isinstance(s, LoopStep) and s.id in will_run and (s.device_id, s.setting) in pinned]


def override_problems(snapshot: RunSnapshot, pinned: frozenset[tuple[str, str]],
                      output: tuple[str, bool] | None = None) -> list[str]:
    """Whether what is left of the run stays safe with ``pinned`` settings skipped.

    ``output`` is a supply output an override turns on or off, as
    ``(device_id, on)``: the recipe's later writes are checked from there.
    """
    remaining = list(remaining_plan(snapshot))
    will_run = _tail_ids(remaining) | {i for i, _ in snapshot.where or ()}
    problems = pinned_loop_problems(snapshot.recipe, will_run, pinned)
    supply = snapshot.supply.copy()
    if output is not None:
        supply.output_on[output[0]] = output[1]
    problem = supply.first_problem(w for item in remaining if isinstance(item, PlannedSet) for w in item.writes
                                   if w.key not in pinned)
    if problem:
        problems.append(f"Later in the recipe, {problem}")
    return problems


def remaining_plan(snapshot: RunSnapshot, recipe: Recipe | None = None) -> Iterator[PlanItem]:
    """Everything left to run: the rest of the running step, then the plan after it.

    With ``recipe``, the plan after it is that recipe's, i.e. what would run
    if it were applied (unchecked; ``prepare_revision`` says if it can be).
    """
    predicted = dict(snapshot.written)
    predicted.update((w.key, w.value) for item in snapshot.rest if isinstance(item, PlannedSet) for w in item.writes)
    yield from snapshot.rest
    yield from resume_plan((recipe or snapshot.recipe).steps, snapshot.where, lambda: predicted, seed=snapshot.seed)


def finished_steps(snapshot: RunSnapshot) -> tuple[set[str], set[str]]:
    """Step IDs of the running recipe that will not run again, and those the run is inside (running now)."""
    inside = {step_id for step_id, _ in snapshot.where or ()}
    will_run = _tail_ids(remaining_plan(snapshot))
    return {s.id for s in iter_steps(snapshot.recipe.steps)} - will_run - inside, inside
