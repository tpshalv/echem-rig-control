"""TA Series Communication Protocols, revision 2022-07-25, TA612 examples.

Binary frames, 9600/8N1. The example identity length is 7 (the summary table's
6 is inconsistent with its two 16-bit fields). See README for uncertainties.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
import struct
from threading import RLock
from time import monotonic

from rig_control.transports.serial_binary import SerialBinaryTransport


CHANNELS = ("tc1", "tc2", "tc3", "tc4")
STOP_AND_IDENTIFY = bytes.fromhex("AA 55 00 03 02")
READ_TEMPERATURES = bytes.fromhex("AA 55 01 03 03")


class Ta612cProtocolError(ValueError):
    """A response cannot be trusted as a TA612C-protocol reading."""


@dataclass(frozen=True, slots=True)
class Ta612cIdentity:
    model_number: int
    version_number: int

    @property
    def firmware_version(self) -> str | None:
        return f"{self.version_number / 100:.2f}" if self.version_number else None


def parse_frame(frame: bytes, command: int, payload_size: int) -> bytes:
    if (len(frame) != payload_size + 5 or frame[:2] != b"\x55\xaa"
            or frame[2] != command or frame[3] != len(frame) - 2):
        raise Ta612cProtocolError(f"Invalid frame header, command or length: {frame.hex(' ')}")
    if sum(frame[:-1]) & 0xff != frame[-1]:
        raise Ta612cProtocolError(f"Invalid frame checksum: {frame.hex(' ')}")
    return frame[4:-1]


def parse_identity(frame: bytes) -> Ta612cIdentity:
    return Ta612cIdentity(*struct.unpack("<HH", parse_frame(frame, 0, 4)))


def parse_raw_temperatures(frame: bytes) -> tuple[int, ...]:
    return struct.unpack("<4H", parse_frame(frame, 1, 8))


def decode_temperature(raw: int) -> float:
    # Negative representation and missing-probe codes are not specified in the
    # supplied document. Two's complement is provisional. Reserve FFFF rather
    # than quietly logging a potentially missing probe as -0.1 degC.
    if raw == 0xffff:
        raise Ta612cProtocolError("Unavailable/ambiguous temperature word 0xFFFF")
    signed = raw if raw < 0x8000 else raw - 0x10000
    value = signed / 10.0
    if not -200 <= value <= 1372:
        raise Ta612cProtocolError(f"Temperature word 0x{raw:04X} is outside -200..1372 degC; probe missing or incompatible reply")
    return value


class Ta612cProtocol:
    def __init__(self, transport: SerialBinaryTransport, timeout_seconds: float = 2.0,
                 *, trace: Callable[[str, bytes], None] | None = None) -> None:
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Protocol timeout must be finite and positive")
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self._trace = trace
        self._lock = RLock()

    def _request(self, request: bytes, response_size: int) -> bytes:
        with self._lock:
            # Only one-shot requests are used. Discard leftovers from a timed-out
            # previous request before sending a fresh one, never reuse cached data.
            self.transport.reset_input_buffer()
            self.transport.write(request)
            if self._trace is not None:
                self._trace("TX", request)
            deadline = monotonic() + self.timeout_seconds
            response = bytearray()
            while len(response) < response_size:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Incomplete TA612C response: {response.hex(' ')}")
                chunk = self.transport.read(response_size - len(response), timeout_seconds=remaining)
                if self._trace is not None and chunk:
                    self._trace("RX", chunk)
                if not chunk:
                    raise TimeoutError(f"Incomplete TA612C response: {response.hex(' ')}")
                if len(chunk) > response_size - len(response):
                    raise Ta612cProtocolError("Transport returned excess bytes")
                response.extend(chunk)
            return bytes(response)

    def identify(self) -> Ta612cIdentity:
        return parse_identity(self._request(STOP_AND_IDENTIFY, 9))

    def read_raw_temperatures(self) -> tuple[int, ...]:
        return parse_raw_temperatures(self._request(READ_TEMPERATURES, 13))
