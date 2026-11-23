from datetime import UTC, datetime
import gc
import json

import pytest
from openpyxl import load_workbook

from rig_control.calibrated_channels import CalibratedChannels
from rig_control.cell_area import CellArea, CellAreaMissing, parse_area
from rig_control.control.service import RigControlService
from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.in_memory_writer import InMemoryExperimentWriter
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_mfc import SimulatedMassFlowController
from rig_control.devices.simulated_power_supply import SimulatedPowerSupply
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, Measurement, Quality
from rig_control.polling import PollingService
from rig_control.recipes import (
    Assignment, EndAction, EndDevice, EndState, LoopStep, Recipe, SetStep, WaitStep, power_supply_problems,
)
from rig_control.recipes.execution import RecipeRunner, RecipeRunState, RecipeValidationError
from rig_control.ui.operation.model import OperationViewModel
from rig_control.ui.operation.recipe_settings import current_density_summary, device_settings, recipe_turns_output_on
from rig_control.rig_profile import DeviceCapability


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DENSITY = "current_density_setpoint"
#: Without this the end state's safe state would zero what a test is checking.
LEAVE_ALL = EndState((EndDevice("supply", EndAction.LEAVE), EndDevice("mfc", EndAction.LEAVE)))


def make_runner(area: float | None = 5.0, flow_unit: str = "SLPM"):
    gc.collect()  # As in test_recipes: Tk objects must not be collected on the recipe thread.
    manager = DeviceManager()
    supply = SimulatedPowerSupply("supply", PowerSupplyLimits(30, 10, 100))
    mfc = SimulatedMassFlowController("mfc", MassFlowControllerLimits(2, flow_unit))
    supply.connect(); mfc.connect(); manager.register(supply); manager.register(mfc)
    cell_area = CellArea()
    cell_area.set(area)
    events: list[Event] = []
    runner = RecipeRunner(RigControlService(manager), manager, sleeper=lambda _s: None, cell_area=cell_area,
                          event_sink=events.append)
    return runner, supply, mfc, cell_area, events


def density_recipe(*values: float) -> Recipe:
    return Recipe("density", steps=(
        SetStep.one("supply", "voltage_limit", 3.0),
        LoopStep("supply", DENSITY, tuple(values), steps=(WaitStep(0),)),
    ), end_state=LEAVE_ALL)


# --- The area ---

@pytest.mark.parametrize("text", ["abc", "0", "-5", "inf", "nan"])
def test_an_unusable_area_is_refused(text):
    with pytest.raises(ValueError):
        parse_area(text)


def test_blank_area_means_none_entered():
    assert parse_area("  ") is None
    assert parse_area("5") == 5.0


def test_density_converts_both_ways_and_needs_an_area():
    area = CellArea()
    assert area.density_for(1.0) is None
    with pytest.raises(CellAreaMissing):
        area.amps_for(100.0)
    area.set(5.0)
    assert area.amps_for(100.0) == 0.5
    assert area.density_for(0.5) == 100.0


def test_the_area_is_fixed_while_recording():
    recording = [False]
    area = CellArea(is_locked=lambda: recording[0])
    area.set(5.0)
    recording[0] = True
    with pytest.raises(RuntimeError):
        area.set(25.0)
    area.set(5.0)  # Not a change.
    assert area.cm2 == 5.0


# --- Recipes in density ---

def test_recipe_densities_reach_the_supply_as_amperes():
    runner, supply, _mfc, _area, events = make_runner(area=5.0)
    runner.start(density_recipe(100.0, 400.0))
    assert runner.join(2).state is RecipeRunState.COMPLETED
    assert supply.current_limit == 2.0
    details = [json.loads(e.message) for e in events if e.source == "recipe"]
    assert [(d["amps"], d["area_cm2"]) for d in details if d["kind"] == "recipe_command" and "amps" in d] == [
        (0.5, 5.0), (2.0, 5.0)]


def test_a_density_recipe_cannot_start_without_an_area():
    runner, supply, _mfc, _area, _events = make_runner(area=None)
    before = supply.current_limit
    with pytest.raises(RecipeValidationError, match="no active area"):
        runner.start(density_recipe(100.0))
    assert supply.current_limit == before


def test_an_absolute_current_recipe_needs_no_area():
    runner, *_ = make_runner(area=None)
    runner.validate(Recipe("amps", steps=(SetStep.one("supply", "current_setpoint", 1.0),)))


def test_the_device_limit_is_checked_in_amperes():
    runner, *_ = make_runner(area=5.0)
    runner.validate(density_recipe(2000.0))  # 10 A is the limit.
    with pytest.raises(RecipeValidationError, match="exceeds"):
        runner.validate(density_recipe(2001.0))


