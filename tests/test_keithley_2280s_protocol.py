import pytest

from rig_control.devices.keithley_2280s.protocol import Keithley2280SProtocol as Protocol


def test_shared_source_commands_and_parsers():
    assert Protocol.set_voltage(12.5) == "SOUR:VOLT:LEV:IMM:AMPL 12.5"
    assert Protocol.set_current_limit(6) == "SOUR:CURR:LEV:IMM:AMPL 6"
    assert Protocol.parse_number(" +1.25E-2\n") == 0.0125
    assert Protocol.parse_identity(
        "KEITHLEY INSTRUMENTS LLC,MODEL 2280S-32-6,01234567,01.00"
    ).model == "MODEL 2280S-32-6"
    assert Protocol.MEASURE_VOLTAGE_QUERY == "MEAS:VOLT:DC?"
    assert Protocol.MEASURE_CURRENT_QUERY == "MEAS:CURR:DC?"
    assert not hasattr(Protocol, "MEASURE_POWER_QUERY")  # Not in the 2280S manual.


@pytest.mark.parametrize("enabled,command", [(True, "OUTP:STAT ON"), (False, "OUTP:STAT OFF")])
def test_output_commands(enabled, command):
    assert Protocol.set_output_enabled(enabled) == command
    assert Protocol.OUTPUT_STATE_QUERY == "OUTP:STAT?"


@pytest.mark.parametrize("value", [1, 0, "ON", None])
def test_output_requires_boolean(value):
    with pytest.raises(TypeError):
        Protocol.set_output_enabled(value)


@pytest.mark.parametrize("response,expected", [
    ("ON", True), ("1", True), ("OFF", False), ("0", False),
    ("2", False), ("DIS", False), (" disable\n", False),
])
def test_output_state_parsing(response, expected):
    assert Protocol.parse_boolean(response) is expected


@pytest.mark.parametrize("response", ["nan", "inf", "1.0A", "1,2", ""])
def test_invalid_numeric_responses_are_rejected(response):
    with pytest.raises(ValueError):
        Protocol.parse_number(response)
