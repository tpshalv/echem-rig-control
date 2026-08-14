from datetime import timezone

import pytest

from rig_control.control.commands import (
    CommandSource,
    EnterDeviceSafeState,
    SetMfcFlow,
    SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput,
    SetPowerSupplyVoltage,
)
from rig_control.control.service import (
    ControlAccessError,
    ControlExecutionError,
    ControlMode,
    RigControlService,
)
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


def make_control_service() -> tuple[
    RigControlService,
    SimulatedPowerSupply,
    SimulatedMassFlowController,
]:
    manager = DeviceManager()

    supply = SimulatedPowerSupply(
        device_id="main_supply",
        limits=PowerSupplyLimits(
            maximum_voltage=30.0,
            maximum_current=108.0,
            maximum_power=1080.0,
        ),
    )
    mfc = SimulatedMassFlowController(
        device_id="dry_gas_mfc",
        limits=MassFlowControllerLimits(
            maximum_flow=100.0,
            flow_unit="sccm",
        ),
    )

    supply.connect()
    mfc.connect()

    manager.register(supply)
    manager.register(mfc)

    return RigControlService(manager), supply, mfc


def test_service_starts_idle_with_empty_history() -> None:
    service, _, _ = make_control_service()

    assert service.mode is ControlMode.IDLE
    assert service.history == ()


def test_manual_mfc_command_is_executed_and_recorded() -> None:
    service, _, mfc = make_control_service()
    command = SetMfcFlow(
        "dry_gas_mfc",
        25.0,
        CommandSource.MANUAL,
    )

    result = service.execute(command)

    assert mfc.flow_setpoint == 25.0
    assert result.command is command
    assert "25.0 sccm" in result.message
    assert result.executed_at.tzinfo is timezone.utc
    assert service.history == (result,)


def test_manual_power_supply_commands_are_executed() -> None:
    service, supply, _ = make_control_service()

    service.execute(
        SetPowerSupplyVoltage(
            "main_supply",
            10.0,
            CommandSource.MANUAL,
        )
    )
    service.execute(
        SetPowerSupplyCurrentLimit(
            "main_supply",
            5.0,
            CommandSource.MANUAL,
        )
    )
    service.execute(
        SetPowerSupplyOutput(
            "main_supply",
            True,
            CommandSource.MANUAL,
        )
    )

    assert supply.voltage_setpoint == 10.0
    assert supply.current_limit == 5.0
    assert supply.output_enabled is True
    assert len(service.history) == 3


def test_recipe_command_is_blocked_before_recipe_control() -> None:
    service, _, mfc = make_control_service()

    with pytest.raises(
        ControlAccessError,
        match="recipe control has not begun",
    ):
        service.execute(
            SetMfcFlow(
                "dry_gas_mfc",
                25.0,
                CommandSource.RECIPE,
            )
        )

    assert mfc.flow_setpoint == 0.0
    assert service.history == ()


def test_recipe_command_is_allowed_after_acquiring_control() -> None:
    service, _, mfc = make_control_service()
    service.begin_recipe_control()

    result = service.execute(
        SetMfcFlow(
            "dry_gas_mfc",
            25.0,
            CommandSource.RECIPE,
        )
    )

    assert service.mode is ControlMode.RECIPE_ACTIVE
    assert mfc.flow_setpoint == 25.0
    assert result.command.source is CommandSource.RECIPE


@pytest.mark.parametrize(
    "source",
    [
        CommandSource.MANUAL,
        CommandSource.DIAGNOSTIC,
    ],
)
def test_non_recipe_commands_are_blocked_during_recipe(
    source: CommandSource,
) -> None:
    service, _, mfc = make_control_service()
    service.begin_recipe_control()

    with pytest.raises(
        ControlAccessError,
        match="while a recipe controls the rig",
    ):
        service.execute(
            SetMfcFlow(
                "dry_gas_mfc",
                25.0,
                source,
            )
        )

    assert mfc.flow_setpoint == 0.0


