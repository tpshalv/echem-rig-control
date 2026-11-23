"""Operator overrides while a recipe runs: until the recipe next sets it, or pinned."""
import json
import threading
import time

from rig_control.control.commands import CommandSource, SetMfcFlow, SetPowerSupplyOutput
from rig_control.control.service import ControlAccessError
from rig_control.recipes import Assignment, LoopStep, Recipe, SetStep, WaitPurpose, WaitStep
from rig_control.recipes.revision import RevisionRefused
from tests.test_recipe_revision import Gate
from tests.test_recipes import make_runner

MEASURE = WaitPurpose.MEASURE


def gated(recipe: Recipe, *gates: str):
    """Start a run that stops at each named wait in turn; ``step()`` moves it to the next."""
    events: list = []
    box: list = []
    runner, _supply, _mfc = make_runner()
    runner._event_sink = events.append
    order = list(gates)
    gate = Gate(box, order.pop(0))
    runner._sleeper = gate
    box.append(runner)
    runner.start(recipe)
    assert gate.reached.wait(5)

    def call(fn):
        """Call ``fn`` (which needs the run's thread) while the run waits at the gate."""
        out: list = []
        thread = threading.Thread(target=lambda: out.append(_catch(fn)))
        thread.start()
        deadline = time.monotonic() + 5
        while not runner._requests and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.001)
        # Let the run serve the request without leaving the wait.
        while thread.is_alive() and time.monotonic() < deadline:
            runner._serve_requests()
            time.sleep(0.001)
        return out[0]

    def step():
        nonlocal gate
        if order:
            nxt = Gate(box, order.pop(0))
            runner._sleeper = nxt
            gate.release.set()
            assert nxt.reached.wait(5)
            gate = nxt
        else:
            gate.release.set()
    return runner, events, call, step


def _catch(fn):
    try:
        return fn()
    except Exception as error:
        return error


def flows(runner):
    return [(r.command.flow, r.command.source) for r in runner._control.history if hasattr(r.command, "flow")]


def kinds(events):
    return [json.loads(e.message)["kind"] for e in events]


RECIPE = Recipe("r", (
    SetStep.one("mfc", "flow", 10.0), WaitStep(60, MEASURE, "first"),
    SetStep.one("mfc", "flow", 20.0), WaitStep(60, MEASURE, "second"),
))


def test_an_override_lasts_until_the_recipe_next_sets_it():
    runner, events, call, step = gated(RECIPE, "first", "second")
    summary = call(lambda: runner.override(SetMfcFlow("mfc", 15.0, CommandSource.MANUAL), pin=False))
    assert "until the recipe next sets it" in summary
    step(); step()
    runner.join(5)
    assert flows(runner) == [(10.0, CommandSource.RECIPE), (15.0, CommandSource.RECIPE_OVERRIDE),
                             (20.0, CommandSource.RECIPE)]
    assert "recipe_override" in kinds(events)


def test_a_pin_skips_the_recipes_writes_until_released_and_restore_sets_its_value():
    runner, events, call, step = gated(RECIPE, "first", "second")
    call(lambda: runner.override(SetMfcFlow("mfc", 15.0, CommandSource.MANUAL), pin=True))
    step()  # The recipe's 20 sccm is skipped.
    assert [p.value for p in runner.pins] == ["15 sccm"]
    pin = runner.pins[0]
    assert pin.skipped is not None and pin.skipped.value == 20.0
    message = call(lambda: runner.release(pin, restore=True))
    assert "20" in message
    step()
    runner.join(5)
    assert [f for f, _ in flows(runner)] == [10.0, 15.0, 20.0]
    assert kinds(events).count("recipe_override_skipped") == 1
    assert "recipe_override_released" in kinds(events)
    assert runner.pins == ()


def test_a_loops_own_setting_cannot_be_pinned():
    recipe = Recipe("r", (LoopStep("mfc", "flow", (10.0, 20.0), steps=(WaitStep(60, MEASURE, "hold"),)),))
    runner, _events, call, step = gated(recipe, "hold")
    refused = call(lambda: runner.override(SetMfcFlow("mfc", 15.0, CommandSource.MANUAL), pin=True))
    assert isinstance(refused, RevisionRefused) and "pinned" in str(refused)
    step(); runner.join(5)


