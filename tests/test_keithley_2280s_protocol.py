import pytest

from rig_control.devices.keithley_2280s.protocol import Keithley2280SProtocol as Protocol


def test_shared_source_commands_and_parsers():
    assert Protocol.set_voltage(12.5) == "SOUR:VOLT:LEV:IMM:AMPL 12.5"
    assert Protocol.set_current_limit(6) == "SOUR:CURR:LEV:IMM:AMPL 6"
    assert Protocol.parse_number(" +1.25E-2\n") == 0.0125
    assert Protocol.parse_identity(
        "KEITHLEY INSTRUMENTS LLC,MODEL 2280S-32-6,01234567,01.00"
    ).model == "MODEL 2280S-32-6"
    assert Protocol.FETCH_QUERY == "FETC?"
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


def test_concurrent_response_parses_current_before_voltage():
    assert Protocol.parse_measurements(" +5.000000E-02, +5.050000E-01, CC\r\n") == (
        0.05, 0.505, "CC",
    )


@pytest.mark.parametrize("response", [
    "+6.477145E-05", "", "0.05,0.505,1", "0.05A,0.505V",
    "nan,0.505", "0.05,inf", ",0.505", "0.05,",
    "nan,0.505,CC", "0.05,inf,CV", "0.05,0.505,UNKNOWN", "0.05,0.505,CC,1",
])
def test_concurrent_response_rejects_missing_extra_or_invalid_fields(response):
    with pytest.raises(ValueError):
        Protocol.parse_measurements(response)


@pytest.mark.parametrize("value", [0, -1, 0.001, 12.01, float("nan"), float("inf"), True, "0.5"])
def test_integration_rejects_invalid_values(value):
    with pytest.raises((ValueError, TypeError)):
        Protocol.set_measurement_nplc(value)


def test_integration_command_targets_concurrent_measurement_only():
    assert Protocol.set_measurement_nplc(0.5) == "SENS:CONC:NPLC 0.5"
    assert Protocol.set_measurement_nplc(0.002) == "SENS:CONC:NPLC 0.002"
    assert Protocol.set_measurement_nplc(12) == "SENS:CONC:NPLC 12"


@pytest.mark.parametrize("mode", ["CC", "CV", "OFF"])
def test_reported_regulation_modes(mode):
    assert Protocol.parse_measurements(f"0,0,{mode}")[2] == mode
