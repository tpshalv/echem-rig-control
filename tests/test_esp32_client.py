import pytest

from rig_control.esp32.client import (
    ControllerClient,
    ControllerCommandError,
)
from rig_control.esp32.protocol import Message, MessageType
from rig_control.transports.simulated_duplex_text import SimulatedDuplexTextTransport


def make_client() -> tuple[SimulatedDuplexTextTransport, ControllerClient]:
    transport = SimulatedDuplexTextTransport()
    transport.connect()
    return transport, ControllerClient(transport)


def queue_response(
    transport: SimulatedDuplexTextTransport,
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


def sent_request(transport: SimulatedDuplexTextTransport) -> Message:
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
    transport = SimulatedDuplexTextTransport()
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


def test_client_reads_capability_description() -> None:
    transport, client = make_client()
    original_receive = transport.receive
    capabilities = {
        "devices": [
            {
                "id": "esp32_dht11",
                "kind": "dht11",
                "label": "DHT11 temperature and humidity",
                "recommended_poll_interval_seconds": 1.5,
                "channels": [
                    {"name": "temperature", "unit": "degC"},
                    {"name": "humidity", "unit": "%RH"},
                ],
            }
        ],
        "outputs": [],
    }

    def receive_description() -> str:
        request = sent_request(transport)
        queue_response(
            transport,
            reply_to=request.message_id,
            payload=capabilities,
        )
        return original_receive()

    transport.receive = receive_description  # type: ignore[method-assign]

    assert client.describe_capabilities() == capabilities
    assert sent_request(transport).name == "describe"


def test_client_rejects_invalid_capability_description() -> None:
    transport, client = make_client()
    original_receive = transport.receive

    def receive_invalid_description() -> str:
        request = sent_request(transport)
        queue_response(
            transport,
            reply_to=request.message_id,
            payload={"devices": [], "outputs": "invalid"},
        )
        return original_receive()

    transport.receive = receive_invalid_description  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="output list"):
        client.describe_capabilities()


def test_client_reads_generic_sensor_channels() -> None:
    transport, client = make_client()
    original_receive = transport.receive
    channels = [
        {"name": "temperature", "value": 22.0, "unit": "degC", "quality": "good"},
        {"name": "humidity", "value": 45.0, "unit": "%RH", "quality": "good"},
    ]

    def receive_sensors() -> str:
        request = sent_request(transport)
        queue_response(
            transport,
            reply_to=request.message_id,
            payload={"channels": channels},
        )
        return original_receive()

    transport.receive = receive_sensors  # type: ignore[method-assign]

    assert client.read_sensors() == channels
    assert sent_request(transport).name == "read_sensors"


def test_client_rejects_sensor_response_without_channel_list() -> None:
    transport, client = make_client()
    original_receive = transport.receive

    def receive_invalid_sensors() -> str:
        request = sent_request(transport)
        queue_response(transport, reply_to=request.message_id, payload={})
        return original_receive()

    transport.receive = receive_invalid_sensors  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="channel list"):
        client.read_sensors()


def test_sensor_read_retries_a_temporary_receive_failure() -> None:
    transport = SimulatedDuplexTextTransport()
    transport.connect()
    client = ControllerClient(transport, read_retry_delay_seconds=0)
    original_receive = transport.receive
    attempts = 0

    def flaky_receive() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary USB glitch")
        request = sent_request(transport)
        queue_response(
            transport, reply_to=request.message_id,
            payload={"channels": []},
        )
        return original_receive()

    transport.receive = flaky_receive  # type: ignore[method-assign]

    assert client.read_sensors() == []
    assert attempts == 2
    assert len(transport.sent_messages) == 2
