"""SCPI for AMETEK Sorensen Asterion DC Series supplies.

Verified against the Asterion DC Series Programming Manual command reference:
common interface/*IDN, source voltage/current, measure voltage/current, and
output state subsystems.
"""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class AmetekAsterionIdentity:
    """Identification returned by the power supply."""

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str


class AmetekAsterionProtocol:
    """Build and parse SCPI messages for Asterion DC supplies."""

    IDENTIFY_QUERY = "*IDN?"
    VOLTAGE_SETPOINT_QUERY = "SOUR:VOLT?"
    CURRENT_LIMIT_QUERY = "SOUR:CURR?"
    MEASURE_VOLTAGE_QUERY = "MEAS:VOLT?"
    MEASURE_CURRENT_QUERY = "MEAS:CURR?"
    OUTPUT_STATE_QUERY = "OUTP:STAT?"

    @staticmethod
    def set_voltage(voltage: float) -> str:
        value = AmetekAsterionProtocol._format_number(voltage)
        return f"SOUR:VOLT {value}"

    @staticmethod
    def set_current_limit(current: float) -> str:
        value = AmetekAsterionProtocol._format_number(current)
        return f"SOUR:CURR {value}"

    @staticmethod
    def set_output_enabled(enabled: bool) -> str:
        if not isinstance(enabled, bool):
            raise TypeError("Output enabled state must be a Boolean")

        return f"OUTP:STAT {'ON' if enabled else 'OFF'}"

    @staticmethod
    def parse_identity(response: str) -> AmetekAsterionIdentity:
        fields = [field.strip() for field in response.strip().split(",")]

        if len(fields) < 3 or any(not field for field in fields[:3]):
            raise ValueError(
                "Invalid Asterion identity response: expected at least "
                "manufacturer, model, and serial number fields"
            )

        return AmetekAsterionIdentity(
            manufacturer=fields[0],
            model=fields[1],
            serial_number=fields[2],
            firmware_version=",".join(fields[3:]).strip(),
        )

    @staticmethod
    def parse_number(response: str) -> float:
        text = response.strip()

        try:
            value = float(text)
        except ValueError as error:
            raise ValueError(
                f"Invalid numerical response from Asterion: {text!r}"
            ) from error

        if not isfinite(value):
            raise ValueError(
                f"Asterion returned a non-finite value: {text!r}"
            )

        return value

    @staticmethod
    def parse_boolean(response: str) -> bool:
        value = response.strip().upper()

        if value in {"1", "ON"}:
            return True

        if value in {"0", "OFF"}:
            return False

        raise ValueError(
            f"Invalid Boolean response from Asterion: {response.strip()!r}"
        )

    @staticmethod
    def _format_number(value: float) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("SCPI numerical value must be an int or float")

        numeric_value = float(value)

        if not isfinite(numeric_value):
            raise ValueError("SCPI numerical value must be finite")

        return format(numeric_value, ".12g")