def test_recipe_control_can_end_normally() -> None:
    service, _, _ = make_control_service()
    service.begin_recipe_control()

    service.end_recipe_control()

    assert service.mode is ControlMode.IDLE


def test_recipe_control_cannot_begin_twice() -> None:
    service, _, _ = make_control_service()
    service.begin_recipe_control()

    with pytest.raises(
        ControlAccessError,
        match="current mode",
    ):
        service.begin_recipe_control()


def test_recipe_control_cannot_end_when_inactive() -> None:
    service, _, _ = make_control_service()

    with pytest.raises(
        ControlAccessError,
        match="not currently active",
    ):
        service.end_recipe_control()


@pytest.mark.parametrize(
    "source",
    [
        CommandSource.MANUAL,
        CommandSource.DIAGNOSTIC,
        CommandSource.RECIPE,
    ],
)
def test_fault_lock_blocks_non_safety_commands(
    source: CommandSource,
) -> None:
    service, _, mfc = make_control_service()
    service.enter_fault_lock()

    with pytest.raises(
        ControlAccessError,
        match="fault-locked",
    ):
        service.execute(
            SetMfcFlow(
                "dry_gas_mfc",
                25.0,
                source,
            )
        )

    assert mfc.flow_setpoint == 0.0


def test_safety_command_remains_available_during_fault() -> None:
    service, _, mfc = make_control_service()

    service.execute(
        SetMfcFlow(
            "dry_gas_mfc",
            25.0,
            CommandSource.MANUAL,
        )
    )
    service.enter_fault_lock()

    result = service.execute(
        EnterDeviceSafeState("dry_gas_mfc")
    )

    assert mfc.flow_setpoint == 0.0
    assert "safe state" in result.message
    assert service.mode is ControlMode.FAULT_LOCKED


def test_fault_lock_requires_explicit_clear() -> None:
    service, _, _ = make_control_service()
    service.enter_fault_lock()

    service.clear_fault_lock()

    assert service.mode is ControlMode.IDLE


def test_fault_lock_cannot_clear_when_not_locked() -> None:
    service, _, _ = make_control_service()

    with pytest.raises(
        ControlAccessError,
        match="not fault-locked",
    ):
        service.clear_fault_lock()


def test_wrong_device_capability_has_clear_error() -> None:
    service, _, _ = make_control_service()

    with pytest.raises(
        ControlExecutionError,
        match="not a mass flow controller",
    ) as captured_error:
        service.execute(
            SetMfcFlow(
                "main_supply",
                25.0,
                CommandSource.MANUAL,
            )
        )

    assert isinstance(
        captured_error.value.__cause__,
        TypeError,
    )


def test_device_limit_failure_is_wrapped_with_context() -> None:
    service, _, mfc = make_control_service()

    with pytest.raises(
        ControlExecutionError,
        match="dry_gas_mfc",
    ) as captured_error:
        service.execute(
            SetMfcFlow(
                "dry_gas_mfc",
                101.0,
                CommandSource.MANUAL,
            )
        )

    assert "configured maximum" in str(captured_error.value)
    assert isinstance(
        captured_error.value.__cause__,
        ValueError,
    )
    assert mfc.flow_setpoint == 0.0
    assert service.history == ()


def test_unknown_device_failure_is_wrapped_with_context() -> None:
    service, _, _ = make_control_service()

    with pytest.raises(
        ControlExecutionError,
        match="missing_mfc",
    ) as captured_error:
        service.execute(
            SetMfcFlow(
                "missing_mfc",
                10.0,
                CommandSource.MANUAL,
            )
        )

    assert isinstance(
        captured_error.value.__cause__,
        KeyError,
    )


def test_unsupported_command_object_is_rejected() -> None:
    service, _, _ = make_control_service()

    with pytest.raises(
        TypeError,
        match="unsupported command object",
    ):
        service.execute(object())  # type: ignore[arg-type]