def test_a_pinned_setting_refuses_a_revision_that_loops_over_it():
    runner, _events, call, step = gated(RECIPE, "first")
    call(lambda: runner.override(SetMfcFlow("mfc", 15.0, CommandSource.MANUAL), pin=True))
    revised = Recipe("r", (*RECIPE.steps[:2], LoopStep("mfc", "flow", (1.0, 2.0), steps=(WaitStep(1),))))
    refused = _catch(lambda: runner.check_revision(revised))
    assert isinstance(refused, RevisionRefused) and "release the pin" in str(refused)
    step(); runner.join(5)


def test_turning_a_supply_on_is_refused_if_the_recipe_later_switches_its_mode():
    recipe = Recipe("r", (
        SetStep((Assignment("supply", "voltage", 1.0), Assignment("supply", "current_limit", 0.5))),
        WaitStep(60, MEASURE, "hold"),
        SetStep((Assignment("supply", "current_setpoint", 0.2), Assignment("supply", "voltage_limit", 2.0))),
    ))
    runner, _events, call, step = gated(recipe, "hold")
    refused = call(lambda: runner.override(SetPowerSupplyOutput("supply", True, CommandSource.MANUAL), pin=False))
    assert isinstance(refused, RevisionRefused) and "while its output is on" in str(refused)
    step(); runner.join(5)


def test_override_commands_are_refused_without_a_recipe():
    runner, _supply, _mfc = make_runner()
    try:
        runner._control.execute(SetMfcFlow("mfc", 1.0, CommandSource.RECIPE_OVERRIDE))
    except ControlAccessError:
        pass
    else:
        raise AssertionError("an override command ran with no recipe")


# --- Next step -------------------------------------------------------------------------

def test_next_step_ends_the_running_wait_and_goes_on():
    runner, events, call, step = gated(RECIPE, "first", "second")
    message = call(lambda: runner.skip_step())
    assert "first" in message
    # Released at once: the run reaches the second wait without the gate being opened.
    step(); step()
    runner.join(5)
    assert [f for f, _ in flows(runner)] == [10.0, 20.0]
    assert kinds(events).count("recipe_step_skipped") == 1
    waits = [json.loads(e.message)["detail"] for e in events if json.loads(e.message)["kind"] == "recipe_wait"]
    assert waits == ["first", "second"]


def test_next_step_during_a_ramp_jumps_to_its_target_and_skips_its_holds():
    from rig_control.recipes import Ramp, RampKind
    recipe = Recipe("r", (
        SetStep.one("mfc", "flow", 0.0),
        SetStep((Assignment("mfc", "flow", 40.0, ramp=Ramp(RampKind.STEP_COUNT, 60.0, count=4)),), "up"),
        WaitStep(60, MEASURE, "after"),
    ))
    runner, events, call, step = gated(recipe, "Ramp hold", "after")
    call(lambda: runner.skip_step())
    step(); step()
    runner.join(5)
    assert [f for f, _ in flows(runner)] == [0.0, 10.0, 20.0, 30.0, 40.0]
    holds = [e for e in events if json.loads(e.message)["kind"] == "recipe_wait"
             and json.loads(e.message)["detail"] == "Ramp hold"]
    assert len(holds) == 1  # Only the one that was running.


def test_manual_changes_go_to_the_recipe_as_overrides_only_when_chosen():
    from rig_control.ui.manual_control.model import ManualControlViewModel
    runner, _events, call, step = gated(RECIPE, "first")
    manual = ManualControlViewModel(runner._devices, runner._control)
    mode = {"value": "blocked"}
    manual.override_router = lambda command: (
        None if mode["value"] == "blocked" else runner.override(command, pin=mode["value"] == "pin"))
    blocked = manual.set_mfc_flow("mfc", 15.0)
    assert not blocked.succeeded and "override" in blocked.summary
    mode["value"] = "pin"
    sent = call(lambda: manual.set_mfc_flow("mfc", 15.0))
    assert sent.succeeded and "pinned" in sent.summary
    assert [p.name for p in runner.pins] == ["mfc flow"]
    step(); runner.join(5)
