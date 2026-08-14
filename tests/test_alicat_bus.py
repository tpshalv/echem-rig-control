import pytest

from rig_control.devices.alicat.bus import AlicatBus
from rig_control.transports.simulated_serial_text import (
    SimulatedSerialTextTransport,
)


class FailingOpenTransport(SimulatedSerialTextTransport):
    def open(self) -> None:
        raise OSError("simulated COM port failure")


def make_bus(
    transport: SimulatedSerialTextTransport | None = None,
) -> tuple[AlicatBus, SimulatedSerialTextTransport]:
    selected_transport = (
        transport
        if transport is not None
        else SimulatedSerialTextTransport()
    )

    bus = AlicatBus(
        bus_id="main_alicat_bus",
        transport=selected_transport,
    )

    return bus, selected_transport


@pytest.mark.parametrize("bus_id", ["", "   "])
def test_empty_bus_id_is_rejected(bus_id: str) -> None:
    transport = SimulatedSerialTextTransport()

    with pytest.raises(ValueError, match="cannot be empty"):
        AlicatBus(
            bus_id=bus_id,
            transport=transport,
        )


def test_bus_starts_disconnected() -> None:
    bus, transport = make_bus()

    assert bus.bus_id == "main_alicat_bus"
    assert bus.is_connected is False
    assert transport.is_open is False


def test_connect_opens_shared_transport() -> None:
    bus, transport = make_bus()

    bus.connect()

    assert bus.is_connected is True
    assert transport.is_open is True


def test_second_connect_attempt_is_rejected() -> None:
    bus, _ = make_bus()
    bus.connect()

    with pytest.raises(RuntimeError, match="already connected"):
        bus.connect()


def test_disconnect_closes_shared_transport() -> None:
    bus, transport = make_bus()
    bus.connect()

    bus.disconnect()

    assert bus.is_connected is False
    assert transport.is_open is False


def test_request_uses_shared_transport() -> None:
    bus, transport = make_bus()
    transport.queue_response("A", "A 0.0")
    bus.connect()

    response = bus.request("A")

    assert response == "A 0.0"
    assert transport.requests == ("A",)


def test_multiple_devices_can_share_request_sequence() -> None:
    bus, transport = make_bus()
    transport.queue_response("A", "A 10.0")
    transport.queue_response("B", "B 20.0")
    bus.connect()

    first_response = bus.request("A")
    second_response = bus.request("B")

    assert first_response == "A 10.0"
    assert second_response == "B 20.0"
    assert transport.requests == ("A", "B")


def test_request_requires_connected_bus() -> None:
    bus, _ = make_bus()

    with pytest.raises(RuntimeError, match="is not connected"):
        bus.request("A")


def test_request_error_contains_bus_and_request_details() -> None:
    bus, transport = make_bus()
    transport.queue_error(
        "A",
        TimeoutError("simulated timeout"),
    )
    bus.connect()

    with pytest.raises(
        RuntimeError,
        match="main_alicat_bus",
    ) as captured_error:
        bus.request("A")

    message = str(captured_error.value)

    assert "'A'" in message
    assert "TimeoutError" in message
    assert "simulated timeout" in message
    assert isinstance(
        captured_error.value.__cause__,
        TimeoutError,
    )


def test_connection_error_contains_original_details() -> None:
    transport = FailingOpenTransport()
    bus, _ = make_bus(transport)

    with pytest.raises(
        ConnectionError,
        match="main_alicat_bus",
    ) as captured_error:
        bus.connect()

    message = str(captured_error.value)

    assert "OSError" in message
    assert "simulated COM port failure" in message
    assert isinstance(
        captured_error.value.__cause__,
        OSError,
    )
    assert bus.is_connected is False