from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from queue import Empty
from traceback import format_exc

from rig_control.data.experiment import ExperimentMetadata
from rig_control.devices.manager import DeviceManager
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, Measurement, Quality
from rig_control.polling import PollingService
from rig_control.control.service import RigControlService
from rig_control.ui.manual_control.model import ManualControlViewModel


@dataclass(frozen=True, slots=True)
class OperationActionResult:
    succeeded: bool
    summary: str
    technical_details: str | None = None


@dataclass(frozen=True, slots=True)
class LiveMeasurementRow:
    device_id: str
    channel: str
    value: float
    unit: str
    quality: str
    timestamp: datetime


class OperationViewModel:
    """GUI-independent coordination for monitoring and recording."""

    def __init__(
        self,
        device_manager: DeviceManager,
        polling_service: PollingService,
        experiment_recorder: ExperimentRecorder,
        control_service: RigControlService,
        *,
        profile_id: str,
        history_limit: int = 120,
        event_limit: int = 1_000,
    ) -> None:
        self._validate_history_limit(history_limit)
        self._validate_event_limit(event_limit)
        self._device_manager = device_manager
        self._polling_service = polling_service
        self._experiment_recorder = experiment_recorder
        self.manual_control = ManualControlViewModel(
            device_manager,
            control_service,
            measurement_provider=self.latest_measurement,
        )
        self._profile_id = profile_id
        self._measurements: dict[
            tuple[str, str], LiveMeasurementRow
        ] = {}
        self._history_limit = history_limit
        self._measurement_histories: dict[
            tuple[str, str], deque[LiveMeasurementRow]
        ] = {}
        self._warnings: dict[str, str] = {}
        self._events: deque[Event] = deque(maxlen=event_limit)

    @property
    def is_monitoring(self) -> bool:
        return self._polling_service.is_running

    @property
    def is_recording(self) -> bool:
        return self._experiment_recorder.is_recording

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    @property
    def history_limit(self) -> int:
        return self._history_limit

    def measurement_rows(self) -> tuple[LiveMeasurementRow, ...]:
        return tuple(
            self._measurements[key]
            for key in sorted(self._measurements)
        )

    def measurement_history(
        self,
        device_id: str,
        channel: str,
    ) -> tuple[LiveMeasurementRow, ...]:
        return tuple(
            self._measurement_histories.get((device_id, channel), ())
        )

    def latest_measurement(
        self,
        device_id: str,
        channel: str,
    ) -> Measurement | None:
        row = self._measurements.get((device_id, channel))
        if row is None:
            return None
        return Measurement(
            value=row.value,
            unit=row.unit,
            timestamp=row.timestamp,
            quality=Quality(row.quality),
        )

    def set_history_limit(self, history_limit: int) -> None:
        """Change retained points while preserving the newest readings."""

        self._validate_history_limit(history_limit)
        if history_limit == self._history_limit:
            return

        self._history_limit = history_limit
        self._measurement_histories = {
            key: deque(history, maxlen=history_limit)
            for key, history in self._measurement_histories.items()
        }

    def warnings(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._warnings.items()))

    def diagnostic_metrics(self) -> dict[str, object]:
        """Describe UI-retained state for the runtime health journal."""

        return {
            "latest_measurement_count": len(self._measurements),
            "history_channel_count": len(self._measurement_histories),
            "history_point_count": sum(
                len(history) for history in self._measurement_histories.values()
            ),
            "history_limit_per_channel": self._history_limit,
            "warning_count": len(self._warnings),
            "retained_event_count": len(self._events),
            "event_limit": self._events.maxlen,
        }

    def connect_all(self) -> tuple[OperationActionResult, ...]:
        results: list[OperationActionResult] = []
        for device_id in self._device_manager.device_ids:
            try:
                self._device_manager.connect(device_id)
            except Exception as error:
                results.append(
                    OperationActionResult(
                        False,
                        f"Could not connect {device_id!r}: "
                        f"{type(error).__name__}: {error}",
                        format_exc(),
                    )
                )
                continue
            results.append(
                OperationActionResult(
                    True,
                    f"Connected {device_id!r}.",
                )
            )
        return tuple(results)

    def start_monitoring(self) -> OperationActionResult:
        try:
            self._polling_service.start()
        except Exception as error:
            return self._failure("start monitoring", error)
        return OperationActionResult(True, "Background monitoring started.")

    def stop_monitoring(self) -> OperationActionResult:
        if self.is_recording:
            return OperationActionResult(
                False,
                "Stop experiment recording before stopping monitoring.",
            )
        try:
            self._polling_service.stop()
        except Exception as error:
            return self._failure("stop monitoring", error)
        return OperationActionResult(True, "Background monitoring stopped.")

    def start_recording(
        self,
        *,
        experiment_id: str,
        operator: str,
        output_directory: str | Path,
        sample_interval_seconds: float,
        notes: str | None = None,
    ) -> OperationActionResult:
        if not self.is_monitoring:
            return OperationActionResult(
                False,
                "Start monitoring before starting experiment recording.",
            )
        if not str(output_directory).strip():
            return OperationActionResult(
                False,
                "Choose an experiment output folder before recording.",
            )
        try:
            metadata = ExperimentMetadata(
                experiment_id=experiment_id,
                operator=operator,
                notes=notes or None,
                extra={"rig_profile_id": self._profile_id},
            )
            self._experiment_recorder.start(
                metadata=metadata,
                root_directory=output_directory,
                sample_interval_seconds=sample_interval_seconds,
            )
        except Exception as error:
            return self._failure("start experiment recording", error)
        return OperationActionResult(
            True,
            f"Recording experiment {experiment_id!r}.",
        )

    def stop_recording(self) -> OperationActionResult:
        try:
            self._experiment_recorder.stop()
        except Exception as error:
            return self._failure("stop experiment recording", error)
        return OperationActionResult(True, "Experiment recording completed.")

    def collect_polling_results(self) -> int:
        """Drain queued batches; intended for a Tk ``after`` callback."""

        count = 0
        while True:
            try:
                batch = self._polling_service.results.get_nowait()
            except Empty:
                return count

            count += 1
            successful_ids: set[str] = set()
            for record in batch.measurements:
                measurement = record.measurement
                key = (record.device_id, record.channel)
                row = LiveMeasurementRow(
                    device_id=record.device_id,
                    channel=record.channel,
                    value=measurement.value,
                    unit=measurement.unit,
                    quality=measurement.quality.value,
                    timestamp=measurement.timestamp,
                )
                self._measurements[key] = row
                history = self._measurement_histories.setdefault(
                    key,
                    deque(maxlen=self._history_limit),
                )
                history.append(row)
                successful_ids.add(record.device_id)

            for device_id in successful_ids:
                self._warnings.pop(device_id, None)
            for failure in batch.failures:
                self._warnings[failure.device_id] = (
                    f"{failure.error_type}: {failure.message}"
                )
                for key, row in tuple(self._measurements.items()):
                    if row.device_id == failure.device_id:
                        self._measurements[key] = replace(
                            row,
                            quality=Quality.STALE.value,
                        )
            self._events.extend(batch.events)

    def shutdown(self) -> tuple[str, ...]:
        """Stop recording/polling and disconnect all devices."""

        failures: list[str] = []
        if self.is_recording:
            try:
                self._experiment_recorder.stop()
            except Exception as error:
                failures.append(
                    f"Could not stop recording: {type(error).__name__}: {error}"
                )
        if self.is_monitoring:
            try:
                self._polling_service.stop()
            except Exception as error:
                failures.append(
                    f"Could not stop monitoring: {type(error).__name__}: {error}"
                )
        return tuple(failures)

    @staticmethod
    def _failure(operation: str, error: Exception) -> OperationActionResult:
        return OperationActionResult(
            False,
            f"Could not {operation}: {type(error).__name__}: {error}",
            format_exc(),
        )

    @staticmethod
    def _validate_history_limit(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("Trend history limit must be an integer")
        if value < 2:
            raise ValueError("Trend history limit must be at least 2")

    @staticmethod
    def _validate_event_limit(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError("Event history limit must be an integer")
        if value <= 0:
            raise ValueError("Event history limit must be positive")
