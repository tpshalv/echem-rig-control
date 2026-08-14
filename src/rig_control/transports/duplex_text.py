from abc import ABC, abstractmethod


class DuplexTextTransport(ABC):
    """Bidirectional text-message channel between two endpoints."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return whether the communication channel is connected."""

    @abstractmethod
    def connect(self) -> None:
        """Open the communication channel."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the communication channel."""

    @abstractmethod
    def send(self, message: str) -> None:
        """Send one complete text message."""

    @abstractmethod
    def receive(self) -> str:
        """Receive one complete text message."""