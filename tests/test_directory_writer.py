import json
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

from rig_control.data.directory_writer import (
    DirectoryExperimentWriter,
)
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
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


def test_configuration_snapshot_is_saved_beside_the_run_metadata(tmp_path):
    snapshot = {"format": "rig-control.run-context", "format_version": 1}
    metadata = replace(make_metadata(), extra={"configuration_snapshot_json": json.dumps(snapshot)})
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(metadata)
    directory = writer.experiment_directory
    assert json.loads((directory / "configuration.json").read_text(encoding="utf-8")) == snapshot
    persisted = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    assert json.loads(persisted["extra"]["configuration_snapshot_json"]) == snapshot
    writer.close_experiment()


def make_metadata(
    experiment_id: str = "EXP-001",
) -> ExperimentMetadata:
    return ExperimentMetadata(
        experiment_id=experiment_id,
        operator="Tom Shalvey",
        started_at=FIXED_TIME,
        experiment_type="durability",
        extra={"catalyst_id": "CAT-42"},
    )


def make_measurement_record() -> MeasurementRecord:
    return MeasurementRecord(
        device_id="outlet_temperature",
        channel="temperature",
        measurement=Measurement(
            value=25.4,
            unit="degC",
            timestamp=FIXED_TIME,
            quality=Quality.GOOD,
        ),
        sequence_step_id="heating",
    )


def make_event() -> Event:
    return Event(
        source="temperature_controller",
        message="Temperature exceeded warning limit",
        severity=EventSeverity.WARNING,
        timestamp=FIXED_TIME,
    )


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_lines(
    path: Path,
) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def test_writer_syncs_journals_to_disk(tmp_path: Path, monkeypatch) -> None:
    syncs = []
    monkeypatch.setattr("rig_control.data.directory_writer.os.fsync", syncs.append)
    writer = DirectoryExperimentWriter(tmp_path, live_export_interval_seconds=0)

    writer.open_experiment(make_metadata())
    writer.write_measurement(make_measurement_record())
    writer.write_event(make_event())
    writer.close_experiment()

    assert len(syncs) >= 4


def test_measurement_batch_uses_one_journal_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    syncs = []
    monkeypatch.setattr("rig_control.data.directory_writer.os.fsync", syncs.append)
    writer = DirectoryExperimentWriter(tmp_path, live_export_interval_seconds=0)
    writer.open_experiment(make_metadata())
    syncs.clear()

    writer.write_measurements(
        (make_measurement_record(), make_measurement_record())
    )

    assert len(syncs) == 1


def test_writer_starts_closed() -> None:
    writer = DirectoryExperimentWriter("unused")

    assert writer.is_open is False
    assert writer.experiment_directory is None


def test_open_creates_self_contained_experiment_directory(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)

    writer.open_experiment(make_metadata())

    expected_directory = (
        tmp_path / "20260814T123045Z_EXP-001"
    )

    assert writer.is_open is True
    assert writer.experiment_directory == expected_directory
    assert (expected_directory / "metadata.json").is_file()
    assert (
        expected_directory / "recording-state.json"
    ).is_file()
    assert (
        expected_directory
        / "measurements.journal.jsonl"
    ).is_file()
    assert (
        expected_directory
        / "events.journal.jsonl"
    ).is_file()


def test_metadata_file_contains_experiment_information(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(make_metadata())

    assert writer.experiment_directory is not None

    metadata = read_json(
        writer.experiment_directory / "metadata.json"
    )

    assert metadata["schema_version"] == 1
    assert metadata["experiment_id"] == "EXP-001"
    assert metadata["operator"] == "Tom Shalvey"
    assert metadata["extra"] == {
        "catalyst_id": "CAT-42"
    }


def test_open_marks_recording_as_in_progress(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(make_metadata())

    assert writer.experiment_directory is not None

    state = read_json(
        writer.experiment_directory
        / "recording-state.json"
    )

    assert state["experiment_id"] == "EXP-001"
    assert state["state"] == "recording"


def test_measurements_are_appended_as_complete_lines(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(make_metadata())

    writer.write_measurement(make_measurement_record())
    writer.write_measurement(make_measurement_record())

    assert writer.experiment_directory is not None

    records = read_json_lines(
        writer.experiment_directory
        / "measurements.journal.jsonl"
    )

    assert len(records) == 2
    assert records[0]["device_id"] == "outlet_temperature"
    assert records[0]["channel"] == "temperature"
    assert records[0]["value"] == 25.4
    assert records[0]["quality"] == "good"


def test_live_wide_csv_is_refreshed_while_recording(tmp_path: Path) -> None:
    writer = DirectoryExperimentWriter(
        tmp_path,
        export_bin_seconds=0.5,
        live_export_interval_seconds=1,
    )
    writer.open_experiment(make_metadata())

    writer.write_measurement(make_measurement_record())

    assert writer.experiment_directory is not None
    csv_path = writer.experiment_directory / "measurements-wide.csv"
    assert writer._live_export_future is not None
    writer._live_export_future.result(timeout=2)
    assert csv_path.is_file()
    assert "outlet_temperature.temperature [degC]" in csv_path.read_text(
        encoding="utf-8-sig"
    )
    writer.close_experiment()


def test_events_are_appended_as_complete_lines(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(make_metadata())

    writer.write_event(make_event())

    assert writer.experiment_directory is not None

    events = read_json_lines(
        writer.experiment_directory
        / "events.journal.jsonl"
    )

    assert len(events) == 1
    assert events[0]["severity"] == "warning"
    assert events[0]["source"] == "temperature_controller"


def test_close_marks_experiment_complete(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(make_metadata())

    assert writer.experiment_directory is not None
    directory = writer.experiment_directory

    writer.close_experiment()

    state = read_json(
        directory / "recording-state.json"
    )

    assert writer.is_open is False
    assert state["state"] == "complete"
    assert state["experiment_id"] == "EXP-001"


def test_existing_experiment_directory_is_not_overwritten(
    tmp_path: Path,
) -> None:
    first_writer = DirectoryExperimentWriter(tmp_path)
    first_writer.open_experiment(make_metadata())
    first_writer.close_experiment()

    second_writer = DirectoryExperimentWriter(tmp_path)

    with pytest.raises(
        FileExistsError,
        match="already exists",
    ):
        second_writer.open_experiment(make_metadata())


def test_experiment_id_is_made_safe_for_folder_name(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)

    writer.open_experiment(
        make_metadata("EXP 001: catalyst/test")
    )

    assert writer.experiment_directory is not None
    assert writer.experiment_directory.name == (
        "20260814T123045Z_EXP_001_catalyst_test"
    )


def test_write_and_close_require_open_experiment(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)

    with pytest.raises(RuntimeError, match="No experiment"):
        writer.write_measurement(make_measurement_record())

    with pytest.raises(RuntimeError, match="No experiment"):
        writer.write_event(make_event())

    with pytest.raises(RuntimeError, match="No experiment"):
        writer.close_experiment()


def test_second_open_is_rejected_while_recording(
    tmp_path: Path,
) -> None:
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(make_metadata())

    with pytest.raises(RuntimeError, match="already open"):
        writer.open_experiment(make_metadata("EXP-002"))
