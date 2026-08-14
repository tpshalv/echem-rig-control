import inspect

import pytest

from rig_control.transports.scpi import ScpiTransport
from rig_control.transports.simulated_scpi import SimulatedScpiTransport


def test_scpi_transport_is_abstract() -> None:
    assert inspect.isabstract(ScpiTransport)


def test_scpi_transport_defines_expected_contract() -> None:
    assert ScpiTransport.__abstractmethods__ == {
        "is_open",
        "open",
        "close",
        "write",
        "query",
    }


def test_simulated_scpi_transport_starts_closed() -> None:
    transport = SimulatedScpiTransport()

    assert transport.is_open is False


def test_simulated_scpi_transport_can_open_and_close() -> None:
    transport = SimulatedScpiTransport()

    transport.open()
    assert transport.is_open is True

    transport.close()
    assert transport.is_open is False


def test_simulated_scpi_transport_records_writes() -> None:
    transport = SimulatedScpiTransport()
    transport.open()

    transport.write("OUTP:STAT:IMM OFF")
    transport.write("SOUR:VOLT:LEV:IMM:AMPL 10")

    assert transport.written_commands == (
        "OUTP:STAT:IMM OFF",
        "SOUR:VOLT:LEV:IMM:AMPL 10",
    )


def test_simulated_scpi_transport_returns_queued_responses() -> None:
    transport = SimulatedScpiTransport()
    transport.queue_response("*IDN?", "KEITHLEY,2260B,SERIAL,1.0")
    transport.open()

    response = transport.query("*IDN?")

    assert response == "KEITHLEY,2260B,SERIAL,1.0"
    assert transport.queried_commands == ("*IDN?",)


def test_simulated_responses_are_returned_in_order() -> None:
    transport = SimulatedScpiTransport()
    transport.queue_response("MEAS:VOLT:DC?", "9.8")
    transport.queue_response("MEAS:VOLT:DC?", "9.9")
    transport.open()

    assert transport.query("MEAS:VOLT:DC?") == "9.8"
    assert transport.query("MEAS:VOLT:DC?") == "9.9"


def test_simulated_scpi_transport_requires_open_connection() -> None:
    transport = SimulatedScpiTransport()

    with pytest.raises(RuntimeError, match="not open"):
        transport.write("OUTP:STAT:IMM OFF")

    with pytest.raises(RuntimeError, match="not open"):
        transport.query("*IDN?")


def test_query_without_queued_response_is_reported_clearly() -> None:
    transport = SimulatedScpiTransport()
    transport.open()

    with pytest.raises(RuntimeError, match="No simulated response"):
        transport.query("*IDN?")