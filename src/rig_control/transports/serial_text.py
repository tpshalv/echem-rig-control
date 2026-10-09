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

    def read_line(self) -> str:
        """Read one further reply line without sending anything.

        For instruments that follow a data reply with a separate
        acknowledgement line. Transports that never need it may leave this
        unimplemented.
        """

        raise NotImplementedError(f"{type(self).__name__} cannot read extra lines")