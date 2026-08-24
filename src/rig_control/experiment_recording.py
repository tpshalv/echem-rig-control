from collections.abc import Callable
from math import isfinite
from pathlib import Path
from threading import RLock
from time import monotonic

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
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._writer_factory = writer_factory
        self._clock = clock
        self._lock = RLock()
        self._writer: ExperimentWriter | None = None
        self._sample_interval_seconds: float | None = None
        self._next_sample_at: float | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._writer is not None and self._writer.is_open

    @property
    def sample_interval_seconds(self) -> float | None:
        with self._lock:
            return self._sample_interval_seconds

    def diagnostic_metrics(self) -> dict[str, object]:
        """Return recorder state without retaining measurement data."""

        with self._lock:
            writer = self._writer
            directory = getattr(writer, "experiment_directory", None)
            return {
                "recording": writer is not None and writer.is_open,
                "sample_interval_seconds": self._sample_interval_seconds,
                "experiment_directory": (
                    str(directory) if directory is not None else None
                ),
            }

    def start(
        self,
        *,
        metadata: ExperimentMetadata,
        root_directory: str | Path,
        sample_interval_seconds: float,
    ) -> None:
        """Create and open a new experiment recording."""

        interval = self._validate_sample_interval(sample_interval_seconds)

        with self._lock:
            if self.is_recording:
                raise RuntimeError("An experiment is already being recorded")

            writer = self._writer_factory(root_directory)
            writer.open_experiment(metadata)
            self._writer = writer
            self._sample_interval_seconds = interval
            self._next_sample_at = self._clock()

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
                self._sample_interval_seconds = None
                self._next_sample_at = None

    def record_batch(self, batch: PollingBatch) -> None:
        """Record a due polling batch, or ignore it when inactive."""

        if not isinstance(batch, PollingBatch):
            raise TypeError("Experiment recorder requires a PollingBatch")

        with self._lock:
            if not self.is_recording:
                return

            now = self._clock()
            assert self._next_sample_at is not None
            assert self._writer is not None
            for event in batch.events:
                self._writer.write_event(event)

            if now < self._next_sample_at:
                return

            for record in batch.measurements:
                self._writer.write_measurement(record)

            assert self._sample_interval_seconds is not None
            self._next_sample_at = now + self._sample_interval_seconds

    @staticmethod
    def _validate_sample_interval(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Sample interval must be an int or float")

        interval = float(value)
        if not isfinite(interval) or interval <= 0:
            raise ValueError(
                "Sample interval must be finite and greater than zero"
            )
        return interval
