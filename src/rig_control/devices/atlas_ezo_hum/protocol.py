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
        response = self._request_data("i")
        parts = response.split(",")
        if len(parts) != 3 or parts[0].lower() != "?i" or parts[1].upper() != "HUM":
            raise EzoHumProtocolError(f"Unexpected EZO-HUM identity response: {response!r}")
        if not parts[2].strip():
            raise EzoHumProtocolError("EZO-HUM identity has no firmware version")
        return EzoHumIdentity(parts[2].strip())

    def read(self, *, include_dew_point: bool) -> EzoHumReading:
        response = self._request_data("R")
        # Real probes (firmware 1.01) label the dew point: "34.96,24.45,Dew,8.04".
        fields = [field for field in response.split(",") if field.strip().lower() != "dew"]
        values = self._numbers(",".join(fields))
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
        if allow_unsolicited_reading:
            # A probe may have sent a final continuous reading just before C,0
            # was received. Its ack follows that reading, so read on to it.
            for _ in range(3):
                if response == "*OK":
                    break
                self._numbers(response)
                response = self._read_line()
        if response == "*OK":
            return
        raise EzoHumProtocolError(
            f"EZO-HUM command {command!r} was not acknowledged: {response!r}"
        )

    def _request_data(self, command: str) -> str:
        # With response codes on (the factory default) a data reply is followed
        # by its own "*OK" line. Left queued, that line would be taken as the
        # reply to the next command.
        response = self._request(command)
        ack = self._read_line()
        if ack != "*OK":
            raise EzoHumProtocolError(
                f"EZO-HUM reply to {command!r} was not followed by *OK: {ack!r}"
            )
        return response

    def _read_line(self) -> str:
        response = self.transport.read_line().strip()
        if response.startswith("*ER"):
            raise EzoHumProtocolError(f"EZO-HUM reported an error: {response}")
        return response

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
