"""Revising a running recipe: the run carries on from where it is, as revised."""
import json
import threading
import time
from dataclasses import replace

import pytest

from rig_control.recipes import (
    Assignment, LoopOrder, LoopStep, Ramp, RampKind, Recipe, RepeatStep, SetStep, WaitPurpose, WaitStep,
)
from rig_control.recipes.execution import RecipeRunState
from rig_control.recipes.model import EndAction, EndDevice, EndState
from rig_control.recipes.plan import PlannedSet, plan, resume_plan
from rig_control.recipes.revision import RevisionRefused
from tests.test_recipes import make_runner

MEASURE, SETTLE = WaitPurpose.MEASURE, WaitPurpose.SETTLE


# --- The planner resumes exactly --------------------------------------------------------

def complex_recipes() -> list[Recipe]:
    ramp_up = Ramp(RampKind.STEP_COUNT, 30.0, count=3)
    return [
        Recipe("nested", (
            SetStep((Assignment("supply", "voltage_limit", 5.0), Assignment("supply", "current_setpoint", 0.1))),
            LoopStep("supply", "current_setpoint", (0.2, 0.4, 0.6), steps=(
                LoopStep("mfc", "flow", (10.0, 20.0, 30.0), order=LoopOrder.SHUFFLE_EACH,
                         steps=(WaitStep(60, SETTLE), WaitStep(120, MEASURE))),
                RepeatStep(2, (WaitStep(30, MEASURE, "inner"),)),
            ), ramp=Ramp(RampKind.STEP_SIZE, 10.0, step=0.1)),
        )),
        Recipe("ramps", (
            SetStep.one("mfc", "flow", 5.0),
            SetStep((Assignment("mfc", "flow", 50.0, ramp=ramp_up),), "Flow up", WaitStep(600, SETTLE)),
            LoopStep("mfc", "flow", (20.0, 40.0), order=LoopOrder.SHUFFLE_ONCE, steps=(
                SetStep((Assignment("supply", "voltage", 2.0, ramp=Ramp(RampKind.STEP_COUNT, 5.0, start=0.0,
                                                                        count=2)),)),
                WaitStep(60, MEASURE),
            )),
        )),
    ]


@pytest.mark.parametrize("recipe", complex_recipes(), ids=lambda r: r.name)
def test_resuming_an_unchanged_recipe_after_any_step_run_gives_exactly_the_rest_of_the_plan(recipe):
    for seed in (0, 7):
        items = list(plan(recipe.steps, seed=seed))
        k = 0
        while k < len(items):
            where = items[k].where
            end = k
            while end + 1 < len(items) and items[end + 1].where == where:
                end += 1
            assert where not in {i.where for i in items[end + 1:]}  # One step run is one block.
            written = {}
            for item in items[:end + 1]:
                if isinstance(item, PlannedSet):
                    written.update((a.key, a.value) for a in item.writes)
            assert list(resume_plan(recipe.steps, where, lambda: written, seed=seed)) == items[end + 1:]
            k = end + 1
        assert list(resume_plan(recipe.steps, None, dict, seed=seed)) == items


def test_ramps_after_the_resume_point_start_from_what_was_actually_written():
    recipe = Recipe("r", (SetStep.one("mfc", "flow", 10.0), WaitStep(60),
                          SetStep((Assignment("mfc", "flow", 40.0, ramp=Ramp(RampKind.STEP_COUNT, 5.0, count=3)),))))
    after = next(plan(recipe.steps)).where  # The first Set.
    actual = {("mfc", "flow"): 25.0}  # e.g. an override after it.
    rest = list(resume_plan(recipe.steps, after, lambda: actual))
    assert [a.value for i in rest if isinstance(i, PlannedSet) for a in i.writes] == [30.0, 35.0, 40.0]


# --- The runner takes a revision mid-run -------------------------------------------------

class Gate:
    """A sleeper that stops the run in the wait named ``at`` until released."""
    def __init__(self, runner_box: list, at: str) -> None:
        self.at, self.box = at, runner_box
        self.reached, self.release = threading.Event(), threading.Event()

    def __call__(self, _seconds: float) -> None:
        runner = self.box[0]
        if not self.release.is_set() and runner.status.active_step == self.at:
            self.reached.set()
            self.release.wait(5)


