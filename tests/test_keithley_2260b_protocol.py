import pytest

from rig_control.devices.keithley_2260b.protocol import (
    Keithley2260BProtocol,
)


def test_protocol_uses_documented_socket_port() -> None:
    assert Keithley2260BProtocol.SOCKET_PORT == 2268


def test_protocol_defines_identification_query() -> None:
    assert Keithley2260BProtocol.IDENTIFY_QUERY == "*IDN?"


def test_protocol_defines_measurement_queries() -> None:
    assert Keithley2260BProtocol.MEASURE_VOLTAGE_QUERY == "MEAS:VOLT:DC?"
    assert Keithley2260BProtocol.MEASURE_CURRENT_QUERY == "MEAS:CURR:DC?"
    assert Keithley2260BProtocol.MEASURE_POWER_QUERY == "MEAS:POW:DC?"


def test_protocol_builds_voltage_command() -> None:
    command = Keithley2260BProtocol.set_voltage(10.5)

    assert command == "SOUR:VOLT:LEV:IMM:AMPL 10.5"


def test_protocol_builds_current_command() -> None:
    command = Keithley2260BProtocol.set_current_limit(42.25)

    assert command == "SOUR:CURR:LEV:IMM:AMPL 42.25"


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (True, "OUTP:STAT:IMM ON"),
        (False, "OUTP:STAT:IMM OFF"),
    ],
)
def test_protocol_builds_output_command(
    enabled: bool,
    expected: str,
) -> None:
    assert Keithley2260BProtocol.set_output_enabled(enabled) == expected


def test_output_command_rejects_non_boolean() -> None:
    with pytest.raises(TypeError, match="Boolean"):
        Keithley2260BProtocol.set_output_enabled(1)


def test_protocol_parses_identity() -> None:
    identity = Keithley2260BProtocol.parse_identity(
        "KEITHLEY INSTRUMENTS,2260B-30-108,TW123456,01.00\n"
    )

    assert identity.manufacturer == "KEITHLEY INSTRUMENTS"
    assert identity.model == "2260B-30-108"
    assert identity.serial_number == "TW123456"
    assert identity.firmware_version == "01.00"


@pytest.mark.parametrize(
    "response",
    [
        "",
        "KEITHLEY,2260B",
        "KEITHLEY,2260B,SERIAL",
        "KEITHLEY,2260B,SERIAL,FIRMWARE,EXTRA",
        "KEITHLEY,,SERIAL,FIRMWARE",
    ],
)
def test_protocol_rejects_invalid_identity(response: str) -> None:
    with pytest.raises(ValueError, match="identity response"):
        Keithley2260BProtocol.parse_identity(response)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("10.5", 10.5),
        ("+1.080000E+02", 108.0),
        (" 0.000 \n", 0.0),
    ],
)
def test_protocol_parses_numerical_response(
    response: str,
    expected: float,
) -> None:
    assert Keithley2260BProtocol.parse_number(response) == expected


@pytest.mark.parametrize(
    "response",
    [
        "",
        "not-a-number",
        "nan",
        "inf",
        "-inf",
    ],
)
def test_protocol_rejects_invalid_numerical_response(
    response: str,
) -> None:
    with pytest.raises(ValueError):
        Keithley2260BProtocol.parse_number(response)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("1", True),
        ("ON", True),
        ("on\n", True),
        ("0", False),
        ("OFF", False),
        (" off ", False),
    ],
)
def test_protocol_parses_boolean_response(
    response: str,
    expected: bool,
) -> None:
    assert Keithley2260BProtocol.parse_boolean(response) is expected


def test_protocol_rejects_invalid_boolean_response() -> None:
    with pytest.raises(ValueError, match="Boolean response"):
        Keithley2260BProtocol.parse_boolean("maybe")


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_protocol_rejects_non_finite_commands(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        Keithley2260BProtocol.set_voltage(value)