import pytest

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.in_memory_writer import (
    InMemoryExperimentWriter,
)
from rig_control.data.records import MeasurementRecord
from rig_control.models import (
    Event,
    EventSeverity,
    Measurement,
)


def make_metadata(
    experiment_id: str = "EXP-001",
) -> ExperimentMetadata:
    return ExperimentMetadata(
        experiment_id=experiment_id,
        operator="operator",
    )


def make_record(
    value: float = 25.0,
) -> MeasurementRecord:
    return MeasurementRecord(
        device_id="temperature_sensor",
        channel="temperature",
        measurement=Measurement(value, "degC"),
        sequence_step_id="step_1",
    )


def make_event() -> Event:
    return Event(
        source="temperature_sensor",
        message="Temperature reading unavailable",
        severity=EventSeverity.WARNING,
    )


def test_writer_starts_closed_and_empty() -> None:
    writer = InMemoryExperimentWriter()

    assert writer.is_open is False
    assert writer.metadata is None
    assert writer.measurements == ()
    assert writer.events == ()


def test_open_experiment_stores_metadata() -> None:
    writer = InMemoryExperimentWriter()
    metadata = make_metadata()

    writer.open_experiment(metadata)

    assert writer.is_open is True
    assert writer.metadata is metadata


def test_second_open_is_rejected() -> None:
    writer = InMemoryExperimentWriter()
    writer.open_experiment(make_metadata())

    with pytest.raises(RuntimeError, match="already open"):
        writer.open_experiment(make_metadata("EXP-002"))


def test_open_requires_experiment_metadata() -> None:
    writer = InMemoryExperimentWriter()

    with pytest.raises(
        TypeError,
        match="requires ExperimentMetadata",
    ):
        writer.open_experiment("EXP-001")  # type: ignore[arg-type]


def test_measurement_can_be_recorded() -> None:
    writer = InMemoryExperimentWriter()
    record = make_record()
    writer.open_experiment(make_metadata())

    writer.write_measurement(record)

    assert writer.measurements == (record,)


def test_multiple_measurements_preserve_order() -> None:
    writer = InMemoryExperimentWriter()
    first = make_record(25.0)
    second = make_record(26.0)
    third = make_record(27.0)
    writer.open_experiment(make_metadata())

    writer.write_measurement(first)
    writer.write_measurement(second)
    writer.write_measurement(third)

    assert writer.measurements == (
        first,
        second,
        third,
    )


def test_measurement_requires_correct_record_type() -> None:
    writer = InMemoryExperimentWriter()
    writer.open_experiment(make_metadata())

    with pytest.raises(
        TypeError,
        match="requires MeasurementRecord",
    ):
        writer.write_measurement(25.0)  # type: ignore[arg-type]


def test_event_can_be_recorded() -> None:
    writer = InMemoryExperimentWriter()
    event = make_event()
    writer.open_experiment(make_metadata())

    writer.write_event(event)

    assert writer.events == (event,)


def test_event_requires_correct_type() -> None:
    writer = InMemoryExperimentWriter()
    writer.open_experiment(make_metadata())

    with pytest.raises(TypeError, match="requires Event"):
        writer.write_event("warning")  # type: ignore[arg-type]


def test_writes_require_open_experiment() -> None:
    writer = InMemoryExperimentWriter()

    with pytest.raises(RuntimeError, match="No experiment"):
        writer.write_measurement(make_record())

    with pytest.raises(RuntimeError, match="No experiment"):
        writer.write_event(make_event())


def test_close_finishes_experiment_but_preserves_records() -> None:
    writer = InMemoryExperimentWriter()
    metadata = make_metadata()
    record = make_record()
    event = make_event()

    writer.open_experiment(metadata)
    writer.write_measurement(record)
    writer.write_event(event)
    writer.close_experiment()

    assert writer.is_open is False
    assert writer.metadata is metadata
    assert writer.measurements == (record,)
    assert writer.events == (event,)


def test_close_requires_open_experiment() -> None:
    writer = InMemoryExperimentWriter()

    with pytest.raises(RuntimeError, match="No experiment"):
        writer.close_experiment()


def test_opening_new_experiment_clears_previous_records() -> None:
    writer = InMemoryExperimentWriter()

    writer.open_experiment(make_metadata("EXP-001"))
    writer.write_measurement(make_record())
    writer.write_event(make_event())
    writer.close_experiment()

    new_metadata = make_metadata("EXP-002")
    writer.open_experiment(new_metadata)

    assert writer.metadata is new_metadata
    assert writer.measurements == ()
    assert writer.events == ()