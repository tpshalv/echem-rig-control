import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

PROTOCOL_VERSION = 1

class MessageType(StrEnum):
    """Kinds of messages exchanged with a remote controller."""

    COMMAND = "command"
    RESPONSE = "response"
    MEASUREMENT = "measurement"
    EVENT = "event"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True, slots=True)
class Message:
    """One structured message exchanged with a remote controller."""

    message_type: MessageType
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid4()))

    def to_json(self) -> str:
        """Convert the message into text for transmission."""

        return json.dumps(
            {
                "message_type": self.message_type,
                "name": self.name,
                "payload": self.payload,
                "message_id": self.message_id,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "Message":
        """Create a message from received JSON text."""

        data = json.loads(text)

        return cls(
            message_type=MessageType(data["message_type"]),
            name=data["name"],
            payload=data.get("payload", {}),
            message_id=data["message_id"],
        )