def start_gated(recipe: Recipe, at: str):
    box: list = []
    runner, supply, mfc = make_runner()
    gate = Gate(box, at)
    runner._sleeper = gate
    box.append(runner)
    runner.start(recipe)
    assert gate.reached.wait(5), "the run never reached the gate"
    return runner, gate


def revise_at_gate(runner, gate, recipe: Recipe):
    """Revise while the run waits at the gate; returns the changes or the exception."""
    outcome: list = []

    def call():
        try:
            outcome.append(runner.revise(recipe))
        except Exception as error:
            outcome.append(error)
    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.monotonic() + 5
    while not runner._requests and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.001)
    gate.release.set()
    thread.join(5)
    return outcome[0]


def flows(runner) -> list[float]:
    return [r.command.flow for r in runner._control.history if hasattr(r.command, "flow")]


def sweep() -> Recipe:
    return Recipe("sweep", (
        SetStep.one("supply", "voltage", 1.0),
        LoopStep("mfc", "flow", (10.0, 20.0, 30.0), steps=(WaitStep(60, MEASURE, "hold"),)),
        WaitStep(60, SETTLE, "after"),
    ))


def test_later_loop_values_can_change_and_be_added_while_the_loop_runs():
    recipe = sweep()
    runner, gate = start_gated(recipe, "hold")  # First pass, 10 sccm.
    loop = recipe.steps[1]
    revised = replace(recipe, steps=(recipe.steps[0], replace(loop, values=(10.0, 25.0, 35.0, 45.0)), recipe.steps[2]))
    changes = revise_at_gate(runner, gate, revised)
    assert not isinstance(changes, Exception), changes
    assert changes.changed == ("Loop over mfc flow",)
    result = runner.join(5)
    assert result.state is RecipeRunState.COMPLETED
    assert flows(runner) == [10.0, 25.0, 35.0, 45.0]
    assert runner.status.revision == 1


def test_the_running_step_finishes_as_it_started_and_changes_apply_from_its_next_run():
    recipe = Recipe("r", (
        LoopStep("mfc", "flow", (10.0, 20.0), steps=(
            SetStep.one("supply", "voltage", 1.0, name="volts"),
            WaitStep(60, MEASURE, "hold"),
        )),
    ))
    runner, gate = start_gated(recipe, "hold")
    loop = recipe.steps[0]
    hold = replace(loop.steps[1], duration_seconds=7200.0)
    volts = replace(loop.steps[0], assignments=(Assignment("supply", "voltage", 2.0),))
    revised = replace(recipe, steps=(replace(loop, steps=(volts, hold)),))
    changes = revise_at_gate(runner, gate, revised)
    assert changes.running_changed == "'hold'" and changes.changed == ("'volts'",)
    # 60 s left of this hold at most, then one pass of 7200 s.
    assert 7200 < changes.remaining_seconds <= 7260
    runner.join(5)
    voltages = [r.command.voltage for r in runner._control.history if hasattr(r.command, "voltage")]
    assert voltages == [1.0, 2.0]


def test_a_change_to_a_step_that_has_already_run_is_refused_with_a_pointer_to_overrides():
    recipe = sweep()
    runner, gate = start_gated(recipe, "after")
    first = replace(recipe.steps[0], assignments=(Assignment("supply", "voltage", 3.0),))
    refused = revise_at_gate(runner, gate, replace(recipe, steps=(first, *recipe.steps[1:])))
    assert isinstance(refused, RevisionRefused)
    assert "has already run" in str(refused) and "override" in str(refused)
    runner.join(5)
    assert runner.status.revision == 0


def test_values_a_loop_has_already_run_are_locked():
    recipe = sweep()
    runner, gate = start_gated(recipe, "hold")
    loop = recipe.steps[1]
    refused = revise_at_gate(runner, gate, replace(recipe, steps=(
        recipe.steps[0], replace(loop, values=(15.0, 20.0, 30.0)), recipe.steps[2])))
    assert isinstance(refused, RevisionRefused) and "can't change" in str(refused)
    runner.join(5)
    assert flows(runner) == [10.0, 20.0, 30.0]


