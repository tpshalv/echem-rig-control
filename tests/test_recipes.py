import gc
import json

import pytest

from rig_control.control.commands import SetPowerSupplyCurrentLimit, SetPowerSupplyOutput, SetPowerSupplyVoltage
from rig_control.control.service import ControlMode, RigControlService
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_mfc import SimulatedMassFlowController
from rig_control.devices.simulated_power_supply import SimulatedPowerSupply
from rig_control.recipes import (
    Assignment, LoopStep, Recipe, RepeatStep, SetStep, WaitPurpose, WaitStep, estimate_recipe, power_supply_problems,
    preview_recipe, recipe_from_dict, recipe_to_dict,
)
from rig_control.recipes.execution import RecipeRunState, RecipeRunner, RecipeValidationError
from rig_control.data.in_memory_writer import InMemoryExperimentWriter
from rig_control.experiment_recording import ExperimentRecorder

SETTLE, MEASURE = WaitPurpose.SETTLE, WaitPurpose.MEASURE
ON = SetStep.one("supply", "output_enabled", True)
OFF = SetStep.one("supply", "output_enabled", False)


def make_runner():
    # Tk objects left by earlier UI tests must not be collected on the
    # recipe thread: Tk blocks when called off the main thread.
    gc.collect()
    manager = DeviceManager()
    supply = SimulatedPowerSupply("supply", PowerSupplyLimits(30, 10, 100))
    mfc = SimulatedMassFlowController("mfc", MassFlowControllerLimits(100, "sccm"))
    supply.connect(); mfc.connect(); manager.register(supply); manager.register(mfc)
    return RecipeRunner(RigControlService(manager), manager, sleeper=lambda _seconds: None), supply, mfc


def run(recipe: Recipe):
    runner, _supply, _mfc = make_runner()
    runner.start(recipe)
    result = runner.join(2)
    assert result is not None
    return runner, result


def commands(runner, attribute):
    return [getattr(r.command, attribute) for r in runner._control.history if hasattr(r.command, attribute)]


def test_nested_loops_run_inner_loop_fully_per_outer_value_and_keep_repeats():
    recipe = Recipe("order", steps=(
        SetStep.one("supply", "voltage", 3),
        LoopStep("mfc", "flow", (1, 1), steps=(
            WaitStep(10, SETTLE),
            LoopStep("supply", "current_limit", (2, 4), steps=(WaitStep(0),)),
        )),
    ))
    runner, result = run(recipe)
    assert result.state is RecipeRunState.COMPLETED
    assert commands(runner, "flow") == [1, 1]
    assert commands(runner, "current") == [2, 4, 2, 4]
    assert commands(runner, "voltage") == [3]
    assert runner._control.mode is ControlMode.IDLE


def test_steps_between_loops_run_at_their_own_level():
    recipe = Recipe("levels", steps=(
        LoopStep("mfc", "flow", (1, 2), steps=(
            SetStep.one("supply", "voltage", 1),
            LoopStep("supply", "current_limit", (5, 6), steps=(WaitStep(0),)),
            SetStep.one("supply", "voltage", 0),
        )),
    ))
    runner, result = run(recipe)
    assert result.state is RecipeRunState.COMPLETED
    assert commands(runner, "voltage") == [1, 0, 1, 0]


def test_repeat_runs_its_steps_count_times():
    runner, result = run(Recipe("repeat", steps=(RepeatStep(3, steps=(SetStep.one("mfc", "flow", 5), WaitStep(0))),)))
    assert result.state is RecipeRunState.COMPLETED
    assert commands(runner, "flow") == [5, 5, 5]


def test_estimate_counts_measurements_and_settles_through_nesting():
    recipe = Recipe("estimate", steps=(
        WaitStep(100, SETTLE),
        LoopStep("mfc", "flow", (1, 2, 3), steps=(
            WaitStep(10, SETTLE),
            RepeatStep(2, steps=(WaitStep(5, MEASURE),)),
        )),
    ))
    estimate = estimate_recipe(recipe)
    assert (estimate.measure_waits, estimate.settle_waits, estimate.duration_seconds) == (6, 4, 100 + 3 * (10 + 2 * 5))


