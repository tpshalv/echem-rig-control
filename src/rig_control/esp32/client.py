from typing import Any

from rig_control.esp32.protocol import Message, MessageType
from rig_control.transports.duplex_text import DuplexTextTransport


class ControllerCommandError(RuntimeError):
    """A remote controller rejected a command."""


class ControllerClient:
    """PC-side interface for sending commands to a controller."""

    def __init__(self, transport: DuplexTextTransport) -> None:
        self._transport = transport

    def identify(self) -> dict[str, Any]:
        """Return the remote controller's identity information."""

        return self._request("identify")

    def heartbeat(self) -> None:
        self._request("heartbeat")

    def set_output(self, name: str, enabled: bool) -> None:
        self._request(
            "set_output",
            {
                "name": name,
                "enabled": enabled,
            },
        )

    def apply_safe_state(self) -> None:
        self._request("safe_state")

    def rearm(self) -> None:
        self._request("rearm")

    def get_status(self) -> dict[str, Any]:
        return self._request("status")

    def read_sensors(self) -> list[dict[str, Any]]:
        payload = self._request("read_sensors")
        channels = payload.get("channels")
        if not isinstance(channels, list):
            raise RuntimeError("Controller sensor response has no channel list")
        if not all(isinstance(channel, dict) for channel in channels):
            raise RuntimeError("Controller sensor channels must be objects")
        return channels

    def _request(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = Message(
            message_type=MessageType.COMMAND,
            name=name,
            payload=payload or {},
        )

        self._transport.send(request.to_json())
        response = Message.from_json(self._transport.receive())

        if response.message_type is not MessageType.RESPONSE:
            raise RuntimeError("Controller reply is not a response")

        if response.payload.get("reply_to") != request.message_id:
            raise RuntimeError("Controller response does not match request")

        if response.name == "error":
            explanation = response.payload.get(
                "error",
                "Controller rejected command",
            )
            raise ControllerCommandError(str(explanation))

        if response.name != "ok":
            raise RuntimeError(
                f"Unknown controller response: {response.name}"
            )

        return response.payload
