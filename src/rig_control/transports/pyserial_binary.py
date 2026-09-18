from collections.abc import Callable
from math import isfinite
from typing import Protocol

from rig_control.transports.serial_binary import SerialBinaryTransport


class _SerialPort(Protocol):
    is_open: bool
    timeout: float

    def close(self) -> None: ...
    def reset_input_buffer(self) -> None: ...
    def write(self, data: bytes) -> int: ...
    def read(self, size: int) -> bytes: ...


class PySerialBinaryTransport(SerialBinaryTransport):
    """8N1 binary serial transport with bounded reads and writes, no flow control."""

    def __init__(self, port: str, baud_rate: int, timeout_seconds: float = 2.0,
                 *, serial_factory: Callable[..., _SerialPort] | None = None) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Serial port cannot be empty")
        if isinstance(baud_rate, bool) or not isinstance(baud_rate, int) or baud_rate <= 0:
            raise ValueError("Baud rate must be a positive integer")
        self._validate_timeout(timeout_seconds)
        self._port, self._baud_rate = port.strip(), baud_rate
        self._timeout = timeout_seconds
        self._factory = serial_factory
        self._serial: _SerialPort | None = None

    @staticmethod
    def _validate_timeout(value: float) -> None:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value <= 0):
            raise ValueError("Serial timeout must be finite and positive")

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        if self.is_open:
            raise RuntimeError("Serial port is already open")
        factory = self._factory
        if factory is None:
            try:
                from serial import Serial
            except ImportError as error:
                raise RuntimeError("Serial communication requires pip install -e .[hardware]") from error
            factory = Serial
        self._serial = factory(
            port=self._port, baudrate=self._baud_rate,
            timeout=self._timeout, write_timeout=self._timeout,
            bytesize=8, parity="N", stopbits=1,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )

    def close(self) -> None:
        try:
            if self._serial is not None:
                self._serial.close()
        finally:
            self._serial = None

    def _require_open(self) -> _SerialPort:
        if not self.is_open:
            raise RuntimeError(f"Serial port {self._port!r} is not open")
        assert self._serial is not None
        return self._serial

    def reset_input_buffer(self) -> None:
        self._require_open().reset_input_buffer()

    def write(self, data: bytes) -> None:
        serial = self._require_open()
        if not isinstance(data, bytes) or not data:
            raise ValueError("Binary request must be nonempty bytes")
        written = serial.write(data)
        if written != len(data):
            raise OSError(f"Incomplete binary serial write: {written} of {len(data)} bytes")

    def read(self, size: int, *, timeout_seconds: float) -> bytes:
        serial = self._require_open()
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("Read size must be a positive integer")
        self._validate_timeout(timeout_seconds)
        previous = serial.timeout
        try:
            serial.timeout = timeout_seconds
            return serial.read(size)
        finally:
            serial.timeout = previous