def test_a_density_end_state_is_also_checked_for_an_area():
    runner, *_ = make_runner(area=None)
    end = EndState((EndDevice("supply", EndAction.SET, (Assignment("supply", DENSITY, 10.0),)),))
    with pytest.raises(RecipeValidationError, match="no active area"):
        runner.validate(Recipe("end", end_state=end))


def test_density_counts_as_the_current_setpoint_for_output_safety():
    on = SetStep.one("supply", "output_enabled", True)
    limit = SetStep.one("supply", "voltage_limit", 3.0)
    density = SetStep.one("supply", DENSITY, 100.0)
    assert power_supply_problems(Recipe("ok", steps=(limit, density, on))) == ()
    # The voltage limit comes from Settings on entering constant current.
    assert power_supply_problems(Recipe("no limit", steps=(density, on))) == ()
    assert "current_setpoint" in power_supply_problems(Recipe("no density", steps=(limit, on)))[0]
    # It states CC, so it cannot replace a CV setting while the output is on.
    cv = (SetStep.one("supply", "voltage", 3.0), SetStep.one("supply", "current_limit", 1.0), on)
    assert "turn the output off" in power_supply_problems(Recipe("switch", steps=(*cv, density)))[0]


def test_amps_and_density_cannot_both_be_set_in_one_step():
    with pytest.raises(ValueError, match="same thing"):
        SetStep((Assignment("supply", "current_setpoint", 1.0), Assignment("supply", DENSITY, 100.0)))
    with pytest.raises(ValueError, match="same thing"):
        SetStep((Assignment("mfc", "flow", 1.0), Assignment("mfc", "flow_sccm", 100.0)))


def test_density_summary_states_the_amperes_being_committed_to():
    recipe = density_recipe(100.0, 400.0)
    assert current_density_summary(recipe, 5.0) == "Active area 5 cm²: 100 to 400 mA/cm² is 0.5 to 2 A."
    assert current_density_summary(density_recipe(100.0), 5.0) == "Active area 5 cm²: 100 mA/cm² is 0.5 A."
    assert current_density_summary(Recipe("none", steps=(SetStep.one("mfc", "flow_sccm", 1.0),)), 5.0) == ""


def test_density_is_offered_for_constant_current_only():
    from rig_control.devices.power_supply import PowerSupplyOperatingMode as Mode
    names = lambda mode: [s.name for s in device_settings(DeviceCapability.DC_POWER_SUPPLY, mode)]
    assert DENSITY in names(Mode.CONSTANT_CURRENT)
    assert DENSITY not in names(Mode.CONSTANT_VOLTAGE)


def test_recipe_turning_output_on_is_noticed():
    assert recipe_turns_output_on(Recipe("on", steps=(
        SetStep.one("supply", "voltage_limit", 1.0), SetStep.one("supply", "current_setpoint", 1.0),
        SetStep.one("supply", "output_enabled", True))))
    assert not recipe_turns_output_on(Recipe("off", steps=(SetStep.one("mfc", "flow_sccm", 1.0),)))


# --- Recipes in SCCM ---

def test_a_recipe_flow_in_sccm_reaches_a_slpm_controller_converted():
    runner, _supply, mfc, *_ = make_runner(flow_unit="SLPM")
    runner.start(Recipe("flow", steps=(SetStep.one("mfc", "flow_sccm", 500.0, "SCCM"),), end_state=LEAVE_ALL))
    assert runner.join(2).state is RecipeRunState.COMPLETED
    assert mfc.flow_setpoint == pytest.approx(0.5)


def test_the_sccm_limit_is_checked_in_the_controllers_own_unit():
    runner, *_ = make_runner(flow_unit="SLPM")
    runner.validate(Recipe("ok", steps=(SetStep.one("mfc", "flow_sccm", 2000.0, "SCCM"),)))
    with pytest.raises(RecipeValidationError, match="exceeds"):
        runner.validate(Recipe("big", steps=(SetStep.one("mfc", "flow_sccm", 2001.0, "SCCM"),)))


def test_existing_recipes_keep_the_controllers_own_unit():
    runner, _supply, mfc, *_ = make_runner(flow_unit="SLPM")
    runner.start(Recipe("old", steps=(SetStep.one("mfc", "flow", 1.5),), end_state=LEAVE_ALL))
    assert runner.join(2).state is RecipeRunState.COMPLETED
    assert mfc.flow_setpoint == pytest.approx(1.5)


# --- Recorded channel ---

def current(value: float = 0.5, unit: str = "A", quality: Quality = Quality.GOOD):
    return (DeviceMeasurement("current", Measurement(value, unit, NOW, quality)),)


def test_a_supply_gains_current_density_next_to_its_raw_current():
    area = CellArea()
    area.set(5.0)
    supply = SimulatedPowerSupply("supply", PowerSupplyLimits(30, 10, 100))
    readings = CalibratedChannels(cell_area=area).add_to(supply, current(0.5, quality=Quality.UNCERTAIN))
    assert readings[0].channel == "current" and readings[0].measurement.value == 0.5
    assert readings[1].channel == "current_density"
    assert readings[1].measurement.value == 100.0
    assert readings[1].measurement.unit == "mA/cm2"
    assert readings[1].measurement.quality is Quality.UNCERTAIN


