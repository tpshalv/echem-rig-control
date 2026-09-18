import pytest

from rig_control.devices.ametek_asterion.protocol import (
    AmetekAsterionProtocol as Protocol,
)


def test_source_measurement_and_output_commands():
    assert Protocol.IDENTIFY_QUERY == "*IDN?"
    assert Protocol.VOLTAGE_SETPOINT_QUERY == "SOUR:VOLT?"
    assert Protocol.CURRENT_LIMIT_QUERY == "SOUR:CURR?"
    assert Protocol.MEASURE_VOLTAGE_QUERY == "MEAS:VOLT?"
    assert Protocol.MEASURE_CURRENT_QUERY == "MEAS:CURR?"
    assert Protocol.OUTPUT_STATE_QUERY == "OUTP:STAT?"
    assert Protocol.set_voltage(12.5) == "SOUR:VOLT 12.5"
    assert Protocol.set_current_limit(6) == "SOUR:CURR 6"
    assert Protocol.set_output_enabled(True) == "OUTP:STAT ON"
    assert Protocol.set_output_enabled(False) == "OUTP:STAT OFF"


def test_parse_identity_accepts_manual_fields():
    identity = Protocol.parse_identity(
        "AMETEK,AST 600-5,12345,FW 1.2.3"
    )

    assert identity.manufacturer == "AMETEK"
    assert identity.model == "AST 600-5"
    assert identity.serial_number == "12345"
    assert identity.firmware_version == "FW 1.2.3"


def test_parse_identity_preserves_extra_firmware_commas():
    identity = Protocol.parse_identity(
        "AMETEK Programmable Power,ASTERION DC,ABC,SW,1.2"
    )

    assert identity.firmware_version == "SW,1.2"


@pytest.mark.parametrize("response,expected", [
    ("ON", True),
    ("1", True),
    ("OFF", False),
    ("0", False),
])
def test_boolean_responses(response, expected):
    assert Protocol.parse_boolean(response) is expected


@pytest.mark.parametrize("value", [1, 0, "ON", None])
def test_output_requires_boolean(value):
    with pytest.raises(TypeError):
        Protocol.set_output_enabled(value)


@pytest.mark.parametrize("response", ["nan", "inf", "1.0A", "1,2", ""])
def test_invalid_numeric_responses_are_rejected(response):
    with pytest.raises(ValueError):
        Protocol.parse_number(response)
