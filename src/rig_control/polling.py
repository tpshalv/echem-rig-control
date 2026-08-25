from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from queue import Empty, Full, Queue
from threading import Event as ThreadEvent, Lock, Thread
from time import monotonic

from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.measurement_source import MeasurementSource
from rig_control.models import Event, EventSeverity, EventSink


@dataclass(frozen=True, slots=True)
class PollingFailure:
    """Details of one device that could not be read."""

    device_id: str
    operation: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class PollingBatch:
    """Measurements and events from acquisition or a display snapshot."""

    started_at: datetime
    finished_at: datetime
    measurements: tuple[MeasurementRecord, ...]
    failures: tuple[PollingFailure, ...]
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class _CompletedRead:
    device_id: str
    started_at: float
    future: Future[tuple[MeasurementRecord, ...]]


class PollingService:
    """Read devices independently and publish cached snapshots."""

    def __init__(
        self,
        device_manager: DeviceManager,
        *,
        interval_seconds: float = 1.0,
        publish_interval_seconds: float | None = None,
        device_intervals_seconds: Mapping[str, float] | None = None,
        max_workers: int | None = None,
        batch_handler: Callable[[PollingBatch], None] | None = None,
        event_sink: EventSink | None = None,
        results_queue_capacity: int = 120,
    ) -> None:
        self._default_device_interval = self._validate_interval(
            interval_seconds,
            "Default device polling interval",
        )
        self._publish_interval = self._validate_interval(
            interval_seconds
            if publish_interval_seconds is None
            else publish_interval_seconds,
            "Polling publish interval",
        )
        if max_workers is not None and (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers <= 0
        ):
            raise ValueError("Polling worker count must be greater than zero")
        if (
            isinstance(results_queue_capacity, bool)
            or not isinstance(results_queue_capacity, int)
            or results_queue_capacity <= 0
        ):
            raise ValueError("Polling results queue capacity must be positive")

        self._device_manager = device_manager
        self._source_ids = tuple(
            device_id
            for device_id in device_manager.device_ids
            if isinstance(device_manager.get(device_id), MeasurementSource)
        )
        configured_intervals = dict(device_intervals_seconds or {})
        unsupported_ids = set(configured_intervals) - set(self._source_ids)
        if unsupported_ids:
            listed = ", ".join(sorted(unsupported_ids))
            raise ValueError(
                "Polling intervals were supplied for devices that are not "
                f"registered measurement sources: {listed}"
            )
        self._device_intervals = {
            device_id: self._validate_interval(
                configured_intervals.get(
                    device_id,
                    self._default_device_interval,
                ),
                f"Polling interval for device {device_id!r}",
            )
            for device_id in self._source_ids
        }
        # One worker per logical source prevents unrelated slow devices from
        # exhausting a smaller shared pool. A supplied value can increase,
        # but cannot weaken, that independence guarantee.
        self._worker_count = max(max_workers or 1, len(self._source_ids), 1)
        self._batch_handler = batch_handler
        self._event_sink = event_sink
        self._results: Queue[PollingBatch] = Queue(
            maxsize=results_queue_capacity
        )
        self._results_queue_capacity = results_queue_capacity
        self._dropped_results_batches = 0
        self._results_overflow_active = False
        self._completed_reads: Queue[_CompletedRead] = Queue()
        self._wake_scheduler = ThreadEvent()
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

    def diagnostic_metrics(self) -> dict[str, object]:
        """Return a low-cost snapshot for runtime health logging."""

        return {
            "running": self.is_running,
            "results_queue_size": self._results.qsize(),
            "results_queue_capacity": self._results_queue_capacity,
            "dropped_results_batches": self._dropped_results_batches,
            "results_overflow_active": self._results_overflow_active,
            "completed_reads_queue_size": self._completed_reads.qsize(),
            "failed_device_count": len(self._failed_device_ids),
            "worker_count": self._worker_count,
            "source_count": len(self._source_ids),
        }

    def start(self) -> None:
        """Start independent device scheduling and snapshot publication."""

        with self._lifecycle_lock:
            if self.is_running:
                raise RuntimeError("Polling service is already running")
            self._stop_requested.clear()
            self._wake_scheduler.clear()
            self._clear_queue(self._completed_reads)
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
        """Request shutdown and wait for in-progress driver calls."""

        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_requested.set()
            self._wake_scheduler.set()

        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("Polling service did not stop before timeout")

        with self._lifecycle_lock:
            if self._thread is thread:
                self._thread = None

    def poll_once(self) -> PollingBatch:
        """Synchronously poll all sources once for diagnostics and tests."""

        if self.is_running:
            raise RuntimeError("poll_once cannot run during background polling")
        with ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="rig-device-read",
        ) as executor:
            batch = self._poll_all_once(executor)
            batch = self._deliver_batch(batch)
            return self._publish_events(batch)

    def _run(self) -> None:
        latest: dict[tuple[str, str], MeasurementRecord] = {}
        active_failures: dict[str, PollingFailure] = {}
        pending_events: list[Event] = []
        now = monotonic()
        next_due = {device_id: now for device_id in self._source_ids}
        in_flight: set[str] = set()
        next_publish = now + self._publish_interval

        with ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="rig-device-read",
        ) as executor:
            while not self._stop_requested.is_set():
                self._drain_completions(
                    latest,
                    active_failures,
                    pending_events,
                    next_due,
                    in_flight,
                )
                now = monotonic()

                for device_id in self._source_ids:
                    if device_id in in_flight or now < next_due[device_id]:
                        continue
                    started_at = now
                    future = executor.submit(self._read_device, device_id)
                    in_flight.add(device_id)
                    future.add_done_callback(
                        lambda completed,
                        selected_id=device_id,
                        selected_start=started_at: self._read_completed(
                            selected_id,
                            selected_start,
                            completed,
                        )
                    )

                now = monotonic()
                if now >= next_publish:
                    batch = PollingBatch(
                        started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC),
                        measurements=tuple(
                            latest[key] for key in sorted(latest)
                        ),
                        failures=tuple(
                            active_failures[key]
                            for key in sorted(active_failures)
                        ),
                        events=tuple(pending_events),
                    )
                    pending_events.clear()
                    self._enqueue_result(batch)
                    while next_publish <= now:
                        next_publish += self._publish_interval

                deadlines = [next_publish]
                deadlines.extend(
                    next_due[device_id]
                    for device_id in self._source_ids
                    if device_id not in in_flight
                )
                wait_seconds = max(0.0, min(deadlines) - monotonic())
                self._wake_scheduler.wait(wait_seconds)
                self._wake_scheduler.clear()

        # The executor waits for any device calls already in progress. Drain
        # their callbacks once more so stopping monitoring cannot discard a
        # successfully completed final acquisition.
        self._drain_completions(
            latest,
            active_failures,
            pending_events,
            next_due,
            in_flight,
        )

    def _enqueue_result(self, batch: PollingBatch) -> None:
        if (
            self._results_overflow_active
            and self._results.qsize() <= self._results_queue_capacity // 2
        ):
            self._results_overflow_active = False
            recovered = Event(
                source="polling_service",
                message=(
                    "Polling UI queue recovered after dropping "
                    f"{self._dropped_results_batches} stale batches."
                ),
            )
            batch = self._with_event(batch, recovered)
            self._publish_single_event(recovered)
        try:
            self._results.put_nowait(batch)
            return
        except Full:
            pass

        try:
            self._results.get_nowait()
        except Empty:
            pass
        self._dropped_results_batches += 1
        if not self._results_overflow_active:
            self._results_overflow_active = True
            warning = Event(
                source="polling_service",
                severity=EventSeverity.WARNING,
                message=(
                    "Polling UI queue filled; dropping the oldest unread "
                    "display batch while experiment recording continues."
                ),
            )
            batch = self._with_event(batch, warning)
            self._publish_single_event(warning)
        self._results.put_nowait(batch)

    def _publish_single_event(self, event: Event) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event, None)
        except Exception:
            # Queue containment must not be defeated by a failing logger.
            pass

    def _read_completed(
        self,
        device_id: str,
        started_at: float,
        future: Future[tuple[MeasurementRecord, ...]],
    ) -> None:
        self._completed_reads.put(_CompletedRead(device_id, started_at, future))
        self._wake_scheduler.set()

    def _drain_completions(
        self,
        latest: dict[tuple[str, str], MeasurementRecord],
        active_failures: dict[str, PollingFailure],
        pending_events: list[Event],
        next_due: dict[str, float],
        in_flight: set[str],
    ) -> None:
        while True:
            try:
                completed = self._completed_reads.get_nowait()
            except Empty:
                return

            device_id = completed.device_id
            in_flight.discard(device_id)
            completed_at = monotonic()
            scheduled_next = (
                completed.started_at + self._device_intervals[device_id]
            )
            next_due[device_id] = (
                scheduled_next
                if scheduled_next > completed_at
                else completed_at + self._device_intervals[device_id]
            )

            try:
                records = completed.future.result()
            except Exception as error:
                failure = PollingFailure(
                    device_id=device_id,
                    operation="read measurements",
                    error_type=type(error).__name__,
                    message=str(error),
                )
                active_failures[device_id] = failure
                if device_id not in self._failed_device_ids:
                    event = self._failure_event(device_id, error)
                    native_batch = self._native_batch(
                        failures=(failure,),
                        events=(event,),
                    )
                    native_batch = self._deliver_batch(native_batch)
                    self._publish_events(native_batch)
                    pending_events.extend(native_batch.events)
                self._failed_device_ids.add(device_id)
                continue

            for record in records:
                latest[(record.device_id, record.channel)] = record
            active_failures.pop(device_id, None)
            events: tuple[Event, ...] = ()
            if device_id in self._failed_device_ids:
                events = (self._recovery_event(device_id),)
                self._failed_device_ids.remove(device_id)
            if records or events:
                native_batch = self._native_batch(
                    measurements=records,
                    events=events,
                )
                native_batch = self._deliver_batch(native_batch)
                self._publish_events(native_batch)
                pending_events.extend(native_batch.events)

    @staticmethod
    def _native_batch(
        *,
        measurements: tuple[MeasurementRecord, ...] = (),
        failures: tuple[PollingFailure, ...] = (),
        events: tuple[Event, ...] = (),
    ) -> PollingBatch:
        now = datetime.now(UTC)
        return PollingBatch(
            started_at=now,
            finished_at=now,
            measurements=measurements,
            failures=failures,
            events=events,
        )

    def _poll_all_once(
        self,
        executor: ThreadPoolExecutor,
    ) -> PollingBatch:
        started_at = datetime.now(UTC)
        futures = {
            executor.submit(self._read_device, device_id): device_id
            for device_id in self._source_ids
        }
        measurements: list[MeasurementRecord] = []
        failures: list[PollingFailure] = []
        events: list[Event] = []

        for future in as_completed(futures):
            device_id = futures[future]
            try:
                measurements.extend(future.result())
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
                    events.append(self._failure_event(device_id, error))
                self._failed_device_ids.add(device_id)
                continue

            if device_id in self._failed_device_ids:
                events.append(self._recovery_event(device_id))
                self._failed_device_ids.remove(device_id)

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
            return self._with_event(
                batch,
                Event(
                    source="polling_service",
                    severity=EventSeverity.ERROR,
                    message=(
                        "Polling result handler failed: "
                        f"{type(error).__name__}: {error}"
                    ),
                ),
            )

        if not self._batch_handler_failed:
            return batch

        self._batch_handler_failed = False
        return self._with_event(
            batch,
            Event(
                source="polling_service",
                message="Polling result handler recovered.",
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
            return self._with_event(
                batch,
                Event(
                    source="polling_service",
                    severity=EventSeverity.ERROR,
                    message=(
                        "Technical event sink failed: "
                        f"{type(error).__name__}: {error}"
                    ),
                ),
            )

        if not self._event_sink_failed:
            return batch

        self._event_sink_failed = False
        return self._with_event(
            batch,
            Event(
                source="polling_service",
                message="Technical event sink recovered.",
            ),
        )

    @staticmethod
    def _failure_event(device_id: str, error: Exception) -> Event:
        return Event(
            source=device_id,
            severity=EventSeverity.ERROR,
            message=(
                "Device measurement polling failed: "
                f"{type(error).__name__}: {error}"
            ),
        )

    @staticmethod
    def _recovery_event(device_id: str) -> Event:
        return Event(
            source=device_id,
            message="Device measurement polling recovered.",
        )

    @staticmethod
    def _with_event(batch: PollingBatch, event: Event) -> PollingBatch:
        return PollingBatch(
            started_at=batch.started_at,
            finished_at=batch.finished_at,
            measurements=batch.measurements,
            failures=batch.failures,
            events=batch.events + (event,),
        )

    @staticmethod
    def _validate_interval(value: float, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be an int or float")
        interval = float(value)
        if not isfinite(interval) or interval <= 0:
            raise ValueError(f"{name} must be finite and greater than zero")
        return interval

    @staticmethod
    def _clear_queue(queue: Queue[object]) -> None:
        while True:
            try:
                queue.get_nowait()
            except Empty:
                return