def test_preview_shows_loop_position_and_targets_and_is_bounded():
    recipe = Recipe("targets", steps=(
        SetStep.one("supply", "voltage", 2),
        LoopStep("mfc", "flow", (10, 20), steps=(WaitStep(1, MEASURE, "sample"),)),
    ))
    rows = preview_recipe(recipe)
    waits = [row for row in rows if row.kind == "measure"]
    assert [row.label for row in waits] == ["sample", "sample"]
    assert waits[1].position == ("flow 20 (2/2)",)
    assert waits[1].planned_start_seconds == 1
    assert waits[0].parameters == (("supply", "voltage", 2.0), ("mfc", "flow", 10.0))
    assert len(preview_recipe(recipe, limit=2)) == 2


def test_wait_events_record_purpose_and_loop_position():
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(Recipe("recorded", steps=(
        LoopStep("mfc", "flow", (7,), steps=(WaitStep(0, SETTLE, "stabilise"), WaitStep(0, MEASURE))),
    )), recording_root="unused")
    assert runner.join(2).state is RecipeRunState.COMPLETED
    assert '"name": "recorded"' in writer.metadata.extra["recipe_snapshot_json"]
    waits = [json.loads(e.message) for e in writer.events if '"recipe_wait"' in e.message]
    assert [(w["purpose"], w["detail"], w["position"]) for w in waits] == [
        ("settle", "stabilise", ["flow 7 (1/1)"]), ("measure", "Measure", ["flow 7 (1/1)"]),
    ]
    assert writer.is_open is False


def test_stop_attempts_safe_state():
    runner, supply, _mfc = make_runner()
    runner._sleeper = lambda _seconds: runner.stop()
    runner.start(Recipe("stop", steps=(SetStep.one("supply", "voltage", 8), WaitStep(1))))
    result = runner.join(2)
    assert result is not None and result.state is RecipeRunState.STOPPED
    assert supply.voltage_setpoint == 0


def test_command_failure_stops_progression_and_enters_safe_state():
    runner, supply, _mfc = make_runner()
    def fail_voltage(_voltage):
        raise OSError("simulated command failure")
    supply.set_voltage = fail_voltage  # type: ignore[method-assign]
    runner.start(Recipe("fails", steps=(SetStep.one("supply", "voltage", 2),)))
    result = runner.join(2)
    assert result is not None and result.state is RecipeRunState.FAILED
    assert "simulated command failure" in (result.failure or "")


def test_round_trip_and_old_format_is_refused_clearly():
    recipe = Recipe("rt", description="d", steps=(
        SetStep.one("mfc", "flow", 3, "sccm"),
        LoopStep("supply", "voltage", (1, 2), "V", steps=(RepeatStep(2, steps=(WaitStep(5, SETTLE, "x"),)),)),
    ))
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(recipe)))) == recipe
    with pytest.raises(ValueError, match="version 1 is not supported"):
        recipe_from_dict({"version": 1, "name": "old", "fixed": []})


def test_empty_loop_is_rejected_at_validation():
    runner, _supply, _mfc = make_runner()
    with pytest.raises(RecipeValidationError, match="no steps inside"):
        runner.validate(Recipe("empty", steps=(LoopStep("mfc", "flow", (1,)),)))


# --- Power-supply modes come from the setting written ---

def chrono_amp_then_potentiometry():
    return Recipe("CA then CP", steps=(
        SetStep.one("supply", "voltage", 1.5), SetStep.one("supply", "current_limit", 2), ON,
        LoopStep("supply", "voltage", (1.5, 1.8), steps=(WaitStep(0, MEASURE),)),
        OFF,
        SetStep.one("supply", "voltage_limit", 3), SetStep.one("supply", "current_setpoint", 0.5), ON,
        LoopStep("supply", "current_setpoint", (0.5, 1), steps=(WaitStep(0, MEASURE),)),
        OFF,
    ))


def test_recipe_can_switch_supply_mode_with_output_off():
    recipe = chrono_amp_then_potentiometry()
    assert power_supply_problems(recipe) == ()
    runner, result = run(recipe)
    assert result.state is RecipeRunState.COMPLETED
    kinds = [type(r.command) for r in runner._control.history]
    assert kinds[:3] == [SetPowerSupplyVoltage, SetPowerSupplyCurrentLimit, SetPowerSupplyOutput]


