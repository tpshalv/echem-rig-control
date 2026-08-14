import json

import pytest

from rig_control.esp32.protocol import Message, MessageType


def test_message_receives_a_unique_identifier() -> None:
    first = Message(MessageType.COMMAND, "status")
    second = Message(MessageType.COMMAND, "status")

    assert first.message_id
    assert first.message_id != second.message_id


def test_message_can_be_converted_to_json() -> None:
    message = Message(
        message_type=MessageType.COMMAND,
        name="set_output",
        payload={"output": "pump", "enabled": True},
        message_id="command-123",
    )

    data = json.loads(message.to_json())

    assert data == {
        "message_type": "command",
        "name": "set_output",
        "payload": {"output": "pump", "enabled": True},
        "message_id": "command-123",
    }


def test_message_survives_a_json_round_trip() -> None:
    original = Message(
        message_type=MessageType.MEASUREMENT,
        name="temperature",
        payload={"value": 25.4, "unit": "degC"},
    )

    restored = Message.from_json(original.to_json())

    assert restored == original


def test_message_rejects_unknown_message_type() -> None:
    text = (
        '{"message_type":"mystery","name":"test",'
        '"payload":{},"message_id":"message-1"}'
    )

    with pytest.raises(ValueError):
        Message.from_json(text)


def test_message_rejects_invalid_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        Message.from_json("not valid JSON")