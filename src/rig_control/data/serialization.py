from typing import Any

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.models import Event


DATA_SCHEMA_VERSION = 1


def experiment_metadata_to_dict(
    metadata: ExperimentMetadata,
) -> dict[str, Any]:
    """Convert experiment metadata into JSON-compatible values."""

    return {
        "schema_version": DATA_SCHEMA_VERSION,
        "experiment_id": metadata.experiment_id,
        "operator": metadata.operator,
        "started_at": metadata.started_at.isoformat(),
        "experiment_type": metadata.experiment_type,
        "sequence_file": metadata.sequence_file,
        "notes": metadata.notes,
        "extra": dict(metadata.extra),
    }


def measurement_record_to_dict(
    record: MeasurementRecord,
) -> dict[str, Any]:
    """Convert one measurement record into portable values."""

    measurement = record.measurement

    return {
        "device_id": record.device_id,
        "channel": record.channel,
        "timestamp": measurement.timestamp.isoformat(),
        "value": measurement.value,
        "unit": measurement.unit,
        "quality": measurement.quality.value,
        "sequence_step_id": record.sequence_step_id,
    }


def event_to_dict(event: Event) -> dict[str, Any]:
    """Convert one event into portable values."""

    return {
        "source": event.source,
        "timestamp": event.timestamp.isoformat(),
        "severity": event.severity.value,
        "message": event.message,
    }