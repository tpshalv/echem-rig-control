from abc import ABC, abstractmethod


class ScpiTransport(ABC):
    """Communication channel for a SCPI-compatible instrument."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return whether the instrument connection is open."""

    @abstractmethod
    def open(self) -> None:
        """Open the instrument connection."""

    @abstractmethod
    def close(self) -> None:
        """Close the instrument connection."""

    @abstractmethod
    def write(self, command: str) -> None:
        """Send a command that does not return a response."""

    @abstractmethod
    def query(self, command: str) -> str:
        """Send a query and return its response."""