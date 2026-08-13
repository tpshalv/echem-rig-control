import pytest

from rig_control.controllers.client import (
    ControllerClient,
    ControllerCommandError,
)
from rig_control.protocol import Message, MessageType
from rig_control.transports.simulated import SimulatedTransport


def make_client() -> tuple[SimulatedTransport, ControllerClient]:
    transport = SimulatedTransport()
    transport.connect()
    return transport, ControllerClient(transport)


def queue_response(
    transport: SimulatedTransport,
    *,
    reply_to: str,
    name: str = "ok",
    payload: dict | None = None,
) -> None:
    response_payload = {
        "reply_to": reply_to,
        **(payload or {}),
    }
    response = Message(
        message_type=MessageType.RESPONSE,
        name=name,
        payload=response_payload,
    )
    transport.queue_incoming(response.to_json())


def sent_request(transport: SimulatedTransport) -> Message:
    return Message.from_json(transport.sent_messages[-1])


def test_client_sends_heartbeat_command() -> None:
    transport, client = make_client()

    # The identifier is generated during send, so prepare the reply afterward
    # by intercepting the expected request using a temporary queued exchange.
    request = Message(MessageType.COMMAND, "heartbeat")
    response = Message(
        MessageType.RESPONSE,
        "ok",
        {"reply_to": request.message_id},
    )

    transport.queue_incoming(response.to_json())

    with pytest.raises(RuntimeError, match="does not match"):
        client.heartbeat()

    actual = sent_request(transport)
    assert actual.name == "heartbeat"
    assert actual.message_type is MessageType.COMMAND


def test_client_set_output_sends_expected_payload() -> None:
    transport, client = make_client()

    # Replace receive temporarily so the reply can reference the sent request.
    original_receive = transport.receive

    def receive_matching_response() -> str:
        request = sent_request(transport)
        queue_response(transport, reply_to=request.message_id)
        return original_receive()

    transport.receive = receive_matching_response  # type: ignore[method-assign]

    client.set_output("pump", True)

    request = sent_request(transport)
    assert request.name == "set_output"
    assert request.payload == {"name": "pump", "enabled": True}


def test_client_returns_status_payload() -> None:
    transport, client = make_client()
    original_receive = transport.receive

    def receive_status() -> str:
        request = sent_request(transport)
        queue_response(
            transport,
            reply_to=request.message_id,
            payload={
                "outputs": {"pump": False},
                "safe_state_active": True,
            },
        )
        return original_receive()

    transport.receive = receive_status  # type: ignore[method-assign]

    status = client.get_status()

    assert status["outputs"] == {"pump": False}
    assert status["safe_state_active"] is True


def test_client_raises_when_controller_returns_error() -> None:
    transport, client = make_client()
    original_receive = transport.receive

    def receive_error() -> str:
        request = sent_request(transport)
        queue_response(
            transport,
            reply_to=request.message_id,
            name="error",
            payload={"error": "Unknown output: missing"},
        )
        return original_receive()

    transport.receive = receive_error  # type: ignore[method-assign]

    with pytest.raises(ControllerCommandError, match="Unknown output"):
        client.set_output("missing", True)


def test_client_rejects_mismatched_response() -> None:
    transport, client = make_client()
    queue_response(transport, reply_to="different-request")

    with pytest.raises(RuntimeError, match="does not match"):
        client.heartbeat()


def test_client_requires_connected_transport() -> None:
    transport = SimulatedTransport()
    client = ControllerClient(transport)

    with pytest.raises(RuntimeError, match="not connected"):
        client.heartbeat()


def test_client_sends_identify_command() -> None:
    transport, client = make_client()
    original_receive = transport.receive

    def receive_identity() -> str:
        request = sent_request(transport)
        queue_response(
            transport,
            reply_to=request.message_id,
            payload={
                "controller_id": "rig_esp32",
                "firmware_version": "0.1.0",
                "protocol_version": 1,
            },
        )
        return original_receive()

    transport.receive = receive_identity  # type: ignore[method-assign]

    identity = client.identify()

    assert identity["controller_id"] == "rig_esp32"
    assert sent_request(transport).name == "identify"