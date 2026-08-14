import pytest

from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_power_supply import SimulatedPowerSupply
from rig_control.models import DeviceStatus


def make_supply() -> SimulatedPowerSupply:
    return SimulatedPowerSupply(
        device_id="main_power_supply",
        limits=PowerSupplyLimits(
            maximum_voltage=30.0,
            maximum_current=108.0,
            maximum_power=1080.0,
        ),
    )


def test_supply_starts_disconnected_and_safe() -> None:
    supply = make_supply()

    assert supply.status is DeviceStatus.DISCONNECTED
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 0.0
    assert supply.output_enabled is False


def test_supply_rejects_commands_while_disconnected() -> None:
    supply = make_supply()

    with pytest.raises(RuntimeError, match="not ready"):
        supply.set_voltage(5.0)


def test_supply_accepts_valid_operating_point() -> None:
    supply = make_supply()
    supply.connect()

    supply.set_voltage(10.0)
    supply.set_current_limit(50.0)
    supply.set_output_enabled(True)

    assert supply.voltage_setpoint == 10.0
    assert supply.current_limit == 50.0
    assert supply.output_enabled is True


@pytest.mark.parametrize(
    ("voltage", "current", "message"),
    [
        (-1.0, 0.0, "Voltage cannot be negative"),
        (0.0, -1.0, "Current cannot be negative"),
        (31.0, 0.0, "exceeds configured maximum"),
        (0.0, 109.0, "exceeds configured maximum"),
        (30.0, 40.0, "1200.0 W"),
    ],
)
def test_supply_rejects_unsafe_operating_points(
    voltage: float,
    current: float,
    message: str,
) -> None:
    supply = make_supply()
    supply.connect()

    with pytest.raises(ValueError, match=message):
        supply.set_voltage(voltage)
        supply.set_current_limit(current)


def test_supply_returns_measurements_with_units() -> None:
    supply = make_supply()
    supply.connect()
    supply.set_simulated_measurement(voltage=9.8, current=42.1)

    voltage = supply.measure_voltage()
    current = supply.measure_current()

    assert voltage.value == 9.8
    assert voltage.unit == "V"
    assert current.value == 42.1
    assert current.unit == "A"


def test_disabling_output_clears_measurements() -> None:
    supply = make_supply()
    supply.connect()
    supply.set_simulated_measurement(voltage=9.8, current=42.1)

    supply.set_output_enabled(False)

    assert supply.measure_voltage().value == 0.0
    assert supply.measure_current().value == 0.0


def test_safe_state_disables_output_and_clears_setpoints() -> None:
    supply = make_supply()
    supply.connect()
    supply.set_voltage(10.0)
    supply.set_current_limit(50.0)
    supply.set_output_enabled(True)

    supply.enter_safe_state()

    assert supply.output_enabled is False
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 0.0


def test_disconnect_applies_safe_state() -> None:
    supply = make_supply()
    supply.connect()
    supply.set_voltage(10.0)
    supply.set_current_limit(50.0)
    supply.set_output_enabled(True)

    supply.disconnect()

    assert supply.status is DeviceStatus.DISCONNECTED
    assert supply.output_enabled is False
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 0.0