"""Everyday supply limits: every current and voltage sent must pass all that apply."""
import pytest

from rig_control.app_settings import AppSettings
from rig_control.cell_area import CellArea
from rig_control.control.commands import (
    CommandSource, EnterDeviceSafeState, SetPowerSupplyCurrentLimit, SetPowerSupplyVoltage,
)
from rig_control.control.service import LimitExceeded
from rig_control.recipes import Recipe, SetStep, WaitStep
from rig_control.recipes.execution import RecipeRunState, RecipeValidationError
from rig_control.supply_limits import SupplyLimitGuard, SupplyLimits, instrument_maximums
from tests.test_recipes import make_runner

DENSITY = "current_density_setpoint"
ON = SetStep.one("supply", "output_enabled", True)
OFF = SetStep.one("supply", "output_enabled", False)


def guarded_runner(area: float | None = None, limits: SupplyLimits = SupplyLimits()):
    runner, supply, mfc = make_runner()
    cell = CellArea(); cell.set(area)
    runner._cell_area = cell
    runner._supply_limits = limits
    runner._control.limit_guard = SupplyLimitGuard(limits, lambda: cell.cm2, instrument_maximums(runner._devices))
    return runner, supply


def test_the_tightest_limit_applies_and_says_which_it_is():
    limits = SupplyLimits()  # 1.5 A, 6 V, 300 mA/cm², 45 A cable
    assert limits.current_ceiling(None) == (1.5, "the everyday maximum of 1.5 A")
    amps, reason = limits.current_ceiling(2.0)  # 300 mA/cm² × 2 cm² = 0.6 A
    assert amps == pytest.approx(0.6) and "mA/cm²" in reason
    # A wrong (too large) area can never raise the limit above the everyday maximum.
    assert limits.current_ceiling(50.0)[0] == 1.5
    assert limits.current_ceiling(None, instrument_amps=1.0)[0] == 1.0
    assert limits.voltage_ceiling()[0] == 6.0 and limits.voltage_ceiling(5.0)[0] == 5.0


def test_settings_build_the_limits_and_high_current_mode_only_moves_the_cable_ceiling():
    settings = AppSettings("s", "S", {"power_supply_default_current_amps": 2.0,
                                      "power_supply_maximum_current_density": 400.0})
    assert settings.supply_limits == SupplyLimits(2.0, 6.0, 400.0, 45.0)
    raised = AppSettings("s", "S", {"power_supply_high_current_mode": True,
                                    "power_supply_wiring_current_ceiling_amps": 60.0})
    assert raised.supply_limits.cable_ceiling_amps == 60.0
    assert raised.supply_limits.current_ceiling(None)[0] == 1.5  # Still the everyday maximum.


@pytest.mark.parametrize("source", [CommandSource.MANUAL, CommandSource.RECIPE_OVERRIDE])
def test_every_command_is_checked_whoever_sends_it(source):
    runner, _supply = guarded_runner(area=5.0)  # 300 × 5 = 1.5 A
    control = runner._control
    control.execute(SetPowerSupplyCurrentLimit("supply", 1.5, CommandSource.MANUAL))
    if source is CommandSource.RECIPE_OVERRIDE:
        control.begin_recipe_control()
    with pytest.raises(LimitExceeded, match="above"):
        control.execute(SetPowerSupplyCurrentLimit("supply", 1.6, source))
    with pytest.raises(LimitExceeded, match="6 V"):
        control.execute(SetPowerSupplyVoltage("supply", 6.5, source))
    # Refusals are audited as blocked, not sent.
    assert all(r.command.current <= 1.5 for r in control.history if hasattr(r.command, "current"))


def test_safety_commands_are_never_limited():
    runner, _supply = guarded_runner()
    runner._control.execute(EnterDeviceSafeState("supply", CommandSource.SAFETY_SYSTEM))


def test_a_recipe_beyond_a_limit_is_refused_before_it_starts():
    runner, _supply = guarded_runner(area=2.0)  # 300 mA/cm² × 2 cm² = 0.6 A
    with pytest.raises(RecipeValidationError, match=r"400 mA/cm² at 2 cm²\) .* above 300 mA/cm² × 2 cm²"):
        runner.validate(Recipe("r", (SetStep.one("supply", DENSITY, 400.0), ON)))
    with pytest.raises(RecipeValidationError, match="everyday maximum of 6 V"):
        runner.validate(Recipe("r", (SetStep.one("supply", "voltage_limit", 8.0),)))
    runner.validate(Recipe("r", (SetStep.one("supply", DENSITY, 300.0), ON)))  # Exactly at the limit.
    no_area, _supply = guarded_runner()  # Without an area, the everyday maximum still applies.
    with pytest.raises(RecipeValidationError, match="everyday maximum of 1.5 A"):
        no_area.validate(Recipe("r", (SetStep.one("supply", "current_setpoint", 5.0), ON)))


def test_entering_a_mode_writes_the_everyday_limit_and_a_run_may_set_a_lower_one():
    runner, _supply = guarded_runner(area=2.0)  # CV current limit: 300 × 2 = 0.6 A
    runner.start(Recipe("r", (
        SetStep.one("supply", DENSITY, 100.0), ON, WaitStep(0), OFF,           # CC: voltage limit 6 V
        SetStep.one("supply", "voltage_limit", 4.0), ON, WaitStep(0), OFF,      # lower, per run
        SetStep.one("supply", "voltage", 1.0), ON, WaitStep(0), OFF,            # CV: current limit 0.6 A
    )))
    assert runner.join(5).state is RecipeRunState.COMPLETED
    history = [r.command for r in runner._control.history
               if isinstance(r.command, (SetPowerSupplyVoltage, SetPowerSupplyCurrentLimit))]
    values = [(type(c).__name__, getattr(c, "voltage", getattr(c, "current", None))) for c in history]
    assert values[:2] == [("SetPowerSupplyVoltage", 6.0), ("SetPowerSupplyCurrentLimit", 0.2)]
    assert ("SetPowerSupplyVoltage", 4.0) in values
    assert values[-2:] == [("SetPowerSupplyCurrentLimit", pytest.approx(0.6)), ("SetPowerSupplyVoltage", 1.0)]
