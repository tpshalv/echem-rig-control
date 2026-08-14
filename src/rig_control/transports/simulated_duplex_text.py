from collections import deque

from rig_control.transports.duplex_text import DuplexTextTransport


class SimulatedDuplexTextTransport(DuplexTextTransport):
    """In-memory communication channel used during development."""

    def __init__(self) -> None:
        self._is_connected = False
        self._incoming: deque[str] = deque()
        self._sent: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def sent_messages(self) -> tuple[str, ...]:
        """Return messages sent by the PC."""
        return tuple(self._sent)

    def connect(self) -> None:
        self._is_connected = True

    def disconnect(self) -> None:
        self._is_connected = False

    def send(self, message: str) -> None:
        self._require_connection()
        self._sent.append(message)

    def receive(self) -> str:
        self._require_connection()

        if not self._incoming:
            raise RuntimeError("No message is available")

        return self._incoming.popleft()

    def queue_incoming(self, message: str) -> None:
        """Simulate a message arriving from remote hardware."""
        self._incoming.append(message)

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise RuntimeError("Transport is not connected")