def test_the_running_step_cannot_be_deleted():
    recipe = sweep()
    runner, gate = start_gated(recipe, "after")
    refused = revise_at_gate(runner, gate, replace(recipe, steps=recipe.steps[:2]))
    assert isinstance(refused, RevisionRefused) and "is running" in str(refused)
    runner.join(5)


def test_steps_can_be_added_and_deleted_ahead_and_the_end_state_changed():
    recipe = sweep()
    runner, gate = start_gated(recipe, "hold")
    end = EndState((EndDevice("mfc", EndAction.LEAVE),))
    revised = replace(recipe, steps=(*recipe.steps[:2], SetStep.one("mfc", "flow", 5.0, name="purge")), end_state=end)
    changes = revise_at_gate(runner, gate, revised)
    assert changes.added == ("'purge'",) and changes.removed == ("'after'",) and changes.end_state_changed
    runner.join(5)
    assert flows(runner)[-1] == 5.0
    # LEAVE kept the purge flow rather than sending the MFC to safe state.
    safe = [r.command.device_id for r in runner._control.history if type(r.command).__name__ == "EnterDeviceSafeState"]
    assert "mfc" not in safe and "supply" in safe


def test_a_revision_is_checked_against_what_has_actually_been_written():
    recipe = Recipe("r", (
        SetStep((Assignment("supply", "voltage", 1.0), Assignment("supply", "current_limit", 0.5),
                 Assignment("supply", "output_enabled", True))),
        WaitStep(60, MEASURE, "hold"),
    ))
    runner, gate = start_gated(recipe, "hold")
    # Switching to constant current while the output is on is unsafe.
    revised = replace(recipe, steps=(*recipe.steps, SetStep.one("supply", "current_setpoint", 0.2)))
    refused = revise_at_gate(runner, gate, revised)
    assert isinstance(refused, RevisionRefused) and "while its output is on" in str(refused)
    runner.join(5)


def test_limits_are_checked_for_the_revised_steps():
    recipe = sweep()
    runner, gate = start_gated(recipe, "hold")
    loop = recipe.steps[1]
    refused = revise_at_gate(runner, gate, replace(recipe, steps=(
        recipe.steps[0], replace(loop, values=(10.0, 20.0, 500.0)), recipe.steps[2])))
    assert isinstance(refused, RevisionRefused) and "exceeds" in str(refused)
    runner.join(5)


def test_a_revision_is_recorded_with_the_recipe_it_applied():
    events = []
    recipe = sweep()
    box: list = []
    runner, _supply, _mfc = make_runner()
    runner._event_sink = events.append
    gate = Gate(box, "hold"); runner._sleeper = gate; box.append(runner)
    runner.start(recipe)
    assert gate.reached.wait(5)
    loop = recipe.steps[1]
    revise_at_gate(runner, gate, replace(recipe, steps=(recipe.steps[0], replace(loop, values=(10.0, 50.0)),
                                                        recipe.steps[2])))
    runner.join(5)
    revised = [json.loads(e.message) for e in events if '"recipe_revised"' in e.message]
    assert len(revised) == 1
    assert json.loads(revised[0]["recipe_snapshot_json"])["steps"][1]["values"] == [10.0, 50.0]
    assert revised[0]["changed"] == ["Loop over mfc flow"]


def test_nothing_changed_is_refused_and_no_run_is_an_error():
    recipe = sweep()
    runner, gate = start_gated(recipe, "hold")
    refused = revise_at_gate(runner, gate, recipe)
    assert isinstance(refused, RevisionRefused) and "Nothing has changed" in str(refused)
    runner.join(5)
    with pytest.raises(RuntimeError):
        runner.revise(recipe)


def test_a_shuffled_loop_that_is_running_keeps_its_values():
    recipe = Recipe("r", (LoopStep("mfc", "flow", (10.0, 20.0, 30.0), order=LoopOrder.SHUFFLE_ONCE,
                                   steps=(WaitStep(60, MEASURE, "hold"),)),))
    runner, gate = start_gated(recipe, "hold")
    loop = recipe.steps[0]
    refused = revise_at_gate(runner, gate, replace(recipe, steps=(replace(loop, values=(10.0, 20.0, 30.0, 40.0)),)))
    assert isinstance(refused, RevisionRefused) and "shuffled" in str(refused)
    runner.join(5)
