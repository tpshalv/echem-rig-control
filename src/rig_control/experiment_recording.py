from collections.abc import Callable
from pathlib import Path
from threading import RLock

from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.writer import ExperimentWriter
from rig_control.polling import PollingBatch


WriterFactory = Callable[[str | Path], ExperimentWriter]


class ExperimentRecorder:
    """Thread-safe bridge from polling batches to an experiment writer."""

    def __init__(
        self,
        *,
        writer_factory: WriterFactory = DirectoryExperimentWriter,
    ) -> None:
        self._writer_factory = writer_factory
        self._lock = RLock()
        self._writer: ExperimentWriter | None = None
        self._measurement_records_written = 0
        self._event_records_written = 0

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._writer is not None and self._writer.is_open

    def diagnostic_metrics(self) -> dict[str, object]:
        """Return recorder state without retaining measurement data."""

        with self._lock:
            writer = self._writer
            directory = getattr(writer, "experiment_directory", None)
            return {
                "recording": writer is not None and writer.is_open,
                "measurement_records_written": self._measurement_records_written,
                "event_records_written": self._event_records_written,
                "experiment_directory": (
                    str(directory) if directory is not None else None
                ),
            }

    def start(
        self,
        *,
        metadata: ExperimentMetadata,
        root_directory: str | Path,
    ) -> None:
        """Create and open a new experiment recording."""

        with self._lock:
            if self.is_recording:
                raise RuntimeError("An experiment is already being recorded")

            writer = self._writer_factory(root_directory)
            writer.open_experiment(metadata)
            self._writer = writer
            self._measurement_records_written = 0
            self._event_records_written = 0

    def stop(self) -> None:
        """Close the active recording and mark it complete."""

        with self._lock:
            if not self.is_recording:
                raise RuntimeError("No experiment is being recorded")

            assert self._writer is not None
            try:
                self._writer.close_experiment()
            finally:
                self._writer = None

    def record_batch(self, batch: PollingBatch) -> None:
        """Record each newly acquired measurement exactly once."""

        if not isinstance(batch, PollingBatch):
            raise TypeError("Experiment recorder requires a PollingBatch")

        with self._lock:
            if not self.is_recording:
                return

            assert self._writer is not None
            self._writer.write_events(batch.events)
            self._writer.write_measurements(batch.measurements)
            self._event_records_written += len(batch.events)
            self._measurement_records_written += len(batch.measurements)
