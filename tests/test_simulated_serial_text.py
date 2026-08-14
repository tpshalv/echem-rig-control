import pytest

from rig_control.transports.simulated_serial_text import (
    SimulatedSerialTextTransport,
)


def test_transport_starts_closed() -> None:
    transport = SimulatedSerialTextTransport()

    assert transport.is_open is False
    assert transport.requests == ()


def test_transport_can_open_and_close() -> None:
    transport = SimulatedSerialTextTransport()

    transport.open()
    assert transport.is_open is True

    transport.close()
    assert transport.is_open is False


def test_open_rejects_second_attempt() -> None:
    transport = SimulatedSerialTextTransport()
    transport.open()

    with pytest.raises(RuntimeError, match="already open"):
        transport.open()


def test_queued_response_is_returned() -> None:
    transport = SimulatedSerialTextTransport()
    transport.queue_response("A", "A 0.0")
    transport.open()

    response = transport.request("A")

    assert response == "A 0.0"
    assert transport.requests == ("A",)


def test_multiple_responses_are_returned_in_order() -> None:
    transport = SimulatedSerialTextTransport()
    transport.queue_response("A", "first")
    transport.queue_response("A", "second")
    transport.open()

    assert transport.request("A") == "first"
    assert transport.request("A") == "second"
    assert transport.requests == ("A", "A")


def test_different_requests_have_separate_response_queues() -> None:
    transport = SimulatedSerialTextTransport()
    transport.queue_response("A", "response A")
    transport.queue_response("B", "response B")
    transport.open()

    assert transport.request("B") == "response B"
    assert transport.request("A") == "response A"
    assert transport.requests == ("B", "A")


def test_queued_error_is_raised_and_request_is_recorded() -> None:
    transport = SimulatedSerialTextTransport()
    transport.queue_error(
        "A",
        TimeoutError("simulated serial timeout"),
    )
    transport.open()

    with pytest.raises(
        TimeoutError,
        match="simulated serial timeout",
    ):
        transport.request("A")

    assert transport.requests == ("A",)


def test_missing_response_has_informative_error() -> None:
    transport = SimulatedSerialTextTransport()
    transport.open()

    with pytest.raises(
        RuntimeError,
        match="No simulated serial response queued",
    ):
        transport.request("A")


def test_request_requires_open_transport() -> None:
    transport = SimulatedSerialTextTransport()
    transport.queue_response("A", "response")

    with pytest.raises(RuntimeError, match="is not open"):
        transport.request("A")


@pytest.mark.parametrize("message", ["", "   ", "A\r", "A\n"])
def test_invalid_request_text_is_rejected(message: str) -> None:
    transport = SimulatedSerialTextTransport()
    transport.open()

    with pytest.raises(ValueError):
        transport.request(message)


def test_non_text_response_is_rejected() -> None:
    transport = SimulatedSerialTextTransport()

    with pytest.raises(TypeError, match="response must be text"):
        transport.queue_response(
            "A",
            123,  # type: ignore[arg-type]
        )


def test_non_exception_error_is_rejected() -> None:
    transport = SimulatedSerialTextTransport()

    with pytest.raises(TypeError, match="must be an Exception"):
        transport.queue_error(
            "A",
            "error",  # type: ignore[arg-type]
        )