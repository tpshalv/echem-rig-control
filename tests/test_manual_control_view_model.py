from rig_control.control.service import RigControlService
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import (
    MassFlowControllerLimits,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_mfc import (
    SimulatedMassFlowController,
)
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.devices.power_supply import (
    PowerSupplyOperatingMode,
)
from rig_control.ui.manual_control.model import (
    ManualControlViewModel,
)
from rig_control.ui.manual_control.types import PowerSupplyManualSafety


def make_model(
    power_supply_safety: PowerSupplyManualSafety | None = None,
) -> tuple[
    ManualControlViewModel,
    RigControlService,
    SimulatedPowerSupply,
    SimulatedMassFlowController,
]:
    manager = DeviceManager()

    supply = SimulatedPowerSupply(
        "main_supply",
        PowerSupplyLimits(30.0, 108.0, 1080.0),
    )
    mfc = SimulatedMassFlowController(
        "dry_gas_mfc",
        MassFlowControllerLimits(100.0, "sccm"),
    )

    supply.connect()
    mfc.connect()

    manager.register(supply)
    manager.register(mfc)

    service = RigControlService(manager)

    return (
        ManualControlViewModel(
            manager, service, power_supply_safety=power_supply_safety
        ),
        service,
        supply,
        mfc,
    )


def test_manual_mfc_flow_command_succeeds() -> None:
    model, _, _, mfc = make_model()

    result = model.set_mfc_flow("dry_gas_mfc", 25.0)

    assert result.succeeded is True
    assert result.technical_details is None
    assert "25.0 sccm" in result.summary
    assert mfc.flow_setpoint == 25.0


def test_manual_power_supply_commands_succeed() -> None:
    model, _, supply, _ = make_model()

    voltage_result = model.set_power_supply_voltage(
        "main_supply",
        10.0,
    )
    current_result = model.set_power_supply_current(
        "main_supply",
        5.0,
    )
    output_result = model.set_power_supply_output(
        "main_supply",
        True,
    )

    assert voltage_result.succeeded is True
    assert current_result.succeeded is True
    assert output_result.succeeded is True
    assert supply.voltage_setpoint == 10.0
    assert supply.current_limit == 5.0
    assert supply.output_enabled is True


def test_manual_command_is_blocked_during_recipe() -> None:
    model, service, _, mfc = make_model()
    service.begin_recipe_control()

    result = model.set_mfc_flow("dry_gas_mfc", 25.0)

    assert result.succeeded is False
    assert "blocked while a recipe controls" in result.summary
    assert result.technical_details is not None
    assert "ControlAccessError" in result.technical_details
    assert mfc.flow_setpoint == 0.0


def test_safe_state_remains_available_during_recipe() -> None:
    model, service, _, mfc = make_model()

    model.set_mfc_flow("dry_gas_mfc", 25.0)
    service.begin_recipe_control()

    result = model.enter_safe_state("dry_gas_mfc")

    assert result.succeeded is True
    assert mfc.flow_setpoint == 0.0
    assert service.mode.value == "recipe_active"


def test_device_limit_error_is_returned_not_raised() -> None:
    model, _, _, mfc = make_model()

    result = model.set_mfc_flow("dry_gas_mfc", 101.0)

    assert result.succeeded is False
    assert "configured maximum" in result.summary
    assert result.technical_details is not None
    assert "ValueError" in result.technical_details
    assert mfc.flow_setpoint == 0.0


def test_disconnected_device_error_is_returned_not_raised() -> None:
    model, _, _, mfc = make_model()
    mfc.disconnect()

    result = model.set_mfc_flow("dry_gas_mfc", 25.0)

    assert result.succeeded is False
    assert "not ready" in result.summary
    assert result.technical_details is not None


def test_global_safe_state_succeeds_for_all_devices() -> None:
    model, service, supply, mfc = make_model()

    supply.set_voltage(10.0)
    supply.set_current_limit(5.0)
    supply.set_output_enabled(True)
    mfc.set_flow_setpoint(25.0)

    result = model.enter_global_safe_state()

    assert result.succeeded is True
    assert result.technical_details is None
    assert "GLOBAL SAFE STATE REQUESTED" in result.summary

    assert supply.output_enabled is False
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 0.0
    assert mfc.flow_setpoint == 0.0
    assert service.mode.value == "idle"


def test_global_safe_state_reports_individual_failure() -> None:
    model, service, supply, mfc = make_model()

    mfc.set_flow_setpoint(25.0)

    def failed_safe_state():
        raise OSError("Simulated supply communication failure")

    supply.enter_safe_state = failed_safe_state  # type: ignore[method-assign]

    result = model.enter_global_safe_state()

    assert result.succeeded is False
    assert "FAILED" in result.summary
    assert "main_supply" in result.summary
    assert "Simulated supply communication failure" in result.summary
    assert result.technical_details is not None
    assert "Traceback" in result.technical_details

    # The MFC was still handled despite the supply failure.
    assert mfc.flow_setpoint == 0.0
    assert service.mode.value == "idle"

def test_power_supply_defaults_to_constant_current_mode() -> None:
    model, _, _, _ = make_model()

    assert (
        model.power_supply_mode("main_supply")
        is PowerSupplyOperatingMode.CONSTANT_CURRENT
    )


def test_switching_to_constant_voltage_uses_safe_initial_target() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        5.0,
    )

    result = model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    assert result.succeeded is True
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 20.0
    assert supply.output_enabled is False
    assert (
        model.power_supply_mode("main_supply")
        is PowerSupplyOperatingMode.CONSTANT_VOLTAGE
    )


