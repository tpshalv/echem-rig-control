import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import TextIO

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.data.serialization import (
    DATA_SCHEMA_VERSION,
    event_to_dict,
    experiment_metadata_to_dict,
    measurement_record_to_dict,
)
from rig_control.data.export import export_experiment_files, export_wide_csv
from rig_control.data.writer import ExperimentWriter
from rig_control.models import Event


class DirectoryExperimentWriter(ExperimentWriter):
    """Write one experiment into a self-contained directory."""

    def __init__(
        self,
        root_directory: str | Path,
        *,
        export_bin_seconds: float = 1.0,
        live_export_interval_seconds: float = 60.0,
    ) -> None:
        self._root_directory = Path(root_directory)
        self._export_bin_seconds = self._validate_export_bin_seconds(
            export_bin_seconds
        )
        self._live_export_interval_seconds = (
            self._validate_live_export_interval_seconds(
                live_export_interval_seconds
            )
        )
        self._last_live_export_at: float | None = None
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

        self.write_measurements((record,))

    def write_measurements(
        self,
        records: Iterable[MeasurementRecord],
    ) -> None:
        self._require_open()
        prepared = tuple(records)
        if any(not isinstance(record, MeasurementRecord) for record in prepared):
            raise TypeError("Experiment writer requires MeasurementRecord")
        if not prepared:
            return
        assert self._measurement_file is not None
        self._write_json_lines(
            self._measurement_file,
            (measurement_record_to_dict(record) for record in prepared),
        )
        self._refresh_live_export_if_due()

    def write_event(self, event: Event) -> None:
        self._require_open()

        if not isinstance(event, Event):
            raise TypeError(
                "Experiment writer requires Event"
            )

        assert self._event_file is not None

        self.write_events((event,))

    def write_events(self, events: Iterable[Event]) -> None:
        self._require_open()
        prepared = tuple(events)
        if any(not isinstance(event, Event) for event in prepared):
            raise TypeError("Experiment writer requires Event")
        if not prepared:
            return
        assert self._event_file is not None
        self._write_json_lines(
            self._event_file,
            (event_to_dict(event) for event in prepared),
        )
        self._refresh_live_export_if_due()

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
        export_experiment_files(
            experiment_directory,
            bin_seconds=self._export_bin_seconds,
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

    def _refresh_live_export_if_due(self) -> None:
        if self._live_export_interval_seconds <= 0:
            return
        if self._experiment_directory is None:
            return
        now = monotonic()
        if (
            self._last_live_export_at is not None
            and now - self._last_live_export_at
            < self._live_export_interval_seconds
        ):
            return
        self._flush_files()
        export_wide_csv(
            self._experiment_directory,
            bin_seconds=self._export_bin_seconds,
        )
        self._last_live_export_at = now

    def _flush_files(self) -> None:
        for file in (self._measurement_file, self._event_file):
            if file is None:
                continue
            file.flush()
            os.fsync(file.fileno())

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
    def _write_json_lines(
        file: TextIO,
        data_items: Iterable[dict[str, object]],
    ) -> None:
        for data in data_items:
            line = json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            file.write(f"{line}\n")
        file.flush()
        os.fsync(file.fileno())

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
            os.fsync(file.fileno())

        for attempt in range(10):
            try:
                temporary_path.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise

                # Windows antivirus or indexing may briefly hold the
                # existing JSON file open. Retrying preserves the
                # atomic replacement rather than deleting it first.
                sleep(0.05)

    @staticmethod
    def _validate_export_bin_seconds(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Export bin size must be a number")
        number = float(value)
        if number <= 0:
            raise ValueError("Export bin size must be greater than zero")
        return number

    @staticmethod
    def _validate_live_export_interval_seconds(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Live export interval must be a number")
        number = float(value)
        if number < 0:
            raise ValueError("Live export interval cannot be negative")
        return number
