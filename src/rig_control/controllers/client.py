from typing import Any

from rig_control.protocol import Message, MessageType
from rig_control.transports.base import Transport


class ControllerCommandError(RuntimeError):
    """A remote controller rejected a command."""


class ControllerClient:
    """PC-side interface for sending commands to a controller."""

    def __init__(self, transport: Transport) -> None:
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