from datetime import UTC, datetime
from pathlib import Path

import pytest

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.in_memory_writer import InMemoryExperimentWriter
from rig_control.data.records import MeasurementRecord
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, EventSeverity, Measurement
from rig_control.polling import PollingBatch


FIXED_TIME = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def metadata() -> ExperimentMetadata:
    return ExperimentMetadata(
        experiment_id="EXP-001",
        operator="operator",
        started_at=FIXED_TIME,
    )


def batch(
    value: float,
    *,
    events: tuple[Event, ...] = (),
) -> PollingBatch:
    return PollingBatch(
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
        measurements=(
            MeasurementRecord(
                device_id="sensor",
                channel="temperature",
                measurement=Measurement(
                    value,
                    "degC",
                    timestamp=FIXED_TIME,
                ),
            ),
        ),
        failures=(),
        events=events,
    )


def make_recorder(
    writer: InMemoryExperimentWriter,
) -> ExperimentRecorder:
    return ExperimentRecorder(
        writer_factory=lambda _root: writer,
    )


def test_recording_has_explicit_start_and_stop() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)

    assert recorder.is_recording is False
    recorder.start(
        metadata=metadata(),
        root_directory=Path("unused"),
    )

    assert recorder.is_recording is True
    assert writer.metadata == metadata()

    recorder.stop()

    assert recorder.is_recording is False
    assert writer.is_open is False


def test_batches_are_ignored_until_recording_starts() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)

    recorder.record_batch(batch(12.0))

    assert writer.measurements == ()


def test_first_batch_is_recorded_immediately() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)
    recorder.start(
        metadata=metadata(),
        root_directory="unused",
    )

    recorder.record_batch(batch(12.0))

    assert [
        record.measurement.value for record in writer.measurements
    ] == [12.0]


def test_every_native_batch_is_recorded_once() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)
    recorder.start(
        metadata=metadata(),
        root_directory="unused",
    )

    recorder.record_batch(batch(1.0))
    recorder.record_batch(batch(2.0))
    recorder.record_batch(batch(3.0))

    assert [
        record.measurement.value for record in writer.measurements
    ] == [1.0, 2.0, 3.0]


def test_due_batch_records_experiment_relevant_events() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)
    event = Event(
        source="mfc_a",
        severity=EventSeverity.ERROR,
        message="Communication lost",
        timestamp=FIXED_TIME,
    )
    recorder.start(
        metadata=metadata(),
        root_directory="unused",
    )

    recorder.record_batch(batch(1.0, events=(event,)))

    assert writer.events == (event,)


def test_events_and_measurements_are_recorded_from_each_batch() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)
    event = Event(
        source="mfc_a",
        severity=EventSeverity.ERROR,
        message="Communication lost",
        timestamp=FIXED_TIME,
    )
    recorder.start(
        metadata=metadata(),
        root_directory="unused",
    )
    recorder.record_batch(batch(1.0))
    recorder.record_batch(batch(2.0, events=(event,)))

    assert writer.events == (event,)
    assert [
        record.measurement.value for record in writer.measurements
    ] == [1.0, 2.0]


def test_start_and_stop_lifecycle_errors_are_clear() -> None:
    writer = InMemoryExperimentWriter()
    recorder = make_recorder(writer)

    with pytest.raises(RuntimeError, match="No experiment"):
        recorder.stop()

    recorder.start(
        metadata=metadata(),
        root_directory="unused",
    )
    with pytest.raises(RuntimeError, match="already"):
        recorder.start(
            metadata=metadata(),
            root_directory="unused",
        )


def test_record_batch_requires_polling_batch() -> None:
    recorder = ExperimentRecorder()

    with pytest.raises(TypeError, match="PollingBatch"):
        recorder.record_batch(object())  # type: ignore[arg-type]
