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
from rig_control.ui.manual_control_model import (
    ManualControlViewModel,
)


def make_model() -> tuple[
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
        ManualControlViewModel(manager, service),
        service,
        supply,
        mfc,
    )


def test_rows_are_generated_from_device_capabilities() -> None:
    model, _, _, _ = make_model()

    mfc_rows = model.mfc_rows()
    supply_rows = model.power_supply_rows()

    assert len(mfc_rows) == 1
    assert mfc_rows[0].device_id == "dry_gas_mfc"
    assert mfc_rows[0].maximum_flow == 100.0
    assert mfc_rows[0].flow_unit == "sccm"

    assert len(supply_rows) == 1
    assert supply_rows[0].device_id == "main_supply"
    assert supply_rows[0].maximum_voltage == 30.0
    assert supply_rows[0].maximum_current == 108.0


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