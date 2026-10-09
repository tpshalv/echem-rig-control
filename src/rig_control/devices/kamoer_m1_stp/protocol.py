"""Kamoer M1-STP Modbus RTU protocol.

Register map and serial settings are from Kamoer's "M1 Control Protocol"
document (2022-11-25). Serial: 9600 8N1, no hardware flow control.

    Coils (function 0x01 read, 0x05 write single):
        0x0001  Pump switch: 0 off, 1 on
        0x0002  Direction: 0 forward, 1 reverse
        0x0003  Run mode: 0 run by time, 1 run by lap count

    Holding registers (function 0x03 read, 0x10 write multiple), each pair
    a 32-bit value, first register the high word:
        0x4001/0x4002  Speed: unsigned, RPM * 100
        0x4003/0x4004  Target lap count: IEEE-754 float
        0x4005/0x4006  Target run time: unsigned, milliseconds

    Input registers (function 0x04, read-only):
        0x3001  Software version (packed 3+5+8 bit major/minor/revision)
        0x3002  Hardware version (same packing)
        0x3003  Fault status: 0 normal, non-zero abnormal

Register byte order is standard Modbus (high byte first), confirmed on the
bench: with each register byte-swapped, a 50 RPM request ran the pump at
348.4 RPM (0x1388 sent as 0x8813) and a 350 RPM pump read back as 472.4
(0x88B8 decoded as 0xB888). An earlier version of this driver did swap,
based on Modbus Poll capture files; those files store each register in the
PC's own little-endian order, so the swap was an artefact of the file
format, not of the wire. The CRC algorithm is verified against a different
Kamoer pump's documented frame (see CRC test fixtures).
"""

from collections.abc import Callable, Sequence
from math import isfinite
from threading import RLock
from time import monotonic
import struct

from rig_control.transports.serial_binary import SerialBinaryTransport


DEFAULT_BAUD_RATE = 9600

COIL_PUMP_SWITCH = 0x0001
COIL_DIRECTION = 0x0002
COIL_RUN_MODE = 0x0003

REGISTER_SPEED = 0x4001          # unsigned32, RPM * 100
REGISTER_TARGET_LAPS = 0x4003    # float32
REGISTER_TARGET_TIME_MS = 0x4005 # unsigned32, milliseconds

INPUT_SOFTWARE_VERSION = 0x3001
INPUT_HARDWARE_VERSION = 0x3002
INPUT_FAULT_STATUS = 0x3003

_COIL_ON = b"\xff\x00"
_COIL_OFF = b"\x00\x00"


class KamoerM1StpProtocolError(RuntimeError):
    """A Modbus reply from the pump cannot be trusted."""


def modbus_crc16(data: bytes) -> int:
    """Standard Modbus RTU CRC-16 (polynomial 0xA001, seed 0xFFFF)."""

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _append_crc(frame: bytes) -> bytes:
    return frame + struct.pack("<H", modbus_crc16(frame))


def pack_registers(words: Sequence[int]) -> bytes:
    """Encode register values for the wire (each register big-endian, in
    address order)."""

    for word in words:
        if isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 0xFFFF:
            raise ValueError(f"Register value {word!r} must be an int in 0..65535")
    return struct.pack(f">{len(words)}H", *words)


def unpack_registers(data: bytes) -> tuple[int, ...]:
    """Decode registers read off the wire."""

    if len(data) % 2:
        raise KamoerM1StpProtocolError(f"Odd-length register payload: {data.hex(' ')}")
    return struct.unpack(f">{len(data) // 2}H", data)


def words_to_uint32(high: int, low: int) -> int:
    return (high << 16) | low


def uint32_to_words(value: int) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"Value {value!r} must be an int in 0..2**32-1")
    return (value >> 16) & 0xFFFF, value & 0xFFFF


def words_to_float32(high: int, low: int) -> float:
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def float32_to_words(value: float) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"Value {value!r} must be a finite number")
    return struct.unpack(">HH", struct.pack(">f", value))