def test_mode_changes_are_recorded_as_events():
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(chrono_amp_then_potentiometry(), recording_root="unused")
    assert runner.join(2).state is RecipeRunState.COMPLETED
    details = [json.loads(e.message)["detail"] for e in writer.events if '"power_supply_mode"' in e.message]
    assert details == ["supply=constant_voltage", "supply=constant_current"]


def test_switching_mode_with_output_on_is_refused():
    recipe = Recipe("unsafe", steps=(
        SetStep.one("supply", "voltage", 1), SetStep.one("supply", "current_limit", 1), ON,
        SetStep.one("supply", "current_setpoint", 1),
    ))
    assert "while its output is on" in power_supply_problems(recipe)[0]
    runner, _supply, _mfc = make_runner()
    with pytest.raises(RecipeValidationError, match="while its output is on"):
        runner.validate(recipe)


def test_output_needs_both_settings_of_the_current_mode():
    missing_limit = Recipe("r", steps=(SetStep.one("supply", "current_setpoint", 1), ON))
    assert "before voltage_limit is set" in power_supply_problems(missing_limit)[0]
    nothing = Recipe("r", steps=(ON,))
    assert "before any setpoint" in power_supply_problems(nothing)[0]
    # After a mode switch, the old mode's settings no longer count.
    switched = Recipe("r", steps=(SetStep.one("supply", "voltage", 1), SetStep.one("supply", "current_limit", 1),
                                  SetStep.one("supply", "current_setpoint", 1), ON))
    assert "before voltage_limit is set" in power_supply_problems(switched)[0]


def test_loop_second_pass_is_checked_for_mode_problems():
    # Pass 1 is fine; pass 2 starts with the output on and switches to CV.
    recipe = Recipe("r", steps=(RepeatStep(2, steps=(
        SetStep.one("supply", "current_setpoint", 1), SetStep.one("supply", "voltage_limit", 5), ON,
        SetStep.one("supply", "voltage", 1), )),))
    assert "while its output is on" in power_supply_problems(recipe)[0]


def test_supply_values_keep_configured_maximum_checks():
    runner, _supply, _mfc = make_runner()
    with pytest.raises(RecipeValidationError, match="exceeds configured maximum 10"):
        runner.validate(Recipe("r", steps=(LoopStep("supply", "current_setpoint", (1, 11), steps=(WaitStep(0),)),)))
    with pytest.raises(RecipeValidationError, match="exceeds configured maximum 30"):
        runner.validate(Recipe("r", steps=(SetStep.one("supply", "voltage_limit", 31),)))
    with pytest.raises(RecipeValidationError, match="PowerSupply"):
        runner.validate(Recipe("r", steps=(SetStep.one("mfc", "voltage", 1),)))


# --- One Set step can write several settings ---

def test_one_set_step_writes_its_settings_in_listed_order():
    recipe = Recipe("multi", steps=(SetStep((
        Assignment("supply", "current_setpoint", 2), Assignment("supply", "voltage_limit", 5),
        Assignment("mfc", "flow", 30), Assignment("supply", "output_enabled", True),
    )), WaitStep(0), OFF))
    assert power_supply_problems(recipe) == ()
    runner, result = run(recipe)
    assert result.state is RecipeRunState.COMPLETED
    kinds = [type(r.command).__name__ for r in runner._control.history]
    assert kinds[:4] == ["SetPowerSupplyCurrentLimit", "SetPowerSupplyVoltage", "SetMfcFlow", "SetPowerSupplyOutput"]
    set_rows = [row for row in preview_recipe(recipe) if row.kind == "set"]
    assert set_rows[0].label == ("Set supply current setpoint = 2, supply voltage limit = 5, "
                                 "mfc flow = 30, supply output enabled = On")


def test_order_inside_a_set_step_is_checked():
    recipe = Recipe("r", steps=(SetStep((
        Assignment("supply", "current_setpoint", 2), Assignment("supply", "output_enabled", True),
        Assignment("supply", "voltage_limit", 5),
    )),))
    assert "before voltage_limit is set" in power_supply_problems(recipe)[0]


def test_set_step_rejects_the_same_setting_twice_and_empty_steps():
    with pytest.raises(ValueError, match="set twice"):
        SetStep((Assignment("mfc", "flow", 1), Assignment("mfc", "flow", 2)))
    with pytest.raises(ValueError, match="at least one"):
        SetStep(())


