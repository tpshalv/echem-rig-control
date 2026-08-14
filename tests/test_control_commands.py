import math
from dataclasses import FrozenInstanceError

import pytest

from rig_control.control.commands import (
    CommandSource,
    EnterDeviceSafeState,
    SetMfcFlow,
    SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput,
    SetPowerSupplyVoltage,
)


def test_command_sources_have_stable_values() -> None:
    assert CommandSource.MANUAL.value == "manual"
    assert CommandSource.DIAGNOSTIC.value == "diagnostic"
    assert CommandSource.RECIPE.value == "recipe"
    assert CommandSource.SAFETY_SYSTEM.value == "safety_system"


def test_mfc_flow_command_stores_request() -> None:
    command = SetMfcFlow(
        device_id="dry_gas_mfc",
        flow=25.0,
        source=CommandSource.MANUAL,
    )

    assert command.device_id == "dry_gas_mfc"
    assert command.flow == 25.0
    assert command.source is CommandSource.MANUAL


def test_power_supply_commands_store_requests() -> None:
    voltage = SetPowerSupplyVoltage(
        "main_supply",
        10.0,
        CommandSource.RECIPE,
    )
    current = SetPowerSupplyCurrentLimit(
        "main_supply",
        5.0,
        CommandSource.RECIPE,
    )
    output = SetPowerSupplyOutput(
        "main_supply",
        True,
        CommandSource.RECIPE,
    )

    assert voltage.voltage == 10.0
    assert current.current == 5.0
    assert output.enabled is True


def test_safe_state_defaults_to_safety_source() -> None:
    command = EnterDeviceSafeState("main_supply")

    assert command.device_id == "main_supply"
    assert command.source is CommandSource.SAFETY_SYSTEM


@pytest.mark.parametrize(
    "command",
    [
        lambda: SetMfcFlow(
            "",
            1.0,
            CommandSource.MANUAL,
        ),
        lambda: SetPowerSupplyVoltage(
            "   ",
            1.0,
            CommandSource.MANUAL,
        ),
        lambda: SetPowerSupplyCurrentLimit(
            "",
            1.0,
            CommandSource.MANUAL,
        ),
        lambda: SetPowerSupplyOutput(
            "",
            False,
            CommandSource.MANUAL,
        ),
        lambda: EnterDeviceSafeState(""),
    ],
)
def test_commands_reject_empty_device_id(command: object) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        command()  # type: ignore[operator]


@pytest.mark.parametrize("value", ["25", True, None])
def test_numeric_command_rejects_non_number(
    value: object,
) -> None:
    with pytest.raises(TypeError, match="int or float"):
        SetMfcFlow(
            "dry_gas_mfc",
            value,  # type: ignore[arg-type]
            CommandSource.MANUAL,
        )


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf],
)
def test_numeric_command_rejects_non_finite_value(
    value: float,
) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        SetPowerSupplyVoltage(
            "main_supply",
            value,
            CommandSource.MANUAL,
        )


def test_output_command_requires_boolean() -> None:
    with pytest.raises(TypeError, match="must be Boolean"):
        SetPowerSupplyOutput(
            "main_supply",
            1,  # type: ignore[arg-type]
            CommandSource.MANUAL,
        )


def test_commands_are_immutable() -> None:
    command = SetMfcFlow(
        "dry_gas_mfc",
        25.0,
        CommandSource.MANUAL,
    )

    with pytest.raises(FrozenInstanceError):
        command.flow = 50.0  # type: ignore[misc]