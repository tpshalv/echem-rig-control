from abc import ABC, abstractmethod


class SerialBinaryTransport(ABC):
    """Unframed binary serial I/O; device protocols own framing and checksums."""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def reset_input_buffer(self) -> None: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, size: int, *, timeout_seconds: float) -> bytes:
        """Read up to size bytes within the supplied timeout; empty means timeout."""
