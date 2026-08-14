from abc import ABC, abstractmethod


class SerialTextTransport(ABC):
    """Atomic text request/response connection over a serial link."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return whether the serial connection is open."""

    @abstractmethod
    def open(self) -> None:
        """Open the serial connection."""

    @abstractmethod
    def close(self) -> None:
        """Close the serial connection."""

    @abstractmethod
    def request(self, message: str) -> str:
        """Send one request and return its complete response."""