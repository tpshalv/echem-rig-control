import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.data.serialization import (
    DATA_SCHEMA_VERSION,
    event_to_dict,
    experiment_metadata_to_dict,
    measurement_record_to_dict,
)
from rig_control.data.writer import ExperimentWriter
from rig_control.models import Event


class DirectoryExperimentWriter(ExperimentWriter):
    """Write one experiment into a self-contained directory."""

    def __init__(self, root_directory: str | Path) -> None:
        self._root_directory = Path(root_directory)
        self._experiment_directory: Path | None = None
        self._metadata: ExperimentMetadata | None = None
        self._measurement_file: TextIO | None = None
        self._event_file: TextIO | None = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def experiment_directory(self) -> Path | None:
        return self._experiment_directory

    def open_experiment(
        self,
        metadata: ExperimentMetadata,
    ) -> None:
        if self.is_open:
            raise RuntimeError(
                "An experiment is already open for recording"
            )

        if not isinstance(metadata, ExperimentMetadata):
            raise TypeError(
                "Experiment writer requires ExperimentMetadata"
            )

        directory_name = self._make_directory_name(metadata)
        experiment_directory = (
            self._root_directory / directory_name
        )

        if experiment_directory.exists():
            raise FileExistsError(
                "Experiment output directory already exists: "
                f"{experiment_directory}"
            )

        try:
            experiment_directory.mkdir(
                parents=True,
                exist_ok=False,
            )

            self._write_json_atomically(
                experiment_directory / "metadata.json",
                experiment_metadata_to_dict(metadata),
            )
            self._write_json_atomically(
                experiment_directory / "recording-state.json",
                {
                    "schema_version": DATA_SCHEMA_VERSION,
                    "experiment_id": metadata.experiment_id,
                    "state": "recording",
                    "updated_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

            measurement_file = (
                experiment_directory
                / "measurements.journal.jsonl"
            ).open(
                "a",
                encoding="utf-8",
                buffering=1,
            )
            event_file = (
                experiment_directory
                / "events.journal.jsonl"
            ).open(
                "a",
                encoding="utf-8",
                buffering=1,
            )

        except Exception:
            self._close_files()
            self._experiment_directory = None
            self._metadata = None
            self._is_open = False
            raise

        self._experiment_directory = experiment_directory
        self._metadata = metadata
        self._measurement_file = measurement_file
        self._event_file = event_file
        self._is_open = True

    def write_measurement(
        self,
        record: MeasurementRecord,
    ) -> None:
        self._require_open()

        if not isinstance(record, MeasurementRecord):
            raise TypeError(
                "Experiment writer requires MeasurementRecord"
            )

        assert self._measurement_file is not None

        self._write_json_line(
            self._measurement_file,
            measurement_record_to_dict(record),
        )

    def write_event(self, event: Event) -> None:
        self._require_open()

        if not isinstance(event, Event):
            raise TypeError(
                "Experiment writer requires Event"
            )

        assert self._event_file is not None

        self._write_json_line(
            self._event_file,
            event_to_dict(event),
        )

    def close_experiment(self) -> None:
        self._require_open()

        experiment_directory = self._experiment_directory
        metadata = self._metadata

        self._close_files()
        self._is_open = False

        assert experiment_directory is not None
        assert metadata is not None

        self._write_json_atomically(
            experiment_directory / "recording-state.json",
            {
                "schema_version": DATA_SCHEMA_VERSION,
                "experiment_id": metadata.experiment_id,
                "state": "complete",
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            },
        )

    def _require_open(self) -> None:
        if not self.is_open:
            raise RuntimeError(
                "No experiment is open for recording"
            )

    def _close_files(self) -> None:
        if self._measurement_file is not None:
            self._measurement_file.close()
            self._measurement_file = None

        if self._event_file is not None:
            self._event_file.close()
            self._event_file = None

    @staticmethod
    def _make_directory_name(
        metadata: ExperimentMetadata,
    ) -> str:
        timestamp = metadata.started_at.astimezone(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")

        safe_experiment_id = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            metadata.experiment_id,
        ).strip("._")

        if not safe_experiment_id:
            safe_experiment_id = "experiment"

        return f"{timestamp}_{safe_experiment_id}"

    @staticmethod
    def _write_json_line(
        file: TextIO,
        data: dict[str, object],
    ) -> None:
        line = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        file.write(f"{line}\n")
        file.flush()

    @staticmethod
    def _write_json_atomically(
        path: Path,
        data: dict[str, object],
    ) -> None:
        temporary_path = path.with_suffix(
            f"{path.suffix}.tmp"
        )

        with temporary_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")
            file.flush()

        temporary_path.replace(path)