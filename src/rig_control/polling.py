from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Queue
from threading import Event as ThreadEvent, Lock, Thread
from time import monotonic

from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.measurement_source import MeasurementSource
from rig_control.models import Event, EventSeverity, EventSink


@dataclass(frozen=True, slots=True)
class PollingFailure:
    """Details of one device that could not be read in a polling cycle."""

    device_id: str
    operation: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class PollingBatch:
    """All results produced by one polling cycle."""

    started_at: datetime
    finished_at: datetime
    measurements: tuple[MeasurementRecord, ...]
    failures: tuple[PollingFailure, ...]
    events: tuple[Event, ...]


class PollingService:
    """Read measurement-capable devices without blocking the UI thread."""

    def __init__(
        self,
        device_manager: DeviceManager,
        *,
        interval_seconds: float = 1.0,
        max_workers: int = 4,
        batch_handler: Callable[[PollingBatch], None] | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Polling interval must be greater than zero")
        if max_workers <= 0:
            raise ValueError("Polling worker count must be greater than zero")

        self._device_manager = device_manager
        self._interval_seconds = float(interval_seconds)
        self._max_workers = max_workers
        self._batch_handler = batch_handler
        self._event_sink = event_sink
        self._results: Queue[PollingBatch] = Queue()
        self._stop_requested = ThreadEvent()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None
        self._failed_device_ids: set[str] = set()
        self._batch_handler_failed = False
        self._event_sink_failed = False

    @property
    def results(self) -> Queue[PollingBatch]:
        """Queue consumed by Tkinter via a short ``after`` callback."""

        return self._results

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start polling; calling start while running is an error."""

        with self._lifecycle_lock:
            if self.is_running:
                raise RuntimeError("Polling service is already running")
            self._stop_requested.clear()
            self._failed_device_ids.clear()
            self._batch_handler_failed = False
            self._event_sink_failed = False
            self._thread = Thread(
                target=self._run,
                name="rig-device-polling",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Request shutdown and wait for in-progress device calls."""

        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_requested.set()

        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("Polling service did not stop before timeout")

        with self._lifecycle_lock:
            if self._thread is thread:
                self._thread = None

    def poll_once(self) -> PollingBatch:
        """Poll all eligible devices once, primarily for tests and tools."""

        with ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="rig-device-read",
        ) as executor:
            batch = self._poll_with_executor(executor)
            batch = self._deliver_batch(batch)
            return self._publish_events(batch)

    def _run(self) -> None:
        with ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="rig-device-read",
        ) as executor:
            while not self._stop_requested.is_set():
                cycle_started = monotonic()
                batch = self._poll_with_executor(executor)
                batch = self._deliver_batch(batch)
                batch = self._publish_events(batch)
                self._results.put(batch)
                remaining = self._interval_seconds - (
                    monotonic() - cycle_started
                )
                if remaining > 0:
                    self._stop_requested.wait(remaining)

    def _poll_with_executor(
        self,
        executor: ThreadPoolExecutor,
    ) -> PollingBatch:
        started_at = datetime.now(UTC)
        futures: dict[Future[tuple[MeasurementRecord, ...]], str] = {}

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)
            if isinstance(device, MeasurementSource):
                futures[executor.submit(self._read_device, device_id)] = (
                    device_id
                )

        measurements: list[MeasurementRecord] = []
        failures: list[PollingFailure] = []
        events: list[Event] = []
        successful_device_ids: set[str] = set()

        for future in as_completed(futures):
            device_id = futures[future]
            try:
                measurements.extend(future.result())
                successful_device_ids.add(device_id)
            except Exception as error:
                failures.append(
                    PollingFailure(
                        device_id=device_id,
                        operation="read measurements",
                        error_type=type(error).__name__,
                        message=str(error),
                    )
                )
                if device_id not in self._failed_device_ids:
                    events.append(
                        Event(
                            source=device_id,
                            severity=EventSeverity.ERROR,
                            message=(
                                "Device measurement polling failed: "
                                f"{type(error).__name__}: {error}"
                            ),
                        )
                    )

        for device_id in successful_device_ids & self._failed_device_ids:
            events.append(
                Event(
                    source=device_id,
                    severity=EventSeverity.INFO,
                    message="Device measurement polling recovered.",
                )
            )

        self._failed_device_ids.difference_update(successful_device_ids)
        self._failed_device_ids.update(
            failure.device_id for failure in failures
        )

        return PollingBatch(
            started_at=started_at,
            finished_at=datetime.now(UTC),
            measurements=tuple(measurements),
            failures=tuple(failures),
            events=tuple(events),
        )

    def _deliver_batch(self, batch: PollingBatch) -> PollingBatch:
        if self._batch_handler is None:
            return batch

        try:
            self._batch_handler(batch)
        except Exception as error:
            if self._batch_handler_failed:
                return batch
            self._batch_handler_failed = True
            return PollingBatch(
                started_at=batch.started_at,
                finished_at=batch.finished_at,
                measurements=batch.measurements,
                failures=batch.failures,
                events=batch.events
                + (
                    Event(
                        source="polling_service",
                        severity=EventSeverity.ERROR,
                        message=(
                            "Polling result handler failed: "
                            f"{type(error).__name__}: {error}"
                        ),
                    ),
                ),
            )

        if not self._batch_handler_failed:
            return batch

        self._batch_handler_failed = False
        return PollingBatch(
            started_at=batch.started_at,
            finished_at=batch.finished_at,
            measurements=batch.measurements,
            failures=batch.failures,
            events=batch.events
            + (
                Event(
                    source="polling_service",
                    message="Polling result handler recovered.",
                ),
            ),
        )

    def _read_device(
        self,
        device_id: str,
    ) -> tuple[MeasurementRecord, ...]:
        with self._device_manager.operation(device_id) as device:
            if not isinstance(device, MeasurementSource):
                return ()
            readings = device.read_measurements()

        return tuple(
            MeasurementRecord(
                device_id=device_id,
                channel=reading.channel,
                measurement=reading.measurement,
            )
            for reading in readings
        )

    def _publish_events(self, batch: PollingBatch) -> PollingBatch:
        if self._event_sink is None:
            return batch

        try:
            for event in batch.events:
                self._event_sink(event, None)
        except Exception as error:
            if self._event_sink_failed:
                return batch
            self._event_sink_failed = True
            return PollingBatch(
                started_at=batch.started_at,
                finished_at=batch.finished_at,
                measurements=batch.measurements,
                failures=batch.failures,
                events=batch.events
                + (
                    Event(
                        source="polling_service",
                        severity=EventSeverity.ERROR,
                        message=(
                            "Technical event sink failed: "
                            f"{type(error).__name__}: {error}"
                        ),
                    ),
                ),
            )

        if not self._event_sink_failed:
            return batch

        self._event_sink_failed = False
        return PollingBatch(
            started_at=batch.started_at,
            finished_at=batch.finished_at,
            measurements=batch.measurements,
            failures=batch.failures,
            events=batch.events
            + (
                Event(
                    source="polling_service",
                    message="Technical event sink recovered.",
                ),
            ),
        )
