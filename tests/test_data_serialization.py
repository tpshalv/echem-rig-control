import json
from datetime import datetime, timezone

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.data.serialization import (
    DATA_SCHEMA_VERSION,
    event_to_dict,
    experiment_metadata_to_dict,
    measurement_record_to_dict,
)
from rig_control.models import (
    Event,
    EventSeverity,
    Measurement,
    Quality,
)


FIXED_TIME = datetime(
    2026,
    8,
    14,
    12,
    30,
    45,
    tzinfo=timezone.utc,
)


def test_data_schema_version_is_one() -> None:
    assert DATA_SCHEMA_VERSION == 1


def test_experiment_metadata_serialization() -> None:
    metadata = ExperimentMetadata(
        experiment_id="EXP-001",
        operator="Tom Shalvey",
        started_at=FIXED_TIME,
        experiment_type="durability",
        sequence_file="sequence.toml",
        notes="Example test",
        extra={
            "catalyst_id": "CAT-42",
            "loading_mg_cm2": 0.5,
        },
    )

    serialized = experiment_metadata_to_dict(metadata)

    assert serialized == {
        "schema_version": 1,
        "experiment_id": "EXP-001",
        "operator": "Tom Shalvey",
        "started_at": "2026-08-14T12:30:45+00:00",
        "experiment_type": "durability",
        "sequence_file": "sequence.toml",
        "notes": "Example test",
        "extra": {
            "catalyst_id": "CAT-42",
            "loading_mg_cm2": 0.5,
        },
    }

    json.dumps(serialized)


def test_measurement_record_serialization() -> None:
    measurement = Measurement(
        value=25.4,
        unit="degC",
        timestamp=FIXED_TIME,
        quality=Quality.GOOD,
    )
    record = MeasurementRecord(
        device_id="outlet_temperature",
        channel="temperature",
        measurement=measurement,
        sequence_step_id="heating",
    )

    serialized = measurement_record_to_dict(record)

    assert serialized == {
        "device_id": "outlet_temperature",
        "channel": "temperature",
        "timestamp": "2026-08-14T12:30:45+00:00",
        "value": 25.4,
        "unit": "degC",
        "quality": "good",
        "sequence_step_id": "heating",
    }

    json.dumps(serialized)


def test_event_serialization() -> None:
    event = Event(
        source="temperature_controller",
        message="Temperature exceeded warning limit",
        severity=EventSeverity.WARNING,
        timestamp=FIXED_TIME,
    )

    serialized = event_to_dict(event)

    assert serialized == {
        "source": "temperature_controller",
        "timestamp": "2026-08-14T12:30:45+00:00",
        "severity": "warning",
        "message": "Temperature exceeded warning limit",
    }

    json.dumps(serialized)