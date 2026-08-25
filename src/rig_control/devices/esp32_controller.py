from threading import Event as ThreadEvent, RLock, Thread
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from rig_control.devices.base import Device
from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.models import DeviceStatus, Event, EventSeverity, EventSink


class Esp32Controller(Device):
    """Rig device backed by the ESP32 JSON controller protocol."""

    def __init__(
        self,
        device_id: str,
        bus: Esp32Bus,
        *,
        heartbeat_interval_seconds: float = 2.0,
        event_sink: EventSink | None = None,
    ) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("Heartbeat interval must be greater than zero")
        self._device_id = device_id
        self._bus = bus
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._event_sink = event_sink
        self._status = DeviceStatus.DISCONNECTED
        self._controller_status: dict[str, Any] = {}
        self._lock = RLock()
        self._stop = ThreadEvent()
        self._heartbeat_thread: Thread | None = None
        self._bus_acquired = False
        self._last_heartbeat_attempt: datetime | None = None
        self._last_heartbeat_success: datetime | None = None
        self._last_heartbeat_failure: datetime | None = None
        self._last_heartbeat_error: str | None = None
        self._consecutive_heartbeat_failures = 0
        self._total_heartbeat_failures = 0
        self._heartbeat_recoveries = 0
        self._event_sink_failures = 0
        self._last_status_refresh = 0.0

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def controller_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._controller_status)

    def heartbeat_diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "interval_seconds": self._heartbeat_interval_seconds,
                "last_attempt": _isoformat(self._last_heartbeat_attempt),
                "last_success": _isoformat(self._last_heartbeat_success),
                "last_failure": _isoformat(self._last_heartbeat_failure),
                "last_error": self._last_heartbeat_error,
                "consecutive_failures": self._consecutive_heartbeat_failures,
                "total_failures": self._total_heartbeat_failures,
                "recoveries": self._heartbeat_recoveries,
                "event_sink_failures": self._event_sink_failures,
                "watchdog_tripped": (
                    self._controller_status.get("watchdog_tripped") is True
                ),
            }

    def connect(self) -> None:
        with self._lock:
            if self._bus_acquired:
                return
            self._status = DeviceStatus.CONNECTING
            acquired = False
            try:
                self._bus.acquire()
                acquired = True
                self._bus_acquired = True
                self._update_controller_status(self._bus.get_status())
                now = datetime.now(UTC)
                self._last_heartbeat_attempt = now
                self._last_heartbeat_success = now
                self._last_heartbeat_error = None
                self._consecutive_heartbeat_failures = 0
                self._last_status_refresh = monotonic()
            except Exception:
                if acquired:
                    self._bus.release()
                    self._bus_acquired = False
                self._status = DeviceStatus.DISCONNECTED
                raise
            self._status = DeviceStatus.READY
            self._stop.clear()
            self._heartbeat_thread = Thread(target=self._heartbeat_loop, name=f"{self.device_id}-heartbeat", daemon=True)
            self._heartbeat_thread.start()

    def disconnect(self) -> None:
        if not self._bus_acquired:
            self._status = DeviceStatus.DISCONNECTED
            return
        self._stop.set()
        thread = self._heartbeat_thread
        if thread is not None:
            thread.join(timeout=self._heartbeat_interval_seconds + 1.0)
        with self._lock:
            try:
                if self._status is not DeviceStatus.DISCONNECTED:
                    self._bus.apply_safe_state()
            finally:
                self._bus.release()
                self._bus_acquired = False
                self._status = DeviceStatus.DISCONNECTED
                self._heartbeat_thread = None

    def refresh_status(self) -> dict[str, Any]:
        with self._lock:
            try:
                self._update_controller_status(self._bus.get_status())
                self._status = DeviceStatus.READY
            except Exception:
                self._status = DeviceStatus.DEGRADED
                raise
            return dict(self._controller_status)

    def set_output(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._bus.set_output(name, enabled)
            self._update_controller_status(self._bus.get_status())

    def rearm(self) -> None:
        with self._lock:
            self._bus.rearm()
            self._update_controller_status(self._bus.get_status())
            self._record_event(
                "ESP32 watchdog rearmed by operator.",
                EventSeverity.INFO,
            )

    def enter_safe_state(self) -> None:
        with self._lock:
            self._bus.apply_safe_state()
            self._update_controller_status(self._bus.get_status())

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_interval_seconds):
            self._heartbeat_once()

    def _heartbeat_once(self) -> None:
        attempted_at = datetime.now(UTC)
        try:
            with self._lock:
                self._last_heartbeat_attempt = attempted_at
                self._bus.heartbeat()
                previous_failures = self._consecutive_heartbeat_failures
                self._last_heartbeat_success = datetime.now(UTC)
                self._last_heartbeat_error = None
                self._consecutive_heartbeat_failures = 0
                self._status = DeviceStatus.READY
                if previous_failures:
                    self._heartbeat_recoveries += 1
                    self._record_event(
                        "ESP32 heartbeat communication recovered after "
                        f"{previous_failures} consecutive failure(s).",
                        EventSeverity.INFO,
                    )
                if monotonic() - self._last_status_refresh >= 30.0:
                    try:
                        self._update_controller_status(self._bus.get_status())
                        self._last_status_refresh = monotonic()
                    except Exception as error:
                        self._record_event(
                            "ESP32 periodic watchdog-status check failed.",
                            EventSeverity.WARNING,
                            f"{type(error).__name__}: {error}",
                        )
        except Exception as error:
            with self._lock:
                first_failure = self._consecutive_heartbeat_failures == 0
                self._last_heartbeat_failure = datetime.now(UTC)
                self._last_heartbeat_error = f"{type(error).__name__}: {error}"
                self._consecutive_heartbeat_failures += 1
                self._total_heartbeat_failures += 1
                self._status = DeviceStatus.DEGRADED
                if first_failure:
                    self._record_event(
                        "ESP32 heartbeat communication failed; the watchdog "
                        "may trip if communication is not restored.",
                        EventSeverity.WARNING,
                        self._last_heartbeat_error,
                    )

    def _update_controller_status(self, status: dict[str, Any]) -> None:
        was_tripped = self._controller_status.get("watchdog_tripped") is True
        self._controller_status = dict(status)
        is_tripped = self._controller_status.get("watchdog_tripped") is True
        if is_tripped and not was_tripped:
            self._record_event(
                "ESP32 watchdog tripped and forced outputs to their safe state.",
                EventSeverity.CRITICAL,
            )

    def _record_event(
        self,
        message: str,
        severity: EventSeverity,
        technical_details: str | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(
                Event(source=self.device_id, message=message, severity=severity),
                technical_details,
            )
        except Exception:
            self._event_sink_failures += 1


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
