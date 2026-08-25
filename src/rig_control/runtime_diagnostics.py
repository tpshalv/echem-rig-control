import gc
import json
import os
import sys
import tracemalloc
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Formatter, INFO, Logger, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event as ThreadEvent, Lock, Thread, active_count
from time import monotonic
from typing import Any

from rig_control.models import Event, EventSeverity, EventSink


@dataclass(frozen=True, slots=True)
class ProcessMemory:
    resident_bytes: int | None
    private_bytes: int | None
    virtual_bytes: int | None
    peak_resident_bytes: int | None
    handle_count: int | None
    gdi_object_count: int | None = None
    user_object_count: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeHealthPoint:
    timestamp: datetime
    resident_bytes: int | None
    private_bytes: int | None
    traced_python_bytes: int
    thread_count: int
    results_queue_size: int | None
    results_queue_capacity: int | None
    dropped_results_batches: int | None
    retained_event_count: int | None
    gdi_object_count: int | None
    user_object_count: int | None
    ui_tick_count: int | None
    ui_tick_failure_count: int | None

    @property
    def value(self) -> float:
        selected = self.private_bytes
        if selected is None:
            selected = self.resident_bytes or 0
        return selected / 1_000_000

    @property
    def unit(self) -> str:
        return "MB private memory"

    @property
    def quality(self) -> str:
        return "good" if self.private_bytes is not None else "stale"


MetricProvider = Callable[[], Mapping[str, object]]
ProcessSampler = Callable[[], ProcessMemory]


