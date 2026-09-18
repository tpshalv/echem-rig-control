import pytest

from rig_control.devices.keithley_2280s.driver import HARDWARE_LIMITS, Keithley2280S
from rig_control.devices.keithley_2280s.protocol import Keithley2280SProtocol as Protocol
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.models import DeviceStatus
from test_keithley_2260b_driver import FakeScpiTransport


def make_supply(*, voltage="0", current="0", output="OFF", model="MODEL 2280S-32-6",
                limits=None, transport=None):
    transport = transport or FakeScpiTransport()
    for command, response in (
        (Protocol.IDENTIFY_QUERY, f"KEITHLEY INSTRUMENTS LLC,{model},12345,01.00"),
        (Protocol.VOLTAGE_SETPOINT_QUERY, voltage),
        (Protocol.CURRENT_LIMIT_QUERY, current),
        (Protocol.OUTPUT_STATE_QUERY, output),
    ):
        transport.queue_response(command, response)
    supply = Keithley2280S(
        "supply", limits or PowerSupplyLimits(100, 100, 10000), transport,
        read_retry_delay_seconds=0,
    )
    return supply, transport


@pytest.mark.parametrize("model", ["MODEL 2280S-32-6", "2280S-32-6", "model 2280s-32-6"])
def test_connect_reads_actual_state_and_configures_numeric_measurements(model):
    supply, transport = make_supply(voltage="32", current="6", output="ON", model=model)
    supply.connect()
    assert supply.status is DeviceStatus.READY
    assert supply.identity.model == model
    assert supply.voltage_setpoint == 32
    assert supply.current_limit == 6
    assert supply.output_enabled
    assert transport.queries == [
        "*IDN?", "SOUR:VOLT:LEV:IMM:AMPL?", "SOUR:CURR:LEV:IMM:AMPL?", "OUTP:STAT?",
    ]
    assert transport.writes == ['FORM:ELEM "READ"', "OUTP:DEL:STAT OFF"]
    assert supply.hardware_limits is HARDWARE_LIMITS
    assert (HARDWARE_LIMITS.maximum_voltage, HARDWARE_LIMITS.maximum_current,
            HARDWARE_LIMITS.maximum_power) == (32, 6, 192)
    assert supply.limits.maximum_voltage == 100  # Rig limits are not overwritten.


@pytest.mark.parametrize("model", ["2280S-60-3", "2260B-30-108", "2280S-32-60", "OTHER"])
def test_wrong_model_is_rejected_without_writes(model):
    supply, transport = make_supply(model=model)
    with pytest.raises(RuntimeError, match="not a Keithley 2280S-32-6"):
        supply.connect()
    assert not transport.is_open
    assert transport.writes == []
    assert supply.identity is None
    assert supply.status is DeviceStatus.DISCONNECTED


@pytest.mark.parametrize("voltage,current,limits,message", [
    ("32.01", "0", PowerSupplyLimits(100, 100, 10000), "hardware maximum"),
    ("0", "6.1", PowerSupplyLimits(100, 100, 10000), "hardware maximum"),
    ("21", "1", PowerSupplyLimits(20, 6, 120), "configured maximum"),
    ("1", "4", PowerSupplyLimits(32, 3, 96), "configured maximum"),
    ("20", "3", PowerSupplyLimits(32, 6, 50), "Power.*configured maximum"),
    ("nan", "0", PowerSupplyLimits(32, 6, 192), "non-finite"),
])
def test_connect_rejects_unsafe_existing_settings(voltage, current, limits, message):
    supply, transport = make_supply(voltage=voltage, current=current, limits=limits)
    with pytest.raises(ValueError, match=message):
        supply.connect()
    assert not transport.is_open
    assert transport.writes == []
    assert supply.status is DeviceStatus.DISCONNECTED


