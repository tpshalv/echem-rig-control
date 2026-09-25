from collections.abc import Callable
from pathlib import Path
from dataclasses import replace
from threading import Lock, RLock

from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.writer import ExperimentWriter
from rig_control.polling import PollingBatch
from rig_control.models import Event


WriterFactory = Callable[[str | Path], ExperimentWriter]


class ExperimentRecorder:
    """Thread-safe bridge from polling batches to an experiment writer."""

    def __init__(
        self,
        *,
        writer_factory: WriterFactory = DirectoryExperimentWriter,
        context_provider: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._writer_factory = writer_factory
        self._context_provider = context_provider
        self._lock = RLock()
        self._export_lock = Lock()
        self._last_writer: ExperimentWriter | None = None
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

        # Capture outside the recorder lock: device operations can themselves
        # publish audit events to this recorder.
        if self._context_provider is not None:
            metadata = replace(metadata, extra={
                **metadata.extra, **self._context_provider(),
            })
        with self._lock:
            if self.is_recording:
                raise RuntimeError("An experiment is already being recorded")

            writer = self._writer_factory(root_directory)
            writer.open_experiment(metadata)
            self._writer = writer
            self._last_writer = writer
            self._measurement_records_written = 0
            self._event_records_written = 0

    def record_event(self, event: Event) -> None:
        """Record a command/configuration event only while a run is active."""
        with self._lock:
            if self._writer is not None and self._writer.is_open:
                self._writer.write_event(event)
                self._event_records_written += 1

    @property
    def experiment_directory(self) -> Path | None:
        with self._lock:
            return getattr(self._last_writer, "experiment_directory", None)

    def export_excel(self, directory: str | Path | None = None) -> Path:
        """Export outside the acquisition lock so readings keep being saved."""
        from rig_control.data.export import export_excel

        with self._export_lock:
            with self._lock:
                writer = self._last_writer
            if directory is not None:
                return export_excel(directory)
            if not isinstance(writer, DirectoryExperimentWriter):
                raise RuntimeError("No experiment folder is available to export")
            return writer.export_excel()

    def stop(self) -> None:
        """Detach acquisition, then finish exports without holding its lock."""
        with self._export_lock:
            with self._lock:
                if not self.is_recording:
                    raise RuntimeError("No experiment is being recorded")
                writer = self._writer
                self._writer = None
            assert writer is not None
            writer.close_experiment()

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