class RuntimeDiagnostics:
    """Periodically persist bounded process and Python allocation diagnostics."""

    def __init__(
        self,
        path: str | Path,
        *,
        metric_providers: Mapping[str, MetricProvider] | None = None,
        event_sink: EventSink | None = None,
        sample_interval_seconds: float = 30.0,
        allocation_interval_seconds: float = 3_600.0,
        warning_allocation_cooldown_seconds: float = 600.0,
        warning_allocation_limit_per_hour: int = 3,
        warning_private_bytes: int = 1_000_000_000,
        warning_growth_bytes: int = 250_000_000,
        warning_window_seconds: float = 600.0,
        max_bytes: int = 5_000_000,
        backup_count: int = 5,
        process_sampler: ProcessSampler | None = None,
        display_history_limit: int = 2_880,
        overview_history_limit: int = 240,
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError("Diagnostic sample interval must be positive")
        if allocation_interval_seconds <= 0:
            raise ValueError("Allocation sample interval must be positive")
        if warning_allocation_cooldown_seconds <= 0:
            raise ValueError("Warning allocation cooldown must be positive")
        if warning_allocation_limit_per_hour <= 0:
            raise ValueError("Warning allocation limit must be positive")
        if max_bytes <= 0 or backup_count < 0:
            raise ValueError("Invalid diagnostic log rotation settings")
        if (
            isinstance(display_history_limit, bool)
            or not isinstance(display_history_limit, int)
            or display_history_limit <= 0
        ):
            raise ValueError("Diagnostic display history limit must be positive")
        if (
            isinstance(overview_history_limit, bool)
            or not isinstance(overview_history_limit, int)
            or overview_history_limit < 4
        ):
            raise ValueError("Diagnostic overview history limit must be at least 4")

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._providers = dict(metric_providers or {})
        self._providers_lock = Lock()
        self._event_sink = event_sink
        self._sample_interval = float(sample_interval_seconds)
        self._allocation_interval = float(allocation_interval_seconds)
        self._warning_allocation_cooldown = float(
            warning_allocation_cooldown_seconds
        )
        self._warning_allocation_limit = warning_allocation_limit_per_hour
        self._warning_private_bytes = warning_private_bytes
        self._warning_growth_bytes = warning_growth_bytes
        self._warning_window = float(warning_window_seconds)
        self._process_sampler = process_sampler or sample_process_memory
        self._stop_requested = ThreadEvent()
        self._thread: Thread | None = None
        self._memory_history: deque[tuple[float, int]] = deque()
        self._display_history: deque[RuntimeHealthPoint] = deque(
            maxlen=display_history_limit
        )
        self._overview_history: list[RuntimeHealthPoint] = []
        self._overview_history_limit = overview_history_limit
        self._display_history_lock = Lock()
        self._last_warning_at: float | None = None
        self._warning_snapshot_times: deque[float] = deque()
        self._previous_snapshot: tracemalloc.Snapshot | None = None
        self._allocation_snapshots_disabled = False
        self._owns_tracemalloc = False
        self._logger = self._create_logger(max_bytes, backup_count)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            raise RuntimeError("Runtime diagnostics are already running")
        if not tracemalloc.is_tracing():
            # One traceback frame keeps observer overhead modest during long runs.
            tracemalloc.start(1)
            self._owns_tracemalloc = True
        self._stop_requested.clear()
        self._thread = Thread(
            target=self._run,
            name="rig-runtime-diagnostics",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_requested.set()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("Runtime diagnostics did not stop before timeout")
        self._thread = None
        try:
            self._write_sample(include_allocations=True, lifecycle="stopped")
        except Exception as error:
            self._write_fallback_error(error)
        self._previous_snapshot = None
        if self._owns_tracemalloc and tracemalloc.is_tracing():
            tracemalloc.stop()
            self._owns_tracemalloc = False
        for handler in tuple(self._logger.handlers):
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)

    def sample_now(self, *, include_allocations: bool = False) -> dict[str, object]:
        return self._write_sample(include_allocations=include_allocations)

    def health_history(self) -> tuple[RuntimeHealthPoint, ...]:
        """Return bounded samples intended for live UI display."""

        with self._display_history_lock:
            return tuple(self._display_history)

    def latest_health_point(self) -> RuntimeHealthPoint | None:
        """Return the newest display sample without copying the full history."""

        with self._display_history_lock:
            return self._display_history[-1] if self._display_history else None

    def overview_health_history(self) -> tuple[RuntimeHealthPoint, ...]:
        """Return a fixed-size, whole-run memory overview.

        Older points are progressively condensed while retaining local memory
        highs and lows, so this does not grow during an unattended run.
        """

        with self._display_history_lock:
            return tuple(
                _condense_health_points(
                    self._overview_history,
                    self._overview_history_limit,
                )
            )

    def register_metric_provider(self, name: str, provider: MetricProvider) -> None:
        """Add or replace a named source of lightweight application counters."""

        with self._providers_lock:
            self._providers[name] = provider

    def unregister_metric_provider(self, name: str) -> None:
        with self._providers_lock:
            self._providers.pop(name, None)

    def _run(self) -> None:
        next_allocations = monotonic() + self._allocation_interval
        try:
            self._write_sample(include_allocations=True, lifecycle="started")
        except Exception as error:
            self._write_fallback_error(error)
        while not self._stop_requested.wait(self._sample_interval):
            now = monotonic()
            include_allocations = now >= next_allocations
            try:
                self._write_sample(include_allocations=include_allocations)
            except Exception as error:
                # The monitor must survive individual sampling/provider failures.
                self._write_fallback_error(error)
            if include_allocations:
                next_allocations = now + self._allocation_interval

    def _write_sample(
        self,
        *,
        include_allocations: bool,
        lifecycle: str | None = None,
    ) -> dict[str, object]:
        now = monotonic()
        memory = self._process_sampler()
        traced_current, traced_peak = (
            tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (0, 0)
        )
        timestamp = datetime.now(UTC)
        application_metrics = self._collect_application_metrics()
        record: dict[str, object] = {
            "schema_version": 1,
            "timestamp": timestamp.isoformat(),
            "pid": os.getpid(),
            "lifecycle": lifecycle,
            "process": {
                "resident_bytes": memory.resident_bytes,
                "private_bytes": memory.private_bytes,
                "virtual_bytes": memory.virtual_bytes,
                "peak_resident_bytes": memory.peak_resident_bytes,
                "handle_count": memory.handle_count,
                "gdi_object_count": memory.gdi_object_count,
                "user_object_count": memory.user_object_count,
                "thread_count": active_count(),
            },
            "python": {
                "traced_current_bytes": traced_current,
                "traced_peak_bytes": traced_peak,
                "gc_counts": list(gc.get_count()),
                "gc_objects": len(gc.get_objects()),
            },
            "application": application_metrics,
        }
        self._append_health_point(
            timestamp,
            memory,
            traced_current,
            application_metrics,
        )
        warning = self._memory_warning(now, memory.private_bytes)
        if warning is not None:
            record["warning"] = warning
            self._publish_warning(warning)
        warning_snapshot = (
            warning is not None
            and not include_allocations
            and self._warning_snapshot_allowed(now, memory.private_bytes)
        )
        if (include_allocations or warning_snapshot) and tracemalloc.is_tracing():
            if self._allocation_snapshots_disabled:
                record["allocation_snapshot_skipped"] = (
                    "disabled after an earlier MemoryError"
                )
            elif (
                memory.private_bytes is not None
                and memory.private_bytes >= self._warning_private_bytes
            ):
                record["allocation_snapshot_skipped"] = (
                    "private memory is already above the safety threshold"
                )
            else:
                if warning_snapshot:
                    self._warning_snapshot_times.append(now)
                record["allocations"] = self._safe_allocation_summary()
        self._logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        return record

    def _append_health_point(
        self,
        timestamp: datetime,
        memory: ProcessMemory,
        traced_current: int,
        application_metrics: Mapping[str, object],
    ) -> None:
        polling = _mapping(application_metrics.get("polling"))
        operation_ui = _mapping(application_metrics.get("operation_ui"))
        point = RuntimeHealthPoint(
            timestamp=timestamp,
            resident_bytes=memory.resident_bytes,
            private_bytes=memory.private_bytes,
            traced_python_bytes=traced_current,
            thread_count=active_count(),
            results_queue_size=_optional_int(polling.get("results_queue_size")),
            results_queue_capacity=_optional_int(
                polling.get("results_queue_capacity")
            ),
            dropped_results_batches=_optional_int(
                polling.get("dropped_results_batches")
            ),
            retained_event_count=_optional_int(
                operation_ui.get("retained_event_count")
            ),
            gdi_object_count=memory.gdi_object_count,
            user_object_count=memory.user_object_count,
            ui_tick_count=_optional_int(operation_ui.get("ui_tick_count")),
            ui_tick_failure_count=_optional_int(
                operation_ui.get("ui_tick_failure_count")
            ),
        )
        with self._display_history_lock:
            self._display_history.append(point)
            self._overview_history.append(point)
            if len(self._overview_history) > self._overview_history_limit * 2:
                self._overview_history = _condense_health_points(
                    self._overview_history,
                    self._overview_history_limit,
                )

    def _collect_application_metrics(self) -> dict[str, object]:
        metrics: dict[str, object] = {}
        with self._providers_lock:
            providers = tuple(self._providers.items())
        for name, provider in providers:
            try:
                metrics[name] = dict(provider())
            except Exception as error:
                metrics[name] = {
                    "diagnostic_error": f"{type(error).__name__}: {error}"
                }
        return metrics

    def _allocation_summary(self) -> dict[str, object]:
        snapshot = tracemalloc.take_snapshot()
        current = snapshot.statistics("lineno")[:20]
        growth = (
            snapshot.compare_to(self._previous_snapshot, "lineno")[:20]
            if self._previous_snapshot is not None
            else ()
        )
        self._previous_snapshot = snapshot
        return {
            "largest": [_statistic_dict(item) for item in current],
            "growth_since_previous": [_statistic_dict(item) for item in growth],
        }

    def _safe_allocation_summary(self) -> dict[str, object]:
        try:
            return self._allocation_summary()
        except MemoryError:
            self._previous_snapshot = None
            self._allocation_snapshots_disabled = True
            return {
                "diagnostic_error": (
                    "MemoryError while taking allocation snapshot; "
                    "further snapshots are disabled for this run"
                )
            }

    def _warning_snapshot_allowed(
        self,
        now: float,
        private_bytes: int | None,
    ) -> bool:
        if self._allocation_snapshots_disabled:
            return False
        if private_bytes is None or private_bytes >= self._warning_private_bytes:
            return False
        cutoff = now - 3_600
        while self._warning_snapshot_times and self._warning_snapshot_times[0] < cutoff:
            self._warning_snapshot_times.popleft()
        if len(self._warning_snapshot_times) >= self._warning_allocation_limit:
            return False
        return not self._warning_snapshot_times or (
            now - self._warning_snapshot_times[-1]
            >= self._warning_allocation_cooldown
        )

    def _memory_warning(self, now: float, private_bytes: int | None) -> str | None:
        if private_bytes is None:
            return None
        self._memory_history.append((now, private_bytes))
        cutoff = now - self._warning_window
        while len(self._memory_history) > 1 and self._memory_history[1][0] <= cutoff:
            self._memory_history.popleft()
        growth = private_bytes - self._memory_history[0][1]
        reasons: list[str] = []
        if private_bytes >= self._warning_private_bytes:
            reasons.append(f"private memory is {private_bytes / 1_000_000_000:.2f} GB")
        if growth >= self._warning_growth_bytes:
            reasons.append(
                f"private memory grew {growth / 1_000_000:.0f} MB within "
                f"{self._warning_window / 60:g} minutes"
            )
        if not reasons:
            return None
        if self._last_warning_at is not None and now - self._last_warning_at < 300:
            return None
        self._last_warning_at = now
        return "; ".join(reasons)

    def _publish_warning(self, warning: str) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(
                Event(
                    source="runtime_diagnostics",
                    severity=EventSeverity.WARNING,
                    message=f"Runtime memory warning: {warning}",
                ),
                None,
            )
        except Exception:
            pass

    def _write_fallback_error(self, error: Exception) -> None:
        try:
            self._logger.info(
                json.dumps(
                    {
                        "schema_version": 1,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "pid": os.getpid(),
                        "diagnostic_error": f"{type(error).__name__}: {error}",
                    },
                    separators=(",", ":"),
                )
            )
        except Exception:
            pass

    def _create_logger(self, max_bytes: int, backup_count: int) -> Logger:
        logger = getLogger(f"rig_control.runtime_diagnostics.{id(self)}")
        logger.setLevel(INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            self._path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(Formatter("%(message)s"))
        logger.addHandler(handler)
        return logger


def _condense_health_points(
    points: list[RuntimeHealthPoint],
    limit: int,
) -> list[RuntimeHealthPoint]:
    """Reduce a time series while retaining endpoints and local extrema."""

    if len(points) <= limit:
        return list(points)
    interior = points[1:-1]
    bucket_count = max(1, (limit - 2) // 4)
    bucket_size = max(1, (len(interior) + bucket_count - 1) // bucket_count)
    selected = [points[0]]
    for start in range(0, len(interior), bucket_size):
        bucket = interior[start : start + bucket_size]
        candidates = {
            min(bucket, key=_display_memory_bytes),
            max(bucket, key=_display_memory_bytes),
            min(bucket, key=lambda point: point.traced_python_bytes),
            max(bucket, key=lambda point: point.traced_python_bytes),
        }
        selected.extend(sorted(candidates, key=lambda point: point.timestamp))
    selected.append(points[-1])
    if len(selected) > limit:
        # This only trims surplus bucket boundaries; first/latest remain exact.
        step = (len(selected) - 1) / (limit - 1)
        selected = [selected[round(index * step)] for index in range(limit)]
    return selected


def _display_memory_bytes(point: RuntimeHealthPoint) -> int:
    if point.private_bytes is not None:
        return point.private_bytes
    return point.resident_bytes or 0


def sample_process_memory() -> ProcessMemory:
    if sys.platform == "win32":
        return _sample_windows_process_memory()
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        scale = 1 if sys.platform == "darwin" else 1024
        return ProcessMemory(
            resident_bytes=None,
            private_bytes=None,
            virtual_bytes=None,
            peak_resident_bytes=int(usage.ru_maxrss * scale),
            handle_count=None,
        )
    except (ImportError, OSError):
        return ProcessMemory(None, None, None, None, None)


def _sample_windows_process_memory() -> ProcessMemory:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user32.GetGuiResources.restype = wintypes.DWORD
    process = kernel32.GetCurrentProcess()
    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    ):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    handle_count = wintypes.DWORD()
    if not kernel32.GetProcessHandleCount(process, ctypes.byref(handle_count)):
        handles: int | None = None
    else:
        handles = int(handle_count.value)
    return ProcessMemory(
        resident_bytes=int(counters.WorkingSetSize),
        private_bytes=int(counters.PrivateUsage),
        virtual_bytes=int(counters.PagefileUsage),
        peak_resident_bytes=int(counters.PeakWorkingSetSize),
        handle_count=handles,
        gdi_object_count=int(user32.GetGuiResources(process, 0)),
        user_object_count=int(user32.GetGuiResources(process, 1)),
    )


def _statistic_dict(statistic: Any) -> dict[str, object]:
    frame = statistic.traceback[0]
    return {
        "file": frame.filename,
        "line": frame.lineno,
        "size_bytes": statistic.size,
        "size_diff_bytes": getattr(statistic, "size_diff", None),
        "count": statistic.count,
        "count_diff": getattr(statistic, "count_diff", None),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
