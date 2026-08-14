from datetime import datetime, timezone
from typing import Any

import pytest

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.models import Measurement, Quality


def test_experiment_metadata_stores_required_information() -> None:
    started_at = datetime(
        2026,
        8,
        14,
        10,
        30,
        tzinfo=timezone.utc,
    )

    metadata = ExperimentMetadata(
        experiment_id="EXP-2026-001",
        operator="Tom Shalvey",
        started_at=started_at,
        experiment_type="electrolyser durability",
        sequence_file="durability_sequence.toml",
        notes="Initial software-controlled test",
    )

    assert metadata.experiment_id == "EXP-2026-001"
    assert metadata.operator == "Tom Shalvey"
    assert metadata.started_at == started_at
    assert metadata.experiment_type == "electrolyser durability"
    assert metadata.sequence_file == "durability_sequence.toml"
    assert metadata.notes == "Initial software-controlled test"


def test_default_start_time_is_timezone_aware() -> None:
    metadata = ExperimentMetadata(
        experiment_id="EXP-001",
        operator="operator",
    )

    assert metadata.started_at.tzinfo is not None
    assert metadata.started_at.utcoffset() is not None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("experiment_id", ""),
        ("experiment_id", "   "),
        ("operator", ""),
        ("operator", "   "),
    ],
)
def test_required_text_cannot_be_empty(
    field_name: str,
    value: str,
) -> None:
    arguments = {
        "experiment_id": "EXP-001",
        "operator": "operator",
        field_name: value,
    }

    with pytest.raises(ValueError, match="cannot be empty"):
        ExperimentMetadata(**arguments)


def test_naive_start_time_is_rejected() -> None:
    naive_time = datetime(2026, 8, 14, 10, 30)

    with pytest.raises(ValueError, match="timezone"):
        ExperimentMetadata(
            experiment_id="EXP-001",
            operator="operator",
            started_at=naive_time,
        )


def test_extra_metadata_supports_custom_fields() -> None:
    metadata = ExperimentMetadata(
        experiment_id="EXP-001",
        operator="operator",
        extra={
            "catalyst_id": "CAT-42",
            "catalyst_loading_mg_cm2": 0.5,
            "active_area_cm2": 25.0,
            "membrane": "example membrane",
            "repeat_number": 2,
            "approved": True,
        },
    )

    assert metadata.extra["catalyst_id"] == "CAT-42"
    assert metadata.extra["active_area_cm2"] == 25.0
    assert metadata.extra["approved"] is True


def test_extra_metadata_is_copied_and_immutable() -> None:
    original = {"catalyst_id": "CAT-42"}

    metadata = ExperimentMetadata(
        experiment_id="EXP-001",
        operator="operator",
        extra=original,
    )

    original["catalyst_id"] = "CHANGED"

    assert metadata.extra["catalyst_id"] == "CAT-42"

    immutable_extra: Any = metadata.extra

    with pytest.raises(TypeError):
        immutable_extra["new_field"] = "new value"


def test_empty_extra_metadata_field_name_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Metadata field name cannot be empty",
    ):
        ExperimentMetadata(
            experiment_id="EXP-001",
            operator="operator",
            extra={"": "value"},
        )


def test_unsupported_extra_metadata_value_is_rejected() -> None:
    with pytest.raises(
        TypeError,
        match="unsupported value type",
    ):
        ExperimentMetadata(
            experiment_id="EXP-001",
            operator="operator",
            extra={
                "invalid": ["lists are not scalar"],
            },  # type: ignore[dict-item]
        )


def test_measurement_record_labels_measurement_source() -> None:
    measurement = Measurement(
        value=25.4,
        unit="degC",
        quality=Quality.GOOD,
    )

    record = MeasurementRecord(
        device_id="outlet_temperature",
        channel="temperature",
        measurement=measurement,
        sequence_step_id="heat_step",
    )

    assert record.device_id == "outlet_temperature"
    assert record.channel == "temperature"
    assert record.measurement is measurement
    assert record.sequence_step_id == "heat_step"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("device_id", ""),
        ("device_id", "   "),
        ("channel", ""),
        ("channel", "   "),
    ],
)
def test_measurement_record_requires_source_labels(
    field_name: str,
    value: str,
) -> None:
    arguments = {
        "device_id": "sensor",
        "channel": "temperature",
        "measurement": Measurement(25.0, "degC"),
        field_name: value,
    }

    with pytest.raises(ValueError, match="cannot be empty"):
        MeasurementRecord(**arguments)


def test_measurement_record_requires_measurement_object() -> None:
    with pytest.raises(
        TypeError,
        match="must contain a Measurement",
    ):
        MeasurementRecord(
            device_id="sensor",
            channel="temperature",
            measurement=25.0,  # type: ignore[arg-type]
        )


def test_empty_sequence_step_id_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Sequence step ID cannot be empty",
    ):
        MeasurementRecord(
            device_id="sensor",
            channel="temperature",
            measurement=Measurement(25.0, "degC"),
            sequence_step_id="",
        )