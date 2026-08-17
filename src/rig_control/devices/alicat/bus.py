from threading import Lock

from rig_control.transports.serial_text import SerialTextTransport


class AlicatBus:
    """One shared serial connection serving multiple Alicat devices."""

    def __init__(
        self,
        bus_id: str,
        transport: SerialTextTransport,
    ) -> None:
        if not isinstance(bus_id, str) or not bus_id.strip():
            raise ValueError("Alicat bus ID cannot be empty")

        self._bus_id = bus_id
        self._transport = transport
        self._request_lock = Lock()
        self._client_count = 0
        self._opened_by_clients = False

    @property
    def bus_id(self) -> str:
        return self._bus_id

    @property
    def is_connected(self) -> bool:
        return self._transport.is_open

    def connect(self) -> None:
        """Open the single shared serial connection."""

        if self.is_connected:
            raise RuntimeError(
                f"Alicat bus {self.bus_id!r} is already connected"
            )

        try:
            self._transport.open()
        except Exception as error:
            raise ConnectionError(
                f"Could not connect Alicat bus {self.bus_id!r}: "
                f"{type(error).__name__}: {error}"
            ) from error

    def disconnect(self) -> None:
        """Close the shared serial connection."""

        self._transport.close()

    def acquire(self) -> None:
        """Acquire one device's reference to the shared connection."""

        with self._request_lock:
            if self._client_count == 0 and not self.is_connected:
                self.connect()
                self._opened_by_clients = True
            self._client_count += 1

    def release(self) -> None:
        """Release one device without disrupting remaining users."""

        with self._request_lock:
            if self._client_count <= 0:
                raise RuntimeError(
                    f"Alicat bus {self.bus_id!r} has no acquired clients"
                )
            self._client_count -= 1
            if self._client_count == 0 and self._opened_by_clients:
                self.disconnect()
                self._opened_by_clients = False

    def request(self, message: str) -> str:
        """Perform one uninterrupted request/response exchange."""

        if not self.is_connected:
            raise RuntimeError(
                f"Alicat bus {self.bus_id!r} is not connected"
            )

        # Only one device may use the shared BB3 connection at a time.
        with self._request_lock:
            try:
                return self._transport.request(message)
            except Exception as error:
                raise RuntimeError(
                    f"Alicat bus {self.bus_id!r} failed while "
                    f"processing request {message!r}: "
                    f"{type(error).__name__}: {error}"
                ) from error
