from collections.abc import Callable
from typing import Protocol

from rig_control.transports.duplex_text import DuplexTextTransport


class _SerialPort(Protocol):
    @property
    def is_open(self) -> bool: ...

    def close(self) -> None: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def readline(self) -> bytes: ...


type SerialPortFactory = Callable[..., _SerialPort]


class PySerialDuplexTextTransport(DuplexTextTransport):
    """Newline-delimited UTF-8 messages over a pyserial connection."""

    def __init__(
        self,
        port: str,
        baud_rate: int = 115200,
        timeout_seconds: float = 2.0,
        *,
        serial_factory: SerialPortFactory | None = None,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Serial port cannot be empty")
        if not isinstance(baud_rate, int) or isinstance(baud_rate, bool):
            raise TypeError("Serial baud rate must be an integer")
        if baud_rate <= 0:
            raise ValueError("Serial baud rate must be positive")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds,
            (int, float),
        ):
            raise TypeError("Serial timeout must be numeric")
        if timeout_seconds <= 0:
            raise ValueError("Serial timeout must be positive")

        self._port = port.strip()
        self._baud_rate = baud_rate
        self._timeout_seconds = float(timeout_seconds)
        self._serial_factory = serial_factory
        self._serial: _SerialPort | None = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self) -> None:
        if self.is_connected:
            raise RuntimeError(f"Serial port {self._port!r} is already connected")

        factory = self._serial_factory or self._load_pyserial_factory()
        self._serial = factory(
            port=self._port,
            baudrate=self._baud_rate,
            timeout=self._timeout_seconds,
            write_timeout=self._timeout_seconds,
            bytesize=8,
            parity="N",
            stopbits=1,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def disconnect(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        finally:
            self._serial = None

    def send(self, message: str) -> None:
        serial_port = self._require_connection()
        if not isinstance(message, str):
            raise TypeError("Serial message must be text")
        if not message:
            raise ValueError("Serial message cannot be empty")
        if "\r" in message or "\n" in message:
            raise ValueError("Serial message must not contain line terminators")

        encoded = f"{message}\n".encode("utf-8")
        written = serial_port.write(encoded)
        if written != len(encoded):
            raise OSError(
                f"Incomplete write to serial port {self._port!r}: "
                f"wrote {written} of {len(encoded)} bytes"
            )
        serial_port.flush()

    def receive(self) -> str:
        serial_port = self._require_connection()
        response = serial_port.readline()
        if not response:
            raise TimeoutError(
                f"No message from serial port {self._port!r} within "
                f"{self._timeout_seconds:g} seconds"
            )
        if not response.endswith(b"\n"):
            raise TimeoutError(
                f"Incomplete message from serial port {self._port!r}: "
                f"no newline received within {self._timeout_seconds:g} seconds"
            )
        try:
            decoded = response.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"Invalid UTF-8 message from serial port {self._port!r}: "
                f"{response!r}"
            ) from error
        if not decoded:
            raise ValueError(f"Empty message from serial port {self._port!r}")
        return decoded

    def _require_connection(self) -> _SerialPort:
        if not self.is_connected or self._serial is None:
            raise RuntimeError(f"Serial port {self._port!r} is not connected")
        return self._serial

    @staticmethod
    def _load_pyserial_factory() -> SerialPortFactory:
        try:
            from serial import Serial
        except ImportError as error:
            raise RuntimeError(
                "Real serial communication requires the optional "
                "hardware dependency: pip install -e .[hardware]"
            ) from error
        return Serial
