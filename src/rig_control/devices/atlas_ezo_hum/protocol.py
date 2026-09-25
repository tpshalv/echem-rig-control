"""UART command framing and response validation for Atlas EZO-HUM."""

from dataclasses import dataclass
from math import isfinite

from rig_control.transports.serial_text import SerialTextTransport


class EzoHumProtocolError(ValueError):
    """An EZO-HUM response was malformed, unexpected, or reports an error."""


@dataclass(frozen=True, slots=True)
class EzoHumIdentity:
    firmware_version: str


@dataclass(frozen=True, slots=True)
class EzoHumReading:
    humidity: float
    temperature: float
    dew_point: float | None = None


class EzoHumProtocol:
    def __init__(self, transport: SerialTextTransport) -> None:
        self.transport = transport

    def configure_output(self, *, include_dew_point: bool) -> None:
        # UART starts in continuous mode. Stop it before issuing request/reply
        # commands, otherwise an unsolicited reading can be mistaken for a reply.
        self._expect_ok("C,0", allow_unsolicited_reading=True)
        self._expect_ok("O,T,1")
        self._expect_ok(f"O,Dew,{1 if include_dew_point else 0}")

    def identify(self) -> EzoHumIdentity:
        response = self._request("i")
        parts = response.split(",")
        if len(parts) != 3 or parts[0] != "?i" or parts[1].upper() != "HUM":
            raise EzoHumProtocolError(f"Unexpected EZO-HUM identity response: {response!r}")
        if not parts[2].strip():
            raise EzoHumProtocolError("EZO-HUM identity has no firmware version")
        return EzoHumIdentity(parts[2].strip())

    def read(self, *, include_dew_point: bool) -> EzoHumReading:
        response = self._request("R")
        values = self._numbers(response)
        expected = 3 if include_dew_point else 2
        if len(values) != expected:
            raise EzoHumProtocolError(
                f"EZO-HUM returned {len(values)} values; expected {expected}: {response!r}"
            )
        humidity, temperature = values[:2]
        dew_point = values[2] if include_dew_point else None
        return EzoHumReading(humidity, temperature, dew_point)

    def _expect_ok(self, command: str, *, allow_unsolicited_reading: bool = False) -> None:
        response = self._request(command)
        if response == "*OK":
            return
        if allow_unsolicited_reading:
            # A probe may have sent one final continuous reading just before C,0
            # was received. Repeat the idempotent command to obtain its ack.
            self._numbers(response)
            if self._request(command) == "*OK":
                return
        raise EzoHumProtocolError(
            f"EZO-HUM command {command!r} was not acknowledged: {response!r}"
        )

    def _request(self, command: str) -> str:
        response = self.transport.request(command).strip()
        if response.startswith("*ER"):
            raise EzoHumProtocolError(
                f"EZO-HUM rejected command {command!r}: {response}"
            )
        return response

    @staticmethod
    def _numbers(response: str) -> tuple[float, ...]:
        try:
            values = tuple(float(value.strip()) for value in response.split(","))
        except ValueError as error:
            raise EzoHumProtocolError(
                f"EZO-HUM response is not numeric: {response!r}"
            ) from error
        if not values or any(not isfinite(value) for value in values):
            raise EzoHumProtocolError(f"EZO-HUM response is not finite: {response!r}")
        return values
