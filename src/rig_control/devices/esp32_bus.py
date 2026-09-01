from threading import RLock
from typing import Any

from rig_control.esp32.session import ControllerSession
from rig_control.models import DeviceStatus


class Esp32Bus:
    """One refcounted, serialized connection to a physical ESP32."""

    def __init__(self, bus_id: str, session: ControllerSession) -> None:
        if not isinstance(bus_id, str) or not bus_id.strip():
            raise ValueError("ESP32 bus ID cannot be empty")
        self._bus_id = bus_id
        self._session = session
        self._lock = RLock()
        self._client_count = 0

    @property
    def bus_id(self) -> str:
        return self._bus_id

    @property
    def is_connected(self) -> bool:
        return self._session.status is not DeviceStatus.DISCONNECTED

    @property
    def client_count(self) -> int:
        return self._client_count

    def acquire(self) -> None:
        """Open the connection for the first logical device."""

        with self._lock:
            if self._client_count == 0:
                self._session.connect()
            self._client_count += 1

    def release(self) -> None:
        """Close the connection after the last logical device releases it."""

        with self._lock:
            if self._client_count <= 0:
                raise RuntimeError(
                    f"ESP32 bus {self.bus_id!r} has no acquired clients"
                )
            self._client_count -= 1
            if self._client_count == 0:
                self._session.disconnect()

    def heartbeat(self) -> None:
        with self._lock:
            self._require_connected()
            self._session.client.heartbeat()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            self._require_connected()
            return self._session.client.get_status()

    def set_output(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._require_connected()
            self._session.client.set_output(name, enabled)

    def apply_safe_state(self) -> None:
        with self._lock:
            self._require_connected()
            self._session.client.apply_safe_state()

    def rearm(self) -> None:
        with self._lock:
            self._require_connected()
            self._session.client.rearm()

    def read_sensors(self) -> list[dict[str, Any]]:
        with self._lock:
            self._require_connected()
            return self._session.client.read_sensors()

    def read_holding_registers(
        self, slave: int, address: int, count: int
    ) -> list[int]:
        with self._lock:
            self._require_connected()
            return self._session.client.read_holding_registers(
                slave, address, count
            )

    def write_register(self, slave: int, address: int, value: int) -> None:
        with self._lock:
            self._require_connected()
            self._session.client.write_register(slave, address, value)

    def _require_connected(self) -> None:
        if self._client_count <= 0 or not self.is_connected:
            raise RuntimeError(f"ESP32 bus {self.bus_id!r} is not connected")
