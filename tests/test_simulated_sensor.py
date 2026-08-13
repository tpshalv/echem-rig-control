import pytest

from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.models import DeviceStatus, Quality


def test_simulated_sensor_starts_disconnected() -> None:
    sensor = SimulatedSensor("inlet_temperature", 25.0, "degC")

    assert sensor.status is DeviceStatus.DISCONNECTED


def test_simulated_sensor_requires_a_connection_before_reading() -> None:
    sensor = SimulatedSensor("inlet_temperature", 25.0, "degC")

    with pytest.raises(RuntimeError, match="not ready"):
        sensor.read_measurement()


def test_simulated_sensor_returns_a_good_measurement() -> None:
    sensor = SimulatedSensor("inlet_temperature", 25.0, "degC")
    sensor.connect()

    reading = sensor.read_measurement()

    assert reading.value == 25.0
    assert reading.unit == "degC"
    assert reading.quality is Quality.GOOD


def test_simulated_sensor_value_can_change() -> None:
    sensor = SimulatedSensor("inlet_temperature", 25.0, "degC")
    sensor.connect()

    sensor.set_value(31.5)

    assert sensor.read_measurement().value == 31.5