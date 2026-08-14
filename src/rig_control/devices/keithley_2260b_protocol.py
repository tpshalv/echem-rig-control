from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class KeithleyIdentity:
    """Identification returned by the power supply."""

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str


class Keithley2260BProtocol:
    """Build and parse SCPI messages for the Keithley 2260B."""

    SOCKET_PORT = 2268

    IDENTIFY_QUERY = "*IDN?"
    VOLTAGE_SETPOINT_QUERY = "SOUR:VOLT:LEV:IMM:AMPL?"
    CURRENT_LIMIT_QUERY = "SOUR:CURR:LEV:IMM:AMPL?"
    MEASURE_VOLTAGE_QUERY = "MEAS:VOLT:DC?"
    MEASURE_CURRENT_QUERY = "MEAS:CURR:DC?"
    MEASURE_POWER_QUERY = "MEAS:POW:DC?"
    OUTPUT_STATE_QUERY = "OUTP:STAT:IMM?"
    PROTECTION_TRIPPED_QUERY = "OUTP:PROT:TRIP?"

    @staticmethod
    def set_voltage(voltage: float) -> str:
        value = Keithley2260BProtocol._format_number(voltage)
        return f"SOUR:VOLT:LEV:IMM:AMPL {value}"

    @staticmethod
    def set_current_limit(current: float) -> str:
        value = Keithley2260BProtocol._format_number(current)
        return f"SOUR:CURR:LEV:IMM:AMPL {value}"

    @staticmethod
    def set_output_enabled(enabled: bool) -> str:
        if not isinstance(enabled, bool):
            raise TypeError("Output enabled state must be a Boolean")

        state = "ON" if enabled else "OFF"
        return f"OUTP:STAT:IMM {state}"

    @staticmethod
    def parse_identity(response: str) -> KeithleyIdentity:
        fields = [field.strip() for field in response.strip().split(",")]

        if len(fields) != 4 or any(not field for field in fields):
            raise ValueError(
                "Invalid Keithley identity response: expected four "
                "non-empty comma-separated fields"
            )

        return KeithleyIdentity(
            manufacturer=fields[0],
            model=fields[1],
            serial_number=fields[2],
            firmware_version=fields[3],
        )

    @staticmethod
    def parse_number(response: str) -> float:
        text = response.strip()

        try:
            value = float(text)
        except ValueError as error:
            raise ValueError(
                f"Invalid numerical response from Keithley: {text!r}"
            ) from error

        if not isfinite(value):
            raise ValueError(
                f"Keithley returned a non-finite value: {text!r}"
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
            f"Invalid Boolean response from Keithley: {response.strip()!r}"
        )

    @staticmethod
    def _format_number(value: float) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("SCPI numerical value must be an int or float")

        numeric_value = float(value)

        if not isfinite(numeric_value):
            raise ValueError("SCPI numerical value must be finite")

        return format(numeric_value, ".12g")