def test_multi_set_round_trips_and_single_setting_files_still_load():
    recipe = Recipe("rt", steps=(SetStep((Assignment("mfc", "flow", 3, "sccm"), Assignment("supply", "voltage", 1, "V"))),))
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(recipe)))) == recipe
    early = {"version": 2, "name": "early", "steps": [
        {"kind": "set", "device_id": "mfc", "setting": "flow", "value": 3, "unit": ""}]}
    assert recipe_from_dict(early).steps == (SetStep.one("mfc", "flow", 3),)


# --- Human-readable names ---

def test_names_appear_in_positions_events_and_round_trip():
    recipe = Recipe("named", steps=(
        SetStep.one("mfc", "flow", 5, name="Purge"),
        LoopStep("supply", "voltage", (1, 2, 3), steps=(WaitStep(0, MEASURE, "Hold"),),
                 name="Current density", pass_names=("break in", "100 mA", "200 mA")),
        RepeatStep(2, steps=(WaitStep(0),), name="Replicates"),
    ))
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(recipe)))) == recipe
    rows = preview_recipe(recipe)
    assert rows[0].label.startswith("Purge: Set")
    assert [r.position for r in rows if r.kind == "measure"][:2] == [
        ("Current density: break in (1/3)",), ("Current density: 100 mA (2/3)",)]
    assert rows[-1].position == ("Replicates (2/2)",)
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(recipe, recording_root="unused")
    assert runner.join(2).state is RecipeRunState.COMPLETED
    events = [json.loads(e.message) for e in writer.events if e.source == "recipe"]
    assert {"kind": "recipe_set", "detail": "Purge", "position": []} in events
    waits = [e for e in events if e["kind"] == "recipe_wait"]
    assert waits[2]["detail"] == "Hold" and waits[2]["position"] == ["Current density: 200 mA (3/3)"]


def test_pass_names_must_match_the_values():
    with pytest.raises(ValueError, match="one pass name per value"):
        LoopStep("mfc", "flow", (1, 2), pass_names=("only one",))


def test_early_wait_label_key_still_loads_as_its_name():
    data = {"version": 2, "name": "early", "steps": [
        {"kind": "wait", "duration_seconds": 5, "purpose": "settle", "label": "stabilise"}]}
    assert recipe_from_dict(data).steps == (WaitStep(5, SETTLE, "stabilise"),)


# --- A Set step can hold before the next step ---

def heat_then_hold():
    return Recipe("hold", steps=(
        SetStep((Assignment("mfc", "flow", 50),), name="Heat up", hold=WaitStep(600, SETTLE)),
        SetStep.one("mfc", "flow", 10),
        WaitStep(60, MEASURE),
    ))


def test_hold_runs_after_the_settings_as_a_recorded_wait():
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(heat_then_hold(), recording_root="unused")
    assert runner.join(2).state is RecipeRunState.COMPLETED
    events = [json.loads(e.message) for e in writer.events if e.source == "recipe"]
    kinds = [(e["kind"], e["detail"]) for e in events if e["kind"] in ("recipe_command", "recipe_wait")]
    assert kinds[:3] == [("recipe_command", "mfc.flow=50.0"), ("recipe_wait", "Heat up"), ("recipe_command", "mfc.flow=10.0")]
    hold_event = next(e for e in events if e["kind"] == "recipe_wait")
    assert hold_event["purpose"] == "settle" and hold_event["duration_seconds"] == 600


def test_hold_counts_in_estimates_preview_and_loops():
    recipe = heat_then_hold()
    estimate = estimate_recipe(recipe)
    assert (estimate.settle_waits, estimate.measure_waits, estimate.duration_seconds) == (1, 1, 660)
    rows = preview_recipe(recipe)
    assert [(r.kind, r.planned_start_seconds) for r in rows][:3] == [("set", 0), ("settle", 0), ("set", 600)]
    looped = Recipe("l", steps=(LoopStep("mfc", "flow", (1, 2), steps=(SetStep.one("supply", "voltage", 1),)),))
    assert estimate_recipe(looped).duration_seconds == 0
    looped = Recipe("l", steps=(LoopStep("mfc", "flow", (1, 2), steps=(
        SetStep((Assignment("supply", "voltage", 1),), hold=WaitStep(30, MEASURE)),)),))
    assert estimate_recipe(looped).duration_seconds == 60 and estimate_recipe(looped).measure_waits == 2


