"""OHAUS 30910709 revision A, section 6 (EN-19/20).

The manual does not give byte-level reply examples. Accept bare query values
or command-prefixed values; require the documented '<command> A' for writes.
See README.md for the reply layouts requiring physical verification.
"""

from dataclasses import dataclass
from enum import IntEnum
from math import isfinite
import re

from rig_control.transports.serial_text import SerialTextTransport


class GuardianProtocolError(ValueError):
    pass


# OHAUS error table. There is no E6. 'N/A' (no power, missing or blown fuse)
# cannot be reported over serial, so it is not listed.
ERROR_MEANINGS = {
    "E1": "Plate RTD disconnected",
    "E2": "Plate RTD error",
    "E3": "Stir error",
    "E4": "Probe RTD disconnected",
    "E5": "Probe RTD error",
    "E7": "User probe error: external probe connected while heating is on",
    "E8": "Plate over temperature",
    "E9": "Heating not working",
    "E10": "Triac fault",
    "AC Err": "Mains frequency out of range (40-55 Hz for 50 Hz rating, 55-70 Hz for 60 Hz)",
}


class OperatingMode(IntEnum):
    IDLE = 0
    HEATING_PLATE = 1
    HEATING_PROBE = 2
    STIRRING = 3
    HEATING_PLATE_AND_STIRRING = 4
    HEATING_PROBE_AND_STIRRING = 5
    ERROR = 99


@dataclass(frozen=True, slots=True)
class GuardianIdentity:
    model: str
    serial_number: str
    firmware_version: str
    manufacturer: str = "OHAUS"


def parse_number(text: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise GuardianProtocolError(f"Invalid numeric reply: {text!r}") from error
    if not isfinite(value):
        raise GuardianProtocolError(f"Non-finite numeric reply: {text!r}")
    return value


def parse_temperatures(text: str) -> tuple[float, float | None]:
    values = re.split(r"\s*,\s*|\s+", text.strip())
    if len(values) not in {1, 2}:
        raise GuardianProtocolError(f"Expected plate and optional probe temperature: {text!r}")
    return parse_number(values[0]), parse_number(values[1]) if len(values) == 2 else None


def parse_timer(text: str) -> int:
    match = re.fullmatch(r"(\d{2}):([0-5]\d):([0-5]\d)", text)
    if match is None:
        raise GuardianProtocolError(f"Invalid timer: {text!r}")
    return int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3])


class GuardianProtocol:
    QUERIES = frozenset({"MODEL", "SERIAL", "VERSION", "ID", "MODE",
                        "TARGET_TEMPERATURE", "TARGET_SPEED", "MEASURED_TEMPERATURE",
                        "MEASURED_SPEED", "TIMER", "PARAM"})
    WRITES = frozenset({"START_HEAT", "STOP_HEAT", "START_STIR", "STOP_STIR",
                       "TARGET_TEMPERATURE", "TARGET_SPEED", "TIMER", "TIMER_RESET"})

    def __init__(self, transport: SerialTextTransport) -> None:
        self.transport = transport

    @staticmethod
    def build(command: str, argument: str | None = None) -> str:
        if command not in GuardianProtocol.QUERIES | GuardianProtocol.WRITES:
            raise ValueError(f"Unknown Guardian command: {command}")
        message = command if argument is None else f"{command} {argument}"
        if any(c in message for c in "\r\n") or len(message.encode("ascii")) + 2 > 80:
            raise ValueError("Invalid Guardian command framing")
        return message

    def query(self, command: str, argument: str | None = None) -> str:
        if command not in self.QUERIES:
            raise ValueError("Not a query")
        response = self.transport.request(self.build(command, argument)).strip()
        if response == "L" or response == f"{command} L":
            raise GuardianProtocolError(f"Instrument rejected {command}")
        if response.startswith(command + " "):
            response = response[len(command) + 1:].strip()
        # Hardware (e-G52, MODEL query) replies '<command> A <value>'.
        if response.startswith("A "):
            response = response[2:].strip()
        if not response or response == "A":
            raise GuardianProtocolError(f"Missing value for {command}")
        return response

    def write(self, command: str, argument: str | None = None) -> None:
        if command not in self.WRITES:
            raise ValueError("Not a writable command")
        response = self.transport.request(self.build(command, argument)).strip()
        if response != f"{command} A":
            raise GuardianProtocolError(f"{command} was not acknowledged: {response!r}")

    def number(self, command: str) -> float:
        return parse_number(self.query(command))

    def mode(self) -> OperatingMode:
        value = self.number("MODE")
        try:
            return OperatingMode(value)
        except ValueError as error:
            raise GuardianProtocolError(f"Unknown operating mode: {value}") from error

    def error_code(self) -> str:
        # PARAM 0 is the documented one-shot dump, not a streaming subscription.
        fields = self.query("PARAM", "0").rstrip(",").split(",")
        if len(fields) != 8:
            raise GuardianProtocolError("Expected eight comma-separated PARAM fields")
        parse_timer(fields[0].strip())
        code = fields[-1].strip()
        if code != "0" and code not in ERROR_MEANINGS:
            raise GuardianProtocolError(f"Unrecognised error code: {code!r}")
        return code
