from queue import Empty
from threading import Event, Lock, Thread
from time import monotonic

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


class CountingSensor(SimulatedSensor):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id, 1.0, "V")
        self._count_lock = Lock()
        self.read_count = 0
        self.active_reads = 0
        self.maximum_active_reads = 0
        self.read_delay_seconds = 0.0

    def read_measurement(self) -> Measurement:
        with self._count_lock:
            self.read_count += 1
            self.active_reads += 1
            self.maximum_active_reads = max(
                self.maximum_active_reads,
                self.active_reads,
            )
        if self.read_delay_seconds:
            Event().wait(self.read_delay_seconds)
        try:
            return super().read_measurement()
        finally:
            with self._count_lock:
                self.active_reads -= 1


class BlockingSensor(SimulatedSensor):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id, 2.0, "V")
        self.read_started = Event()
        self.release_read = Event()

    def read_measurement(self) -> Measurement:
        self.read_started.set()
        if not self.release_read.wait(2.0):
            raise TimeoutError("test did not release blocked read")
        return super().read_measurement()


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
    with pytest.raises(ValueError, match="queue capacity"):
        PollingService(DeviceManager(), results_queue_capacity=0)


def test_full_results_queue_drops_oldest_and_reports_overflow() -> None:
    recorded = []
    service = PollingService(
        DeviceManager(),
        results_queue_capacity=2,
        event_sink=lambda event, details: recorded.append((event, details)),
    )
    batches = [service.poll_once() for _ in range(3)]

    for batch in batches:
        service._enqueue_result(batch)

    retained = [service.results.get_nowait() for _ in range(2)]
    metrics = service.diagnostic_metrics()
    assert retained[0] is batches[1]
    assert retained[1].started_at == batches[2].started_at
    assert len(retained[1].events) == 1
    assert "dropping the oldest" in retained[1].events[0].message
    assert metrics["dropped_results_batches"] == 1
    assert metrics["results_queue_capacity"] == 2
    assert metrics["results_overflow_active"] is True
    assert len(recorded) == 1


def test_results_queue_reports_recovery_after_consumer_catches_up() -> None:
    recorded = []
    service = PollingService(
        DeviceManager(),
        results_queue_capacity=4,
        event_sink=lambda event, details: recorded.append((event, details)),
    )
    for _ in range(5):
        service._enqueue_result(service.poll_once())
    for _ in range(3):
        service.results.get_nowait()

    service._enqueue_result(service.poll_once())

    assert service.diagnostic_metrics()["results_overflow_active"] is False
    assert len(recorded) == 2
    assert "recovered" in recorded[-1][0].message


def test_unknown_device_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="not registered measurement"):
        PollingService(
            DeviceManager(),
            device_intervals_seconds={"missing": 1.0},
        )


def test_devices_are_read_at_independent_configured_rates() -> None:
    manager = DeviceManager()
    fast = CountingSensor("fast")
    slow = CountingSensor("slow")
    fast.connect()
    slow.connect()
    manager.register(fast)
    manager.register(slow)
    service = PollingService(
        manager,
        publish_interval_seconds=0.02,
        device_intervals_seconds={"fast": 0.01, "slow": 0.08},
    )

    service.start()
    deadline = monotonic() + 1.0
    try:
        while fast.read_count < 8 and monotonic() < deadline:
            Event().wait(0.005)
    finally:
        service.stop(timeout=1.0)

    assert fast.read_count >= 8
    assert slow.read_count >= 1
    assert fast.read_count >= slow.read_count * 3


def test_blocked_slow_device_does_not_delay_fast_reads_or_publish() -> None:
    manager = DeviceManager()
    blocked = BlockingSensor("blocked")
    fast = CountingSensor("fast")
    blocked.connect()
    fast.connect()
    manager.register(blocked)
    manager.register(fast)
    service = PollingService(
        manager,
        publish_interval_seconds=0.02,
        device_intervals_seconds={"blocked": 1.0, "fast": 0.01},
    )

    service.start()
    try:
        assert blocked.read_started.wait(1.0)
        batches = [service.results.get(timeout=0.3) for _ in range(3)]
        assert fast.read_count >= 3
        assert all(
            any(
                record.device_id == "fast"
                for record in batch.measurements
            )
            for batch in batches
        )
    finally:
        blocked.release_read.set()
        service.stop(timeout=1.0)


def test_same_device_never_has_overlapping_reads() -> None:
    manager = DeviceManager()
    sensor = CountingSensor("sensor")
    sensor.read_delay_seconds = 0.03
    sensor.connect()
    manager.register(sensor)
    service = PollingService(
        manager,
        publish_interval_seconds=0.02,
        device_intervals_seconds={"sensor": 0.005},
    )

    service.start()
    try:
        Event().wait(0.12)
    finally:
        service.stop(timeout=1.0)

    assert sensor.read_count >= 2
    assert sensor.maximum_active_reads == 1


def test_published_cache_preserves_original_measurement_timestamp() -> None:
    manager = DeviceManager()
    sensor = CountingSensor("slow")
    sensor.connect()
    manager.register(sensor)
    service = PollingService(
        manager,
        publish_interval_seconds=0.02,
        device_intervals_seconds={"slow": 1.0},
    )

    service.start()
    try:
        first = service.results.get(timeout=0.3)
        second = service.results.get(timeout=0.3)
    finally:
        service.stop(timeout=1.0)

    assert first.measurements[0].measurement.timestamp == (
        second.measurements[0].measurement.timestamp
    )
    assert sensor.read_count == 1


def test_polling_batch_handler_receives_results() -> None:
    manager = DeviceManager()
    manager.register(connected_sensor("sensor"))
    received = []
    service = PollingService(manager, batch_handler=received.append)

    returned = service.poll_once()

    assert received == [returned]


def test_batch_handler_failure_is_reported_without_losing_results() -> None:
    manager = DeviceManager()
    manager.register(connected_sensor("sensor"))

    def fail(_batch: object) -> None:
        raise OSError("simulated disk failure")

    service = PollingService(manager, batch_handler=fail)

    batch_with_failure = service.poll_once()
    repeated_failure = service.poll_once()

    assert len(batch_with_failure.measurements) == 1
    assert len(batch_with_failure.events) == 1
    assert "simulated disk failure" in batch_with_failure.events[0].message
    assert repeated_failure.events == ()


def test_polling_events_are_published_to_event_sink() -> None:
    manager = DeviceManager()
    sensor = FailingMeasurementSource("sensor")
    sensor.connect()
    manager.register(sensor)
    recorded = []
    service = PollingService(
        manager,
        event_sink=lambda event, details: recorded.append((event, details)),
    )

    returned = service.poll_once()

    assert recorded == [(returned.events[0], None)]


def test_event_sink_failure_does_not_stop_polling_results() -> None:
    manager = DeviceManager()
    sensor = FailingMeasurementSource("sensor")
    sensor.connect()
    manager.register(sensor)

    def fail_to_log(_event: object, _details: object) -> None:
        raise OSError("log disk unavailable")

    service = PollingService(manager, event_sink=fail_to_log)

    returned = service.poll_once()

    assert len(returned.failures) == 1
    assert len(returned.events) == 2
    assert "Technical event sink failed" in returned.events[1].message


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