def test_hold_round_trips_and_zero_means_none():
    recipe = heat_then_hold()
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(recipe)))) == recipe
    assert SetStep((Assignment("mfc", "flow", 1),), hold=WaitStep(0)).hold is None
    with pytest.raises(TypeError, match="hold"):
        SetStep((Assignment("mfc", "flow", 1),), hold=5)  # type: ignore[arg-type]


# --- Ramps: reach a value as a staircase, holding at each step ---

from rig_control.recipes import Ramp, RampKind, RampStartUnknown, plan, recipe_problems, step_times  # noqa: E402
from rig_control.recipes.plan import PlannedSet  # noqa: E402
from rig_control.recipes.model import ramp_holds, ramp_path  # noqa: E402


def test_ramp_paths_for_each_kind_land_exactly_and_go_either_way():
    assert ramp_holds((0.02, 0.04, 0.06)) == 2 and ramp_holds(()) == 0
    steps = Ramp(RampKind.STEP_SIZE, 30, start=0.0, step=0.02)
    assert ramp_path(steps, 0.0, 0.1) == (0.02, 0.04, 0.06, 0.08, 0.1)
    assert ramp_path(steps, 0.0, 0.05) == (0.02, 0.04, 0.05)          # last step shortened, no overshoot
    assert ramp_path(steps, 0.1, 0.05) == (0.08, 0.06, 0.05)          # down works the same way
    assert ramp_path(steps, 0.1, 0.1) == ()
    count = Ramp(RampKind.STEP_COUNT, 30, start=0.0, count=4)
    assert ramp_path(count, 0.0, 0.2) == (0.05, 0.1, 0.15, 0.2)
    listed = Ramp(RampKind.SETPOINTS, 60, setpoints=(0.01, 0.05))
    assert ramp_path(listed, 0.01, 0.1) == (0.01, 0.05, 0.1)
    rate = Ramp(RampKind.RATE, 2, start=0.0, rate=0.005)               # 5 mA/s, updated every 2 s
    assert ramp_path(rate, 0.0, 0.03) == (0.01, 0.02, 0.03)


def test_ramps_refuse_bad_input():
    with pytest.raises(ValueError, match="step size"):
        Ramp(RampKind.STEP_SIZE, 30, start=0.0, step=0)
    with pytest.raises(ValueError, match="setpoints"):
        Ramp(RampKind.SETPOINTS, 30)
    with pytest.raises(ValueError, match="limit"):
        Assignment("supply", "current_setpoint", 10, ramp=Ramp(RampKind.STEP_SIZE, 1, start=0.0, step=1e-6))
    with pytest.raises(ValueError, match="cannot be ramped"):
        Assignment("supply", "output_enabled", True, ramp=Ramp(RampKind.STEP_COUNT, 1, start=0.0, count=2))
    with pytest.raises(ValueError, match="loop cannot ramp via"):
        LoopStep("mfc", "flow", (1, 2), ramp=Ramp(RampKind.SETPOINTS, 1, setpoints=(0.5,)))


def break_in_recipe():
    return Recipe("break in", steps=(
        # The editor's card order: setpoint, limit, output.
        # A fixed start (0 A): nothing earlier sets the current.
        SetStep((Assignment("supply", "current_setpoint", 0.04, ramp=Ramp(RampKind.STEP_COUNT, 30, start=0.0, count=2)),
                 Assignment("supply", "voltage_limit", 5), Assignment("supply", "output_enabled", True)),
                name="Break in"),
        # No start given: the loop ramps on from wherever the break-in left the current.
        LoopStep("supply", "current_setpoint", (0.1, 0.2), name="Current density",
                 ramp=Ramp(RampKind.STEP_SIZE, 10, step=0.05), steps=(WaitStep(60, MEASURE),)),
    ))


def test_set_step_writes_ramp_start_first_so_supply_order_stays_safe():
    recipe = break_in_recipe()
    first = next(item for item in plan(recipe.steps) if isinstance(item, PlannedSet))
    assert [(a.setting, a.value) for a in first.writes] == [
        ("current_setpoint", 0), ("voltage_limit", 5), ("output_enabled", True)]
    assert power_supply_problems(recipe) == ()