@pytest.mark.parametrize("method,value,limits,message", [
    ("set_voltage", 32.01, PowerSupplyLimits(100, 100, 10000), "hardware maximum"),
    ("set_current_limit", 6.1, PowerSupplyLimits(100, 100, 10000), "hardware maximum"),
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


def test_boundary_power_is_allowed_and_output_can_be_enabled_and_disabled():
    supply, transport = make_supply(limits=PowerSupplyLimits(32, 6, 50))
    supply.connect()
    supply.set_current_limit(2)
    supply.set_voltage(25)
    supply.set_output_enabled(True)
    assert supply.output_enabled
    supply.set_output_enabled(False)
    assert not supply.output_enabled
    assert transport.writes[-4:] == [
        "SOUR:CURR:LEV:IMM:AMPL 2", "SOUR:VOLT:LEV:IMM:AMPL 25",
        "OUTP:STAT ON", "OUTP:STAT OFF",
    ]


def test_enable_revalidates_cached_operating_point_but_disable_is_always_allowed():
    supply, transport = make_supply()
    supply.connect()
    supply._voltage_setpoint = 32
    supply._current_limit = 7
    writes = transport.writes.copy()
    with pytest.raises(ValueError, match="hardware maximum"):
        supply.set_output_enabled(True)
    assert transport.writes == writes
    supply.set_output_enabled(False)
    assert transport.writes[-1] == "OUTP:STAT OFF"


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


@pytest.mark.parametrize("output", ["OFF", "0", "2", "DISABLE"])
def test_measurements_fail_without_querying_when_output_is_off(output):
    supply, transport = make_supply(output=output)
    supply.connect()
    queries = transport.queries.copy()
    for method in (supply.measure_voltage, supply.measure_current):
        with pytest.raises(RuntimeError, match="require output enabled"):
            method()
    assert transport.queries == queries


def test_disconnect_preserves_output_off_then_zero_setpoints():
    supply, transport = make_supply(voltage="20", current="3", output="ON")
    supply.connect()
    supply.disconnect()
    assert transport.writes[-3:] == [
        "OUTP:STAT OFF", "SOUR:VOLT:LEV:IMM:AMPL 0", "SOUR:CURR:LEV:IMM:AMPL 0",
    ]
    assert not transport.is_open
    assert supply.status is DeviceStatus.DISCONNECTED
    assert supply.identity is None
    assert supply.voltage_setpoint == supply.current_limit == 0
    assert not supply.output_enabled
    supply.disconnect()  # Already disconnected is safe.


class FailingWriteTransport(FakeScpiTransport):
    fail_command = None

    def write(self, command):
        if command == self.fail_command:
            raise OSError("write failed")
        super().write(command)


def test_failed_setpoint_write_does_not_update_cache_and_disconnect_still_closes():
    supply, transport = make_supply(transport=FailingWriteTransport())
    supply.connect()
    transport.fail_command = "SOUR:VOLT:LEV:IMM:AMPL 10"
    with pytest.raises(OSError):
        supply.set_voltage(10)
    assert supply.voltage_setpoint == 0
    transport.fail_command = "OUTP:STAT OFF"
    with pytest.raises(OSError):
        supply.disconnect()
    assert not transport.is_open
    assert supply.status is DeviceStatus.DISCONNECTED


def test_failed_measurement_setup_closes_connection():
    supply, transport = make_supply(transport=FailingWriteTransport())
    transport.fail_command = Protocol.MEASUREMENT_FORMAT
    with pytest.raises(OSError):
        supply.connect()
    assert not transport.is_open
    assert supply.status is DeviceStatus.DISCONNECTED


def test_disconnected_operations_are_rejected():
    supply, transport = make_supply()
    for action in (lambda: supply.set_voltage(1), lambda: supply.set_current_limit(1),
                   lambda: supply.set_output_enabled(True), supply.measure_voltage,
                   supply.measure_current):
        with pytest.raises(RuntimeError, match="not ready"):
            action()
    assert transport.writes == []
