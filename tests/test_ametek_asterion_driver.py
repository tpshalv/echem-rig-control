import pytest

from rig_control.devices.ametek_asterion.driver import (
    HARDWARE_LIMITS_BY_MODEL,
    AmetekAsterion,
    HardwareLimits,
)
from rig_control.devices.ametek_asterion.protocol import (
    AmetekAsterionProtocol as Protocol,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.models import DeviceStatus
from test_keithley_2260b_driver import FakeScpiTransport


def make_supply(
    *,
    voltage="0",
    current="0",
    output="OFF",
    identity="AMETEK,ASTERION DC,12345,01.00",
    limits=None,
    transport=None,
):
    transport = transport or FakeScpiTransport()
    for command, response in (
        (Protocol.IDENTIFY_QUERY, identity),
        (Protocol.VOLTAGE_SETPOINT_QUERY, voltage),
        (Protocol.CURRENT_LIMIT_QUERY, current),
        (Protocol.OUTPUT_STATE_QUERY, output),
    ):
        transport.queue_response(command, response)
    supply = AmetekAsterion(
        "supply",
        limits or PowerSupplyLimits(100, 100, 10000),
        transport,
        read_retry_delay_seconds=0,
    )
    return supply, transport


def test_connect_reads_actual_state_without_config_writes():
    supply, transport = make_supply(voltage="12", current="3", output="ON")

    supply.connect()

    assert supply.status is DeviceStatus.READY
    assert supply.identity.model == "ASTERION DC"
    assert supply.voltage_setpoint == 12
    assert supply.current_limit == 3
    assert supply.output_enabled
    assert supply.hardware_limits is None
    assert transport.queries == [
        "*IDN?",
        "SOUR:VOLT?",
        "SOUR:CURR?",
        "OUTP:STAT?",
    ]
    assert transport.writes == []


@pytest.mark.parametrize("identity", [
    "AMETEK,AST 600-5,12345,01.00",
    "Sorensen,ASTERION,12345,01.00",
    "AMETEK Programmable Power,ASTERION DC,12345,01.00",
])
def test_asterion_identity_variants_are_accepted(identity):
    supply, _ = make_supply(identity=identity)

    supply.connect()

    assert supply.status is DeviceStatus.READY


def test_wrong_identity_is_rejected_without_writes():
    supply, transport = make_supply(identity="AMETEK,ELGAR,12345,01.00")

    with pytest.raises(RuntimeError, match="not an AMETEK Sorensen Asterion"):
        supply.connect()

    assert not transport.is_open
    assert transport.writes == []
    assert supply.status is DeviceStatus.DISCONNECTED


@pytest.mark.parametrize("voltage,current,limits,message", [
    ("21", "1", PowerSupplyLimits(20, 6, 120), "configured maximum"),
    ("1", "4", PowerSupplyLimits(32, 3, 96), "configured maximum"),
    ("20", "3", PowerSupplyLimits(32, 6, 50), "Power.*configured maximum"),
    ("nan", "0", PowerSupplyLimits(32, 6, 192), "non-finite"),
])
def test_connect_rejects_settings_that_exceed_rig_limits(
    voltage,
    current,
    limits,
    message,
):
    supply, transport = make_supply(voltage=voltage, current=current, limits=limits)

    with pytest.raises(ValueError, match=message):
        supply.connect()

    assert not transport.is_open
    assert transport.writes == []


def test_model_hardware_limits_are_enforced_when_mapping_is_known():
    HARDWARE_LIMITS_BY_MODEL["ASTERION DC"] = HardwareLimits(10, 2, 20)
    try:
        supply, transport = make_supply(voltage="11", current="1")

        with pytest.raises(ValueError, match="hardware maximum"):
            supply.connect()

        assert not transport.is_open
    finally:
        del HARDWARE_LIMITS_BY_MODEL["ASTERION DC"]


@pytest.mark.parametrize("method,value,limits,message", [
    ("set_voltage", 21, PowerSupplyLimits(20, 6, 120), "configured maximum"),
    ("set_current_limit", 4, PowerSupplyLimits(32, 3, 96), "configured maximum"),
    ("set_voltage", 26, PowerSupplyLimits(32, 6, 50), "Power.*configured maximum"),
    ("set_current_limit", 6, PowerSupplyLimits(32, 6, 50), "Power.*configured maximum"),
    ("set_voltage", -1, PowerSupplyLimits(32, 6, 192), "negative"),
    ("set_current_limit", -1, PowerSupplyLimits(32, 6, 192), "negative"),
    ("set_voltage", float("nan"), PowerSupplyLimits(32, 6, 192), "finite"),
    ("set_current_limit", float("inf"), PowerSupplyLimits(32, 6, 192), "finite"),
])
def test_invalid_setpoints_do_not_write_or_change_cache(method, value, limits, message):
    supply, transport = make_supply(voltage="10", current="2", limits=limits)
    supply.connect()
    writes = transport.writes.copy()

    with pytest.raises(ValueError, match=message):
        getattr(supply, method)(value)

    assert transport.writes == writes
    assert supply.voltage_setpoint == 10
    assert supply.current_limit == 2


def test_setpoints_output_and_safe_shutdown_commands():
    supply, transport = make_supply()

    supply.connect()
    supply.set_voltage(25)
    supply.set_current_limit(2)
    supply.set_output_enabled(True)
    supply.disconnect()

    assert transport.writes == [
        "SOUR:VOLT 25",
        "SOUR:CURR 2",
        "OUTP:STAT ON",
        "OUTP:STAT OFF",
        "SOUR:VOLT 0",
        "SOUR:CURR 0",
    ]
    assert supply.status is DeviceStatus.DISCONNECTED


@pytest.mark.parametrize("method,query,unit", [
    ("measure_voltage", Protocol.MEASURE_VOLTAGE_QUERY, "V"),
    ("measure_current", Protocol.MEASURE_CURRENT_QUERY, "A"),
])
def test_measurements_keep_units_and_retry_bad_responses(method, query, unit):
    supply, transport = make_supply(output="ON")
    supply.connect()
    transport.queue_response(query, "bad")
    transport.queue_response(query, "1.25")

    result = getattr(supply, method)()

    assert result.value == 1.25
    assert result.unit == unit
    assert transport.queries.count(query) == 2


def test_disconnected_operations_are_rejected():
    supply, transport = make_supply()

    for action in (
        lambda: supply.set_voltage(1),
        lambda: supply.set_current_limit(1),
        lambda: supply.set_output_enabled(True),
        supply.measure_voltage,
        supply.measure_current,
    ):
        with pytest.raises(RuntimeError, match="not ready"):
            action()

    assert transport.writes == []
