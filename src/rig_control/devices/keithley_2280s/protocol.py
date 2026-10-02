"""SCPI verified against Tektronix manual 077085503 (March 2019).

Source levels: 7-77; output: 7-59; fetch/format: 7-13 to 7-15;
concurrent readings: 3-13; measurement function: 7-60; triggering: 7-20,
7-51, 7-151; integration time: 7-66.
Application Note 3281, p. 4, maps READ,SOUR to current,voltage.
https://download.tek.com/manual/077085503_2280_Ref_Mar_20191.pdf
https://download.tek.com/document/2280S%20Low%20Current%20AppNote.pdf
"""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class KeithleyIdentity:
    """Identification returned by the power supply."""

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str


class Keithley2280SProtocol:
    """Build and parse SCPI messages for the Keithley 2280S-32-6."""

    IDENTIFY_QUERY = "*IDN?"
    VOLTAGE_SETPOINT_QUERY = "SOUR:VOLT:LEV:IMM:AMPL?"
    CURRENT_LIMIT_QUERY = "SOUR:CURR:LEV:IMM:AMPL?"
    FETCH_QUERY = "FETC?"
    OUTPUT_STATE_QUERY = "OUTP:STAT?"
    CONCURRENT_MEASUREMENT = 'SENS:FUNC "CONC"'
    MEASUREMENT_FORMAT = 'FORM:ELEM "READ,SOUR,MODE"'
    IMMEDIATE_ARM = "ARM:SOUR IMM"
    IMMEDIATE_TRIGGER = "TRIG:SOUR IMM"
    CONTINUOUS_MEASUREMENT = "INIT:CONT ON"
    DISABLE_OUTPUT_DELAY = "OUTP:DEL:STAT OFF"

    @staticmethod
    def set_measurement_nplc(nplc: float) -> str:
        value = Keithley2280SProtocol._format_number(nplc)
        # Common documented range for both 50 Hz and 60 Hz mains.
        if not 0.002 <= nplc <= 12:
            raise ValueError("Measurement NPLC must be between 0.002 and 12")
        return f"SENS:CONC:NPLC {value}"

    @staticmethod
    def set_voltage(voltage: float) -> str:
        value = Keithley2280SProtocol._format_number(voltage)
        return f"SOUR:VOLT:LEV:IMM:AMPL {value}"

    @staticmethod
    def set_current_limit(current: float) -> str:
        value = Keithley2280SProtocol._format_number(current)
        return f"SOUR:CURR:LEV:IMM:AMPL {value}"

    @staticmethod
    def set_output_enabled(enabled: bool) -> str:
        if not isinstance(enabled, bool):
            raise TypeError("Output enabled state must be a Boolean")
        return f"OUTP:STAT {'ON' if enabled else 'OFF'}"

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
    def parse_measurements(response: str) -> tuple[float, float, str]:
        """Return current (A), voltage (V), and reported CC/CV/OFF state."""
        fields = response.strip().split(",")
        if len(fields) != 3:
            raise ValueError(
                "Invalid Keithley concurrent response: expected three "
                f"comma-separated fields (current, voltage, mode), got {response!r}"
            )
        mode = fields[2].strip().upper()
        if mode not in {"CC", "CV", "OFF"}:
            raise ValueError(f"Invalid Keithley output mode: {mode!r}")
        return (
            Keithley2280SProtocol.parse_number(fields[0]),
            Keithley2280SProtocol.parse_number(fields[1]),
            mode,
        )

    @staticmethod
    def parse_boolean(response: str) -> bool:
        # The output state additionally supports DISable (2).
        if response.strip().upper() in {"2", "DIS", "DISABLE"}:
            return False

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
