from threading import Event as ThreadEvent, RLock, Thread
from typing import Any

from rig_control.devices.base import Device
from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.models import DeviceStatus


class Esp32Controller(Device):
    """Rig device backed by the ESP32 JSON controller protocol."""

    def __init__(self, device_id: str, bus: Esp32Bus, *, heartbeat_interval_seconds: float = 5.0) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("Heartbeat interval must be greater than zero")
        self._device_id = device_id
        self._bus = bus
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._status = DeviceStatus.DISCONNECTED
        self._controller_status: dict[str, Any] = {}
        self._lock = RLock()
        self._stop = ThreadEvent()
        self._heartbeat_thread: Thread | None = None
        self._bus_acquired = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def controller_status(self) -> dict[str, Any]:
        return dict(self._controller_status)

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
                self._controller_status = self._bus.get_status()
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
                self._controller_status = self._bus.get_status()
                self._status = DeviceStatus.READY
            except Exception:
                self._status = DeviceStatus.DEGRADED
                raise
            return dict(self._controller_status)

    def set_output(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._bus.set_output(name, enabled)
            self._controller_status = self._bus.get_status()

    def rearm(self) -> None:
        with self._lock:
            self._bus.rearm()
            self._controller_status = self._bus.get_status()

    def enter_safe_state(self) -> None:
        with self._lock:
            self._bus.apply_safe_state()
            self._controller_status = self._bus.get_status()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_interval_seconds):
            try:
                with self._lock:
                    self._bus.heartbeat()
                    self._status = DeviceStatus.READY
            except Exception:
                self._status = DeviceStatus.DEGRADED