def test_no_area_means_no_density_channel():
    supply = SimulatedPowerSupply("supply", PowerSupplyLimits(30, 10, 100))
    assert len(CalibratedChannels(cell_area=CellArea()).add_to(supply, current())) == 1


# --- Operation ---

def make_operation(area: float | None):
    manager = DeviceManager()
    supply = SimulatedPowerSupply("supply", PowerSupplyLimits(30, 10, 100))
    supply.connect(); manager.register(supply)
    recorder = ExperimentRecorder(writer_factory=lambda _root: InMemoryExperimentWriter())
    polling = PollingService(manager, interval_seconds=60.0, batch_handler=recorder.record_batch)
    cell_area = CellArea()
    cell_area.set(area)
    return (OperationViewModel(manager, polling, recorder, RigControlService(manager), profile_id="sim",
                               cell_area=cell_area), supply)


def row_for(model, channel):
    return next(r for r in model.channel_rows() if r.channel == channel)


def test_operation_shows_and_accepts_density_alongside_amperes():
    model, supply = make_operation(5.0)
    supply.set_current_limit(1.0)
    density, amps = row_for(model, "current_density_setpoint"), row_for(model, "current_limit")
    assert (density.value, density.unit, density.writable) == (200.0, "mA/cm²", True)
    assert density.maximum == 2000.0
    assert (amps.value, amps.unit, amps.writable) == (1.0, "A", True)
    assert model.apply_channel_value("supply", "current_density_setpoint", 100.0).succeeded
    assert supply.current_limit == 0.5
    assert row_for(model, "current_limit").value == 0.5


def test_operation_without_an_area_refuses_density_but_not_amperes():
    model, supply = make_operation(None)
    row = row_for(model, "current_density_setpoint")
    assert not row.writable and row.value is None and "enter active area" in row.channel_name
    result = model.apply_channel_value("supply", "current_density_setpoint", 100.0)
    assert not result.succeeded and "no active area" in result.summary
    assert model.apply_channel_value("supply", "current_limit", 0.5).succeeded
    assert supply.current_limit == 0.5


def test_operation_density_beyond_the_instruments_maximum_is_refused():
    model, supply = make_operation(5.0)
    before = supply.current_limit
    assert not model.apply_channel_value("supply", "current_density_setpoint", 2001.0).succeeded  # 10.005 A.
    assert supply.current_limit == before


def test_operation_density_obeys_the_wiring_ceiling():
    model, supply = make_operation(5.0)
    model.manual_control.effective_current_ceiling = lambda: 1.0
    assert "ceiling" in model.apply_channel_value("supply", "current_density_setpoint", 201.0).summary  # 1.005 A.
    assert model.apply_channel_value("supply", "current_density_setpoint", 200.0).succeeded
    assert supply.current_limit == 1.0


def test_a_forgotten_area_is_noticed_only_when_a_cell_looks_present():
    model, supply = make_operation(None)
    assert not model.active_area_forgotten("")
    assert model.active_area_forgotten("TS-041")
    supply.set_current_limit(0.1); supply.set_voltage(1.0); supply.set_output_enabled(True)
    assert model.active_area_forgotten("")
    model.cell_area.set(5.0)
    assert not model.active_area_forgotten("TS-041")


# --- The record ---

def test_the_recording_states_its_area_in_the_excel_overview(tmp_path):
    writer = DirectoryExperimentWriter(tmp_path, export_bin_seconds=1.0, live_export_interval_seconds=0)
    writer.open_experiment(ExperimentMetadata("run", "TS", NOW, extra=CellAreaWith(5.0).snapshot()))
    writer.close_experiment()
    overview = load_workbook(next(tmp_path.rglob("*.xlsx")))["Overview"]
    rows = {row[0].value: row[1].value for row in overview.iter_rows() if row[0].value}
    assert rows["Active area (cm²)"] == 5.0
    assert "extra.active_area_cm2" not in rows


def test_a_run_without_an_area_says_so(tmp_path):
    writer = DirectoryExperimentWriter(tmp_path, export_bin_seconds=1.0, live_export_interval_seconds=0)
    writer.open_experiment(ExperimentMetadata("run", "TS", NOW))
    writer.close_experiment()
    overview = load_workbook(next(tmp_path.rglob("*.xlsx")))["Overview"]
    rows = {row[0].value: row[1].value for row in overview.iter_rows() if row[0].value}
    assert rows["Active area (cm²)"] == "not recorded"


def CellAreaWith(cm2: float) -> CellArea:
    area = CellArea()
    area.set(cm2)
    return area