class KamoerM1StpProtocol:
    """One Modbus RTU session with an M1-STP pump."""

    def __init__(
        self,
        transport: SerialBinaryTransport,
        slave: int,
        timeout_seconds: float = 1.0,
        *,
        trace: Callable[[str, bytes], None] | None = None,
    ) -> None:
        if isinstance(slave, bool) or not isinstance(slave, int) or not 1 <= slave <= 247:
            raise ValueError("Modbus slave address must be between 1 and 247")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("Protocol timeout must be finite and positive")
        self.transport = transport
        self.slave = slave
        self.timeout_seconds = timeout_seconds
        self._trace = trace
        self._lock = RLock()

    def read_coils(self, address: int, count: int) -> tuple[bool, ...]:
        data = self._request_read(0x01, address, count, expected_bytes=-(-count // 8))
        return tuple(bool(data[index // 8] & (1 << (index % 8))) for index in range(count))

    def write_coil(self, address: int, value: bool) -> None:
        field = _COIL_ON if value else _COIL_OFF
        self._request_write(0x05, address, request_body=field, echoed_field=field)

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        return unpack_registers(self._request_read(0x03, address, count, expected_bytes=2 * count))

    def write_registers(self, address: int, words: Sequence[int]) -> None:
        data = pack_registers(words)
        request_body = struct.pack(">HB", len(words), len(data)) + data
        echoed_field = struct.pack(">H", len(words))
        self._request_write(0x10, address, request_body=request_body, echoed_field=echoed_field)

    def read_input_registers(self, address: int, count: int) -> tuple[int, ...]:
        return unpack_registers(self._request_read(0x04, address, count, expected_bytes=2 * count))

    def _request_read(self, function: int, address: int, count: int, *, expected_bytes: int) -> bytes:
        if not 0 <= address <= 0xFFFF:
            raise ValueError("Modbus address must be in 0..65535")
        if not 1 <= count <= 125:
            raise ValueError("Modbus read count must be in 1..125")
        with self._lock:
            deadline = self._send(function, struct.pack(">HH", address, count))
            self._receive_header(function, deadline)
            byte_count = self._read_exact(1, deadline)[0]
            if byte_count != expected_bytes:
                raise KamoerM1StpProtocolError(
                    f"Expected {expected_bytes} response bytes, pump reported {byte_count}"
                )
            data = self._read_exact(byte_count, deadline)
            self._verify_crc(bytes([self.slave, function, byte_count]) + data, deadline)
            return data

    def _request_write(self, function: int, address: int, *, request_body: bytes, echoed_field: bytes) -> None:
        if not 0 <= address <= 0xFFFF:
            raise ValueError("Modbus address must be in 0..65535")
        with self._lock:
            deadline = self._send(function, struct.pack(">H", address) + request_body)
            self._receive_header(function, deadline)
            tail = self._read_exact(4, deadline)
            self._verify_crc(bytes([self.slave, function]) + tail, deadline)
            expected_tail = struct.pack(">H", address) + echoed_field
            if tail != expected_tail:
                raise KamoerM1StpProtocolError(
                    f"Write was not confirmed: pump echoed {tail.hex(' ')}, "
                    f"expected {expected_tail.hex(' ')}"
                )

    def _send(self, function: int, body: bytes) -> float:
        frame = _append_crc(bytes([self.slave, function]) + body)
        self.transport.reset_input_buffer()
        self.transport.write(frame)
        if self._trace is not None:
            self._trace("TX", frame)
        return monotonic() + self.timeout_seconds

    def _receive_header(self, function: int, deadline: float) -> None:
        slave, received_function = self._read_exact(2, deadline)
        if slave != self.slave:
            raise KamoerM1StpProtocolError(f"Reply from slave {slave}, expected {self.slave}")
        if received_function == function | 0x80:
            code = self._read_exact(1, deadline)[0]
            self._verify_crc(bytes([slave, received_function, code]), deadline)
            raise KamoerM1StpProtocolError(
                f"Pump reported Modbus exception {code} for function 0x{function:02X}"
            )
        if received_function != function:
            raise KamoerM1StpProtocolError(
                f"Unexpected function 0x{received_function:02X}, expected 0x{function:02X}"
            )

    def _verify_crc(self, payload: bytes, deadline: float) -> None:
        received = self._read_exact(2, deadline)
        expected = struct.pack("<H", modbus_crc16(payload))
        if received != expected:
            raise KamoerM1StpProtocolError(f"CRC mismatch in reply: {(payload + received).hex(' ')}")

    def _read_exact(self, size: int, deadline: float) -> bytes:
        buffer = bytearray()
        while len(buffer) < size:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Incomplete Modbus reply from slave {self.slave}: {bytes(buffer).hex(' ')}")
            chunk = self.transport.read(size - len(buffer), timeout_seconds=remaining)
            if self._trace is not None and chunk:
                self._trace("RX", chunk)
            if not chunk:
                raise TimeoutError(f"Incomplete Modbus reply from slave {self.slave}: {bytes(buffer).hex(' ')}")
            buffer.extend(chunk)
        return bytes(buffer)
