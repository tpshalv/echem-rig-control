from threading import Lock, Thread
from time import sleep
from typing import Any

import pytest

from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.models import DeviceStatus


class FakeClient:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.lock = Lock()

    def get_status(self) -> dict[str, Any]:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        sleep(0.01)
        with self.lock:
            self.active -= 1
        return {"outputs": {"led": False}}

    def heartbeat(self) -> None: pass
    def set_output(self, name: str, enabled: bool) -> None: pass
    def apply_safe_state(self) -> None: pass
    def rearm(self) -> None: pass
    def read_sensors(self) -> list[dict[str, Any]]: return []


class FakeSession:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.status = DeviceStatus.DISCONNECTED
        self.connect_count = 0
        self.disconnect_count = 0

    def connect(self) -> None:
        self.connect_count += 1
        self.status = DeviceStatus.READY

    def disconnect(self) -> None:
        self.disconnect_count += 1
        self.status = DeviceStatus.DISCONNECTED


def test_bus_opens_once_and_closes_after_last_release() -> None:
    session = FakeSession()
    bus = Esp32Bus("main_esp32", session)  # type: ignore[arg-type]

    bus.acquire()
    bus.acquire()
    bus.release()

    assert session.connect_count == 1
    assert session.disconnect_count == 0
    assert bus.client_count == 1

    bus.release()

    assert session.disconnect_count == 1
    assert bus.is_connected is False


def test_bus_serializes_requests_from_different_devices() -> None:
    session = FakeSession()
    bus = Esp32Bus("main_esp32", session)  # type: ignore[arg-type]
    bus.acquire()
    threads = [Thread(target=bus.get_status) for _ in range(4)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert session.client.maximum_active == 1
    bus.release()


def test_bus_rejects_release_without_client() -> None:
    bus = Esp32Bus("main_esp32", FakeSession())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="no acquired clients"):
        bus.release()
