from queue import Empty
from threading import Event, Thread

import pytest

from rig_control.devices.manager import DeviceManager
from rig_control.devices.measurement_source import (
    DeviceMeasurement,
    MeasurementSource,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_power_supply import SimulatedPowerSupply
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.models import EventSeverity, Measurement
from rig_control.polling import PollingService


class FailingMeasurementSource(SimulatedSensor):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id, 0.0, "V")
        self.should_fail = True

    def read_measurement(self) -> Measurement:
        if self.should_fail:
            raise OSError("simulated read failure")
        return super().read_measurement()


class NonDeviceMeasurementSource(MeasurementSource):
    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        return ()


def connected_sensor(
    device_id: str,
    value: float = 12.5,
) -> SimulatedSensor:
    sensor = SimulatedSensor(device_id, value, "degC")
    sensor.connect()
    return sensor


def test_poll_once_returns_named_measurements_from_all_devices() -> None:
    manager = DeviceManager()
    sensor = connected_sensor("temperature")
    supply = SimulatedPowerSupply(
        "supply",
        PowerSupplyLimits(30.0, 10.0, 100.0),
    )
    supply.connect()
    supply.set_simulated_measurement(voltage=4.2, current=1.5)
    manager.register(sensor)
    manager.register(supply)

    batch = PollingService(manager).poll_once()

    by_source = {
        (record.device_id, record.channel): record.measurement.value
        for record in batch.measurements
    }
    assert by_source == {
        ("temperature", "measurement"): 12.5,
        ("supply", "voltage"): 4.2,
        ("supply", "current"): 1.5,
    }
    assert batch.failures == ()
    assert batch.events == ()


def test_one_device_failure_does_not_prevent_other_measurements() -> None:
    manager = DeviceManager()
    failing = FailingMeasurementSource("failing")
    failing.connect()
    manager.register(failing)
    manager.register(connected_sensor("working", 7.0))

    batch = PollingService(manager).poll_once()

    assert [record.device_id for record in batch.measurements] == ["working"]
    assert len(batch.failures) == 1
    assert batch.failures[0].device_id == "failing"
    assert batch.failures[0].error_type == "OSError"
    assert "simulated read failure" in batch.failures[0].message
    assert len(batch.events) == 1
    assert batch.events[0].severity is EventSeverity.ERROR


def test_failure_event_is_not_repeated_and_recovery_is_reported() -> None:
    manager = DeviceManager()
    sensor = FailingMeasurementSource("sensor")
    sensor.connect()
    manager.register(sensor)
    service = PollingService(manager)

    first = service.poll_once()
    second = service.poll_once()
    sensor.should_fail = False
    recovered = service.poll_once()

    assert len(first.events) == 1
    assert second.events == ()
    assert second.failures[0].device_id == "sensor"
    assert len(recovered.measurements) == 1
    assert len(recovered.events) == 1
    assert "recovered" in recovered.events[0].message
    assert recovered.events[0].severity is EventSeverity.INFO


def test_background_service_places_batches_on_queue_and_stops() -> None:
    manager = DeviceManager()
    manager.register(connected_sensor("sensor"))
    service = PollingService(manager, interval_seconds=0.01)

    service.start()
    try:
        batch = service.results.get(timeout=1.0)
    except Empty:
        pytest.fail("Polling service produced no result")
    finally:
        service.stop(timeout=1.0)

    assert service.is_running is False
    assert batch.measurements[0].device_id == "sensor"


def test_start_is_rejected_while_service_is_running() -> None:
    service = PollingService(DeviceManager(), interval_seconds=0.1)
    service.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            service.start()
    finally:
        service.stop(timeout=1.0)


def test_invalid_polling_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="interval"):
        PollingService(DeviceManager(), interval_seconds=0)
    with pytest.raises(ValueError, match="worker"):
        PollingService(DeviceManager(), max_workers=0)


def test_device_operation_lock_serializes_access() -> None:
    manager = DeviceManager()
    manager.register(connected_sensor("sensor"))
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first_operation() -> None:
        with manager.operation("sensor"):
            first_entered.set()
            release_first.wait(1.0)

    def second_operation() -> None:
        first_entered.wait(1.0)
        with manager.operation("sensor"):
            second_entered.set()

    first_thread = Thread(target=first_operation)
    second_thread = Thread(target=second_operation)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(1.0)
    assert second_entered.wait(0.05) is False

    release_first.set()
    first_thread.join(1.0)
    second_thread.join(1.0)

    assert second_entered.is_set()


def test_non_device_measurement_source_is_not_a_registered_device() -> None:
    manager = DeviceManager()

    with pytest.raises(TypeError, match="Device interface"):
        manager.register(NonDeviceMeasurementSource())  # type: ignore[arg-type]
