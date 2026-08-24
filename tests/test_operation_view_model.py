from collections import deque
from datetime import UTC, datetime

import pytest

from rig_control.data.in_memory_writer import InMemoryExperimentWriter
from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, Measurement, Quality
from rig_control.polling import PollingBatch, PollingFailure, PollingService
from rig_control.control.service import RigControlService
from rig_control.ui.operation.model import OperationViewModel


FIXED_TIME = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def make_model() -> tuple[
    OperationViewModel,
    DeviceManager,
    PollingService,
    InMemoryExperimentWriter,
]:
    manager = DeviceManager()
    manager.register(SimulatedSensor("temperature", 25.0, "degC"))
    writer = InMemoryExperimentWriter()
    recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    polling = PollingService(
        manager,
        interval_seconds=60.0,
        batch_handler=recorder.record_batch,
    )
    return (
        OperationViewModel(
            manager,
            polling,
            recorder,
            RigControlService(manager),
            profile_id="simulation",
        ),
        manager,
        polling,
        writer,
    )


def measurement_batch(value: float = 25.0) -> PollingBatch:
    return PollingBatch(
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
        measurements=(
            MeasurementRecord(
                device_id="temperature",
                channel="temperature",
                measurement=Measurement(
                    value,
                    "degC",
                    timestamp=FIXED_TIME,
                    quality=Quality.GOOD,
                ),
            ),
        ),
        failures=(),
        events=(),
    )


def test_connect_all_connects_configured_devices() -> None:
    model, manager, _, _ = make_model()

    results = model.connect_all()

    assert all(result.succeeded for result in results)
    assert manager.summaries()[0].status.value == "ready"


def test_monitoring_has_explicit_start_and_stop() -> None:
    model, _, _, _ = make_model()

    started = model.start_monitoring()
    stopped = model.stop_monitoring()

    assert started.succeeded is True
    assert stopped.succeeded is True
    assert model.is_monitoring is False


def test_recording_requires_monitoring_and_output_folder() -> None:
    model, _, _, _ = make_model()

    inactive = model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="data",
        sample_interval_seconds=1.0,
    )
    model.start_monitoring()
    missing_folder = model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="",
        sample_interval_seconds=1.0,
    )
    model.stop_monitoring()

    assert inactive.succeeded is False
    assert "monitoring" in inactive.summary
    assert missing_folder.succeeded is False
    assert "output folder" in missing_folder.summary


def test_start_and_stop_recording_use_existing_recorder() -> None:
    model, _, _, writer = make_model()
    model.start_monitoring()

    started = model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="unused",
        sample_interval_seconds=2.0,
        notes="test notes",
    )

    assert started.succeeded is True
    assert model.is_recording is True
    assert writer.metadata is not None
    assert writer.metadata.extra["rig_profile_id"] == "simulation"
    assert writer.metadata.notes == "test notes"

    stopped = model.stop_recording()
    model.stop_monitoring()
    assert stopped.succeeded is True
    assert model.is_recording is False


def test_monitoring_cannot_stop_while_recording() -> None:
    model, _, _, _ = make_model()
    model.start_monitoring()
    model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="unused",
        sample_interval_seconds=1.0,
    )

    result = model.stop_monitoring()

    assert result.succeeded is False
    assert "Stop experiment recording" in result.summary
    model.shutdown()


def test_polling_queue_updates_live_measurements() -> None:
    model, _, polling, _ = make_model()
    polling.results.put(measurement_batch(27.5))

    count = model.collect_polling_results()

    assert count == 1
    assert model.measurement_rows()[0].value == 27.5
    assert model.measurement_rows()[0].quality == "good"


def test_measurement_history_has_configurable_bounded_default() -> None:
    model, _, polling, _ = make_model()

    assert model.history_limit == 120
    for value in range(125):
        polling._enqueue_result(measurement_batch(float(value)))
    model.collect_polling_results()

    history = model.measurement_history("temperature", "temperature")
    assert len(history) == 120
    assert history[0].value == 5.0
    assert history[-1].value == 124.0


def test_reducing_history_limit_keeps_newest_readings() -> None:
    model, _, polling, _ = make_model()
    for value in range(5):
        polling.results.put(measurement_batch(float(value)))
    model.collect_polling_results()

    model.set_history_limit(3)

    assert model.history_limit == 3
    assert [
        row.value
        for row in model.measurement_history(
            "temperature",
            "temperature",
        )
    ] == [2.0, 3.0, 4.0]


def test_changed_history_limit_applies_to_future_readings() -> None:
    model, _, polling, _ = make_model()
    model.set_history_limit(2)
    for value in range(3):
        polling.results.put(measurement_batch(float(value)))
    model.collect_polling_results()

    assert [
        row.value
        for row in model.measurement_history(
            "temperature",
            "temperature",
        )
    ] == [1.0, 2.0]


@pytest.mark.parametrize("invalid", [0, 1, -1])
def test_history_limit_validation_is_clear(invalid: int) -> None:
    model, _, _, _ = make_model()

    with pytest.raises(ValueError, match="at least 2"):
        model.set_history_limit(invalid)

def test_history_limit_rejects_boolean() -> None:
    model, _, _, _ = make_model()

    with pytest.raises(TypeError, match="integer"):
        model.set_history_limit(True)  # type: ignore[arg-type]


def test_device_warning_persists_until_a_successful_read() -> None:
    model, _, polling, _ = make_model()
    polling.results.put(measurement_batch())
    model.collect_polling_results()
    polling.results.put(
        PollingBatch(
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
            measurements=(),
            failures=(
                PollingFailure(
                    device_id="temperature",
                    operation="read measurements",
                    error_type="OSError",
                    message="connection lost",
                ),
            ),
            events=(Event("temperature", "Polling failed"),),
        )
    )
    model.collect_polling_results()

    assert model.warnings() == (
        ("temperature", "OSError: connection lost"),
    )
    assert model.measurement_rows()[0].quality == "stale"
    assert len(model.measurement_history("temperature", "temperature")) == 1
    assert len(model.events) == 1

    polling.results.put(measurement_batch())
    model.collect_polling_results()
    assert model.warnings() == ()


def test_retained_events_are_bounded() -> None:
    model, _, polling, _ = make_model()
    model._events = deque(maxlen=3)
    for index in range(5):
        polling.results.put(
            PollingBatch(
                started_at=FIXED_TIME,
                finished_at=FIXED_TIME,
                measurements=(),
                failures=(),
                events=(Event("test", f"event {index}"),),
            )
        )
    model.collect_polling_results()

    assert [event.message for event in model.events] == [
        "event 2",
        "event 3",
        "event 4",
    ]


def test_shutdown_stops_feature_services_but_leaves_session_devices_connected() -> None:
    model, manager, _, _ = make_model()
    model.connect_all()
    model.start_monitoring()

    failures = model.shutdown()

    assert failures == ()
    assert model.is_monitoring is False
    assert manager.summaries()[0].status.value == "ready"
