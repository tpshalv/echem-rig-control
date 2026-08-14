import inspect

import pytest

from rig_control.devices.power_supply import PowerSupply, PowerSupplyLimits


def test_power_supply_is_abstract() -> None:
    assert inspect.isabstract(PowerSupply)


def test_power_supply_defines_expected_contract() -> None:
    assert PowerSupply.__abstractmethods__ == {
        "device_id",
        "status",
        "limits",
        "voltage_setpoint",
        "current_limit",
        "output_enabled",
        "connect",
        "disconnect",
        "set_voltage",
        "set_current_limit",
        "set_output_enabled",
        "measure_voltage",
        "measure_current",
        "enter_safe_state",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_voltage", 0),
        ("maximum_voltage", -1),
        ("maximum_current", 0),
        ("maximum_current", -1),
        ("maximum_power", 0),
        ("maximum_power", -1),
    ],
)
def test_power_supply_limits_must_be_positive(
    field: str,
    value: float,
) -> None:
    values = {
        "maximum_voltage": 30.0,
        "maximum_current": 108.0,
        "maximum_power": 1080.0,
    }
    values[field] = value

    with pytest.raises(ValueError, match="greater than zero"):
        PowerSupplyLimits(**values)


def test_2260b_limits_can_be_represented() -> None:
    limits = PowerSupplyLimits(
        maximum_voltage=30.0,
        maximum_current=108.0,
        maximum_power=1080.0,
    )

    assert limits.maximum_voltage == 30.0
    assert limits.maximum_current == 108.0
    assert limits.maximum_power == 1080.0