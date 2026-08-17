from collections import defaultdict, deque

import pytest

from rig_control.devices.keithley_2260b.driver import Keithley2260B
from rig_control.devices.keithley_2260b.protocol import (
    Keithley2260BProtocol,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.models import DeviceStatus
from rig_control.transports.scpi import ScpiTransport


class FakeScpiTransport(ScpiTransport):
    """Small controllable SCPI transport used by these driver tests."""

    def __init__(self) -> None:
        self._is_open = False
        self.responses: dict[str, deque[str]] = defaultdict(deque)
        self.writes: list[str] = []
        self.queries: list[str] = []

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def write(self, command: str) -> None:
        if not self.is_open:
            raise RuntimeError("SCPI transport is not open")

        self.writes.append(command)

    def query(self, command: str) -> str:
        if not self.is_open:
            raise RuntimeError("SCPI transport is not open")

        self.queries.append(command)

        if not self.responses[command]:
            raise RuntimeError(
                f"No simulated response queued for SCPI query: {command}"
            )

        return self.responses[command].popleft()

    def queue_response(self, command: str, response: str) -> None:
        self.responses[command].append(response)


def make_supply(
    transport: FakeScpiTransport,
) -> Keithley2260B:
    return Keithley2260B(
        device_id="main_power_supply",
        limits=PowerSupplyLimits(
            maximum_voltage=30.0,
            maximum_current=108.0,
            maximum_power=1080.0,
        ),
        transport=transport,
    )


def queue_connection_responses(
    transport: FakeScpiTransport,
    *,
    identity: str = "Keithley Instruments,2260B-30-108,1234567,1.00",
    voltage: str = "0",
    current: str = "0",
    output: str = "OFF",
) -> None:
    transport.queue_response(
        Keithley2260BProtocol.IDENTIFY_QUERY,
        identity,
    )
    transport.queue_response(
        Keithley2260BProtocol.VOLTAGE_SETPOINT_QUERY,
        voltage,
    )
    transport.queue_response(
        Keithley2260BProtocol.CURRENT_LIMIT_QUERY,
        current,
    )
    transport.queue_response(
        Keithley2260BProtocol.OUTPUT_STATE_QUERY,
        output,
    )


def test_supply_starts_disconnected() -> None:
    transport = FakeScpiTransport()
    supply = make_supply(transport)

    assert supply.status is DeviceStatus.DISCONNECTED
    assert supply.identity is None
    assert supply.output_enabled is False
    assert transport.is_open is False


def test_connect_reads_identity_and_current_instrument_state() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(
        transport,
        voltage="12.5",
        current="4.2",
        output="ON",
    )
    supply = make_supply(transport)

    supply.connect()

    assert supply.status is DeviceStatus.READY
    assert supply.identity is not None
    assert supply.identity.model == "2260B-30-108"
    assert supply.voltage_setpoint == pytest.approx(12.5)
    assert supply.current_limit == pytest.approx(4.2)
    assert supply.output_enabled is True
    assert transport.is_open is True


def test_connect_rejects_wrong_instrument_model() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(
        transport,
        identity="Example Instruments,OTHER-DEVICE,1234,1.0",
    )
    supply = make_supply(transport)

    with pytest.raises(RuntimeError, match="not a Keithley 2260B"):
        supply.connect()

    assert supply.status is DeviceStatus.DISCONNECTED
    assert supply.identity is None
    assert transport.is_open is False


def test_connect_accepts_independent_voltage_and_current_settings() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(
        transport,
        voltage="30",
        current="40",
    )
    supply = make_supply(transport)

    supply.connect()

    assert supply.status is DeviceStatus.READY
    assert supply.voltage_setpoint == 30.0
    assert supply.current_limit == 40.0
    assert transport.is_open is True


def test_set_voltage_sends_expected_command() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(transport)
    supply = make_supply(transport)
    supply.connect()

    supply.set_voltage(10.5)

    assert supply.voltage_setpoint == pytest.approx(10.5)
    assert transport.writes[-1] == (
        "SOUR:VOLT:LEV:IMM:AMPL 10.5"
    )


def test_set_current_limit_sends_expected_command() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(transport, voltage="10")
    supply = make_supply(transport)
    supply.connect()

    supply.set_current_limit(5.5)

    assert supply.current_limit == pytest.approx(5.5)
    assert transport.writes[-1] == (
        "SOUR:CURR:LEV:IMM:AMPL 5.5"
    )


def test_independent_voltage_and_current_settings_are_sent() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(transport, voltage="30")
    supply = make_supply(transport)
    supply.connect()

    supply.set_current_limit(40.0)

    assert supply.current_limit == 40.0
    assert transport.writes[-1] == (
        "SOUR:CURR:LEV:IMM:AMPL 40"
    )


def test_output_control_sends_expected_commands() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(transport)
    supply = make_supply(transport)
    supply.connect()

    supply.set_output_enabled(True)
    supply.set_output_enabled(False)

    assert transport.writes[-2:] == [
        "OUTP:STAT:IMM ON",
        "OUTP:STAT:IMM OFF",
    ]
    assert supply.output_enabled is False


def test_measurements_are_parsed_with_correct_units() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(transport)
    transport.queue_response(
        Keithley2260BProtocol.MEASURE_VOLTAGE_QUERY,
        "12.34",
    )
    transport.queue_response(
        Keithley2260BProtocol.MEASURE_CURRENT_QUERY,
        "5.67",
    )
    supply = make_supply(transport)
    supply.connect()

    voltage = supply.measure_voltage()
    current = supply.measure_current()

    assert voltage.value == pytest.approx(12.34)
    assert voltage.unit == "V"
    assert current.value == pytest.approx(5.67)
    assert current.unit == "A"


def test_commands_are_rejected_while_disconnected() -> None:
    transport = FakeScpiTransport()
    supply = make_supply(transport)

    with pytest.raises(RuntimeError, match="not ready"):
        supply.set_voltage(5.0)

    with pytest.raises(RuntimeError, match="not ready"):
        supply.set_current_limit(2.0)

    with pytest.raises(RuntimeError, match="not ready"):
        supply.set_output_enabled(True)

    with pytest.raises(RuntimeError, match="not ready"):
        supply.measure_voltage()

    with pytest.raises(RuntimeError, match="not ready"):
        supply.measure_current()


def test_disconnect_applies_safe_state_before_closing_transport() -> None:
    transport = FakeScpiTransport()
    queue_connection_responses(
        transport,
        voltage="10",
        current="5",
        output="ON",
    )
    supply = make_supply(transport)
    supply.connect()

    supply.disconnect()

    assert transport.writes[-3:] == [
        "OUTP:STAT:IMM OFF",
        "SOUR:VOLT:LEV:IMM:AMPL 0",
        "SOUR:CURR:LEV:IMM:AMPL 0",
    ]
    assert transport.is_open is False
    assert supply.status is DeviceStatus.DISCONNECTED
    assert supply.identity is None
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 0.0
    assert supply.output_enabled is False