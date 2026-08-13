import pytest

from rig_control.transports.simulated import SimulatedTransport


def test_simulated_transport_starts_disconnected() -> None:
    transport = SimulatedTransport()

    assert transport.is_connected is False


def test_simulated_transport_can_connect_and_disconnect() -> None:
    transport = SimulatedTransport()

    transport.connect()
    assert transport.is_connected is True

    transport.disconnect()
    assert transport.is_connected is False


def test_simulated_transport_records_sent_messages() -> None:
    transport = SimulatedTransport()
    transport.connect()

    transport.send("STATUS")
    transport.send("READ temperature")

    assert transport.sent_messages == ("STATUS", "READ temperature")


def test_simulated_transport_receives_queued_messages_in_order() -> None:
    transport = SimulatedTransport()
    transport.queue_incoming("READY")
    transport.queue_incoming("25.4 degC")
    transport.connect()

    assert transport.receive() == "READY"
    assert transport.receive() == "25.4 degC"


def test_simulated_transport_rejects_use_while_disconnected() -> None:
    transport = SimulatedTransport()

    with pytest.raises(RuntimeError, match="not connected"):
        transport.send("STATUS")

    with pytest.raises(RuntimeError, match="not connected"):
        transport.receive()


def test_simulated_transport_reports_when_no_message_is_available() -> None:
    transport = SimulatedTransport()
    transport.connect()

    with pytest.raises(RuntimeError, match="No message"):
        transport.receive()