def test_switching_modes_restores_separate_targets() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )
    model.set_power_supply_voltage(
        "main_supply",
        2.0,
    )

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_CURRENT,
    )

    assert supply.current_limit == 15.0
    assert supply.voltage_setpoint == 10.0

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    assert supply.voltage_setpoint == 2.0
    assert supply.current_limit == 20.0

def test_manual_mode_cannot_change_while_output_is_enabled() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )
    supply.set_output_enabled(True)

    result = model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    assert result.succeeded is False
    assert "Disable the output first" in result.summary
    assert supply.output_enabled is True
    assert supply.current_limit == 15.0
    assert supply.voltage_setpoint == 0.0
    assert (
        model.power_supply_mode("main_supply")
        is PowerSupplyOperatingMode.CONSTANT_CURRENT
    )

def test_supply_remembers_separate_targets() -> None:
    model, _, _, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )
    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )
    model.set_power_supply_voltage(
        "main_supply",
        2.0,
    )

    assert model.power_supply_target("main_supply", PowerSupplyOperatingMode.CONSTANT_CURRENT) == 15.0
    assert model.power_supply_target("main_supply", PowerSupplyOperatingMode.CONSTANT_VOLTAGE) == 2.0

def test_manual_defaults_use_safe_starting_values_in_constant_current() -> None:
    model, _, supply, _ = make_model()

    results = model.initialize_manual_power_supply_defaults()

    assert all(result.succeeded for result in results)
    # Current is the setpoint in this mode - left untouched (remembered/0),
    # not defaulted here.
    assert supply.current_limit == 0.0
    # Voltage is the protective compliance limit in this mode: the low
    # starting default, not the instrument's own true maximum - this was
    # the original reported bug (108 A applied the same mistake to current
    # in constant voltage mode).
    assert supply.voltage_setpoint == 10.0
    assert supply.output_enabled is False


def test_manual_defaults_use_safe_starting_values_in_constant_voltage() -> None:
    model, _, supply, _ = make_model()
    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    results = model.initialize_manual_power_supply_defaults()

    assert all(result.succeeded for result in results)
    # Current is the protective compliance limit in this mode: the low
    # starting default (20 A), not the full 45 A wiring ceiling and
    # nowhere near the instrument's 108 A maximum - this was the original
    # reported bug.
    assert supply.current_limit == 20.0
    # Voltage is the setpoint in this mode - left untouched.
    assert supply.voltage_setpoint == 0.0
    assert supply.output_enabled is False

def test_constant_current_output_rejects_zero_voltage_limit() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )

    result = model.set_power_supply_output(
        "main_supply",
        True,
    )

    assert result.succeeded is False
    assert "voltage compliance limit is zero" in result.summary
    assert supply.output_enabled is False


def test_constant_voltage_output_rejects_zero_current_limit() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    # Deliberately override the normal 20 A default compliance limit.
    model.set_power_supply_current(
        "main_supply",
        0.0,
    )
    model.set_power_supply_voltage(
        "main_supply",
        2.0,
    )

    result = model.set_power_supply_output(
        "main_supply",
        True,
    )

    assert result.succeeded is False
    assert "current compliance limit is zero" in result.summary
    assert supply.output_enabled is False


def test_default_current_ceiling_is_45_amps() -> None:
    model, _, _, _ = make_model()

    assert model.effective_current_ceiling() == 45.0
    assert model.high_current_mode is False


def test_current_above_default_ceiling_is_rejected() -> None:
    model, _, supply, _ = make_model()

    result = model.set_power_supply_current("main_supply", 60.0)

    assert result.succeeded is False
    assert "exceeds the active current ceiling of 45" in result.summary
    assert supply.current_limit == 0.0


def test_high_current_mode_raises_the_ceiling() -> None:
    model, _, supply, _ = make_model(
        PowerSupplyManualSafety(
            high_current_mode=True,
            wiring_current_ceiling_amps=80.0,
            default_current_amps=20.0,
            default_voltage_volts=10.0,
        )
    )

    assert model.effective_current_ceiling() == 80.0

    accepted = model.set_power_supply_current("main_supply", 60.0)
    assert accepted.succeeded is True
    assert supply.current_limit == 60.0

    rejected = model.set_power_supply_current("main_supply", 90.0)
    assert rejected.succeeded is False
    assert "exceeds the active current ceiling of 80" in rejected.summary


def test_high_current_mode_off_ignores_a_stored_raised_ceiling() -> None:
    # Outside High current mode the ceiling is always the fixed 45 A
    # default, regardless of whatever the (inaccessible) ceiling setting
    # happens to be stored as - decisions/0014.
    model, _, _, _ = make_model(
        PowerSupplyManualSafety(
            high_current_mode=False,
            wiring_current_ceiling_amps=80.0,
            default_current_amps=20.0,
            default_voltage_volts=10.0,
        )
    )

    assert model.effective_current_ceiling() == 45.0
    result = model.set_power_supply_current("main_supply", 60.0)
    assert result.succeeded is False