def test_ramps_run_record_and_estimate_the_same_path():
    recipe = break_in_recipe()
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(recipe, recording_root="unused")
    assert runner.join(2).state is RecipeRunState.COMPLETED
    currents = [r.command.current for r in runner._control.history if type(r.command).__name__ == "SetPowerSupplyCurrentLimit"]
    # Break in: 0 then 20, 40 mA. Loop: first value from 40 mA in 50 mA steps (90, 100), then 100 -> 150, 200.
    assert currents == [0, 0.02, 0.04, 0.09, 0.1, 0.15, 0.2]  # no jump back between break-in and loop
    events = [json.loads(e.message) for e in writer.events if e.source == "recipe"]
    ramps = [e for e in events if e["kind"] == "recipe_ramp"]
    assert [(e["start"], e["target"], e["steps"]) for e in ramps] == [(0, 0.04, 2), (0.04, 0.1, 2), (0.1, 0.2, 2)]
    holds = [e for e in events if e["kind"] == "recipe_wait" and e["purpose"] == "settle"]
    # Held on the way (20 mA; 90 mA; 150 mA), never at a target.
    assert [e["position"][-1] for e in holds] == ["ramp 0.02 (1/2)", "ramp 0.09 (1/2)", "ramp 0.15 (1/2)"]
    estimate = estimate_recipe(recipe)
    assert (estimate.settle_waits, estimate.measure_waits) == (3, 2)
    assert estimate.duration_seconds == 30 + 2 * 10 + 2 * 60
    preview_settles = [r for r in preview_recipe(recipe) if r.kind == "settle"]
    assert [r.planned_start_seconds for r in preview_settles] == [0, 30, 30 + 10 + 60]


def test_ramps_round_trip():
    recipe = break_in_recipe()
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(recipe)))) == recipe
    listed = Recipe("l", steps=(SetStep((Assignment("supply", "current_setpoint", 0.1,
                                                     ramp=Ramp(RampKind.SETPOINTS, 5, setpoints=(0.01, 0.05))),)),))
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(listed)))) == listed


def temperatures(*writes):
    return [round(a.value, 6) for item in plan(Recipe("t", steps=writes).steps) if isinstance(item, PlannedSet)
            for a in item.writes if a.setting == "temperature_setpoint"]


def test_ramp_starts_from_the_last_value_the_recipe_set():
    hold_at_20 = SetStep.one("re72", "temperature_setpoint", 20)
    sweep = LoopStep("re72", "temperature_setpoint", (30, 40), ramp=Ramp(RampKind.STEP_SIZE, 60, step=5),
                     steps=(WaitStep(600, MEASURE),))
    # From 20 °C, not from 0: 25, 30 | 35, 40.
    assert temperatures(hold_at_20, sweep) == [20, 25, 30, 35, 40]


def test_nested_loops_ramp_from_where_the_inner_loop_finished():
    inner = LoopStep("re72", "temperature_setpoint", (30, 40), ramp=Ramp(RampKind.STEP_SIZE, 1, step=10),
                     steps=(WaitStep(1),))
    outer = RepeatStep(2, steps=(inner,))
    # Second pass of the repeat ramps 40 -> 30 (down), then 30 -> 40 again.
    assert temperatures(SetStep.one("re72", "temperature_setpoint", 20), outer) == [20, 30, 40, 30, 40]


def test_a_ramp_with_nothing_to_start_from_is_reported_not_guessed():
    lonely = Recipe("r", steps=(SetStep((Assignment("re72", "temperature_setpoint", 50,
                                                     ramp=Ramp(RampKind.STEP_COUNT, 1, count=5)),), name="Heat"),))
    assert "nothing earlier in the recipe sets it" in recipe_problems(lonely)[0]
    with pytest.raises(RampStartUnknown):
        estimate_recipe(lonely)
    runner, _supply, _mfc = make_runner()
    with pytest.raises(RecipeValidationError, match="fixed start"):
        runner.validate(lonely)
    # A fixed start makes it fine: it jumps to 20 °C first, then ramps.
    fixed = SetStep((Assignment("re72", "temperature_setpoint", 50, ramp=Ramp(RampKind.STEP_COUNT, 1, start=20.0, count=3)),))
    assert temperatures(fixed) == [20, 30, 40, 50]


