from collections.abc import Callable
from typing import Protocol

from rig_control.transports.serial_text import SerialTextTransport


class _SerialPort(Protocol):
    @property
    def is_open(self) -> bool: ...

    def close(self) -> None: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def readline(self) -> bytes: ...


type SerialPortFactory = Callable[..., _SerialPort]


class PySerialTextTransport(SerialTextTransport):
    """Carriage-return-delimited text requests using pyserial."""

    def __init__(
        self,
        port: str,
        baud_rate: int,
        timeout_seconds: float,
        *,
        serial_factory: SerialPortFactory | None = None,
        line_ending: str = "\r",
        xonxoff: bool = False,
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

        if line_ending not in {"\r", "\n", "\r\n"}:
            raise ValueError("Unsupported serial line ending")
        if not isinstance(xonxoff, bool):
            raise TypeError("xonxoff must be Boolean")
        self._line_ending = line_ending
        self._xonxoff = xonxoff
        self._port = port.strip()
        self._baud_rate = baud_rate
        self._timeout_seconds = float(timeout_seconds)
        self._serial_factory = serial_factory
        self._serial: _SerialPort | None = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        if self.is_open:
            raise RuntimeError(f"Serial port {self._port!r} is already open")

        factory = self._serial_factory or self._load_pyserial_factory()
        self._serial = factory(
            port=self._port,
            baudrate=self._baud_rate,
            timeout=self._timeout_seconds,
            write_timeout=self._timeout_seconds,
            bytesize=8,
            parity="N",
            stopbits=1,
            xonxoff=self._xonxoff,
            rtscts=False,
            dsrdtr=False,
        )

    def close(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        finally:
            self._serial = None

    def request(self, message: str) -> str:
        if not self.is_open or self._serial is None:
            raise RuntimeError(f"Serial port {self._port!r} is not open")
        if not isinstance(message, str):
            raise TypeError("Serial request must be text")
        if not message.strip():
            raise ValueError("Serial request cannot be empty")
        if "\r" in message or "\n" in message:
            raise ValueError("Serial request must not contain line terminators")

        encoded = (message + self._line_ending).encode("ascii")
        written = self._serial.write(encoded)
        if written != len(encoded):
            raise OSError(
                f"Incomplete write to serial port {self._port!r}: "
                f"wrote {written} of {len(encoded)} bytes"
            )
        self._serial.flush()
        response = self._serial.readline()
        if not response:
            raise TimeoutError(
                f"No response from serial port {self._port!r} "
                f"within {self._timeout_seconds:g} seconds for "
                f"request {message!r}"
            )
        if message.upper().endswith(("??M*", "??D*")):
            # Alicat tables terminate with an idle interval, not a single line.
            # Keep this inside the caller's bus lock. Never leave a partial table
            # queued for a subsequent addressed request.
            lines = [response]
            for _ in range(63):
                following = self._serial.readline()
                if not following:
                    response = b"\n".join(line.rstrip(b"\r\n") for line in lines)
                    break
                lines.append(following)
                if sum(map(len, lines)) > 32768:
                    self.close()
                    raise ValueError("Alicat multiline response exceeds 32768 bytes; connection closed")
            else:
                self.close()
                raise ValueError("Alicat multiline response exceeds 64 lines; connection closed")
        try:
            if self._line_ending == "\r\n" and not response.endswith(b"\r\n"):
                raise TimeoutError("Incomplete CRLF serial response")
            decoded = response.decode("ascii").strip("\r\n")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"Non-ASCII response from serial port {self._port!r}: "
                f"{response!r}"
            ) from error
        if not decoded:
            raise ValueError(
                f"Empty response from serial port {self._port!r} for "
                f"request {message!r}"
            )
        return decoded

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
