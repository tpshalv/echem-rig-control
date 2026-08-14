from collections import defaultdict, deque

from rig_control.transports.serial_text import SerialTextTransport


class SimulatedSerialTextTransport(SerialTextTransport):
    """Controllable serial-text connection requiring no hardware."""

    def __init__(self) -> None:
        self._is_open = False
        self._responses: dict[
            str,
            deque[str | Exception],
        ] = defaultdict(deque)
        self._requests: list[str] = []

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def requests(self) -> tuple[str, ...]:
        """Return every request in transmission order."""

        return tuple(self._requests)

    def open(self) -> None:
        if self.is_open:
            raise RuntimeError(
                "Simulated serial-text transport is already open"
            )

        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def queue_response(
        self,
        message: str,
        response: str,
    ) -> None:
        """Queue a response for a particular future request."""

        self._validate_message(message)

        if not isinstance(response, str):
            raise TypeError("Simulated serial response must be text")

        self._responses[message].append(response)

    def queue_error(
        self,
        message: str,
        error: Exception,
    ) -> None:
        """Queue an exception for a particular future request."""

        self._validate_message(message)

        if not isinstance(error, Exception):
            raise TypeError(
                "Simulated serial error must be an Exception"
            )

        self._responses[message].append(error)

    def request(self, message: str) -> str:
        if not self.is_open:
            raise RuntimeError(
                "Simulated serial-text transport is not open"
            )

        self._validate_message(message)
        self._requests.append(message)

        if not self._responses[message]:
            raise RuntimeError(
                "No simulated serial response queued for request: "
                f"{message!r}"
            )

        result = self._responses[message].popleft()

        if isinstance(result, Exception):
            raise result

        return result

    @staticmethod
    def _validate_message(message: str) -> None:
        if not isinstance(message, str):
            raise TypeError("Serial request must be text")

        if not message.strip():
            raise ValueError("Serial request cannot be empty")

        if "\r" in message or "\n" in message:
            raise ValueError(
                "Serial request must not contain line terminators"
            )