def test_step_times_follow_ramps_that_differ_per_pass():
    recipe = break_in_recipe()
    times = step_times(recipe.steps)
    assert times.total((0,)) == 30                          # break-in: one held step
    assert times.total((1,)) == 2 * 10 + 2 * 60             # loop: ramps + measurements
    assert times.passes[(1,)] == [10 + 60, 10 + 60]
    assert times.total((1, 0)) == 120                       # the Measure inside, over both passes


# --- End state: how the rig is left on finish, Stop or failure ---

from rig_control.recipes import EndAction, EndDevice, EndState  # noqa: E402


def mfc_keeps_flowing():
    return EndState((EndDevice("mfc", EndAction.LEAVE),))


def test_stop_skips_remaining_steps_and_applies_the_end_state():
    runner, supply, mfc = make_runner()
    runner._sleeper = lambda _seconds: runner.stop()
    runner.start(Recipe("stop", steps=(
        SetStep((Assignment("mfc", "flow", 40), Assignment("supply", "voltage", 8))),
        WaitStep(10), SetStep.one("mfc", "flow", 99),
    ), end_state=mfc_keeps_flowing()))
    result = runner.join(2)
    assert result.state is RecipeRunState.STOPPED and result.safe_state_failures == ()
    assert mfc.flow_setpoint == 40          # left as it was; the later 99 never ran
    assert supply.voltage_setpoint == 0     # not listed, so safe state


def test_end_state_can_set_values_and_completion_uses_it():
    runner, supply, mfc = make_runner()
    end = EndState((EndDevice("mfc", EndAction.SET, (Assignment("mfc", "flow", 20),)),))
    runner.start(Recipe("purge", steps=(SetStep.one("mfc", "flow", 60),), end_state=end))
    assert runner.join(2).state is RecipeRunState.COMPLETED
    assert mfc.flow_setpoint == 20


def test_failed_end_state_write_falls_back_to_that_devices_safe_state():
    runner, supply, _mfc = make_runner()
    safe_calls = []
    def not_answering(_volts):
        raise OSError("supply not answering")
    supply.set_voltage = not_answering  # type: ignore[method-assign]
    supply.enter_safe_state = lambda: safe_calls.append("safe")  # type: ignore[method-assign]
    end = EndState((EndDevice("supply", EndAction.SET, (Assignment("supply", "voltage", 2),)),))
    runner.start(Recipe("r", end_state=end))
    result = runner.join(2)
    assert result.state is RecipeRunState.COMPLETED
    assert safe_calls == ["safe"]
    assert "safe state requested instead" in result.safe_state_failures[0]


def test_recipes_saved_before_end_states_send_everything_to_safe_state():
    old = recipe_from_dict({"version": 2, "name": "old", "steps": []})
    assert old.end_state == EndState()
    assert old.end_state.for_device("mfc").action is EndAction.SAFE
    recipe = Recipe("rt", end_state=EndState((EndDevice("mfc", EndAction.LEAVE),
                                               EndDevice("supply", EndAction.SET, (Assignment("supply", "output_enabled", False),)))))
    assert recipe_from_dict(json.loads(json.dumps(recipe_to_dict(recipe)))) == recipe


def test_end_state_entries_are_checked():
    with pytest.raises(ValueError, match="at least one setting"):
        EndDevice("mfc", EndAction.SET)
    with pytest.raises(ValueError, match="cannot ramp"):
        EndDevice("supply", EndAction.SET, (Assignment("supply", "voltage", 1, ramp=Ramp(RampKind.STEP_COUNT, 1, start=0.0, count=2)),))
    with pytest.raises(ValueError, match="only once"):
        EndState((EndDevice("mfc", EndAction.LEAVE), EndDevice("mfc", EndAction.SAFE)))
    runner, _supply, _mfc = make_runner()
    with pytest.raises(RecipeValidationError, match="exceeds configured maximum"):
        runner.validate(Recipe("r", end_state=EndState((EndDevice("mfc", EndAction.SET, (Assignment("mfc", "flow", 500),)),))))


def test_runner_reports_which_session_devices_have_a_safe_state():
    runner, _supply, _mfc = make_runner()
    assert runner.has_safe_state("supply") is True
    assert runner.has_safe_state("not-in-session") is None
