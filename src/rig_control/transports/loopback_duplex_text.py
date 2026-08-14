from collections import deque
from collections.abc import Callable

from rig_control.transports.duplex_text import DuplexTextTransport


class LoopbackDuplexTextTransport(DuplexTextTransport):
    """In-memory transport connected directly to a message responder."""

    def __init__(self, responder: Callable[[str], str]) -> None:
        self._responder = responder
        self._is_connected = False
        self._responses: deque[str] = deque()

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(self) -> None:
        self._is_connected = True

    def disconnect(self) -> None:
        self._is_connected = False
        self._responses.clear()

    def send(self, message: str) -> None:
        self._require_connection()
        self._responses.append(self._responder(message))

    def receive(self) -> str:
        self._require_connection()

        if not self._responses:
            raise RuntimeError("No response is available")

        return self._responses.popleft()

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise RuntimeError("Transport is not connected")