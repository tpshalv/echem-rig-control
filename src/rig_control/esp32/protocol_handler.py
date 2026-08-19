from rig_control.esp32.simulated_controller import SimulatedController
from rig_control.esp32.protocol import Message, MessageType, PROTOCOL_VERSION


class ControllerProtocolHandler:
    """Process protocol messages intended for a controller."""

    def __init__(
        self,
        controller: SimulatedController,
        controller_id: str = "main_controller",
        firmware_version: str = "simulated",
    ) -> None:
        self._controller = controller
        self._controller_id = controller_id
        self._firmware_version = firmware_version
    
    def handle(self, message: Message) -> Message:
        """Handle one command and return a correlated response."""

        if message.message_type is not MessageType.COMMAND:
            return self._error(message, "Expected a command message")

        try:
            payload = self._handle_command(message)
        except (KeyError, TypeError, RuntimeError, ValueError) as error:
            return self._error(message, str(error))

        return Message(
            message_type=MessageType.RESPONSE,
            name="ok",
            payload={
                "reply_to": message.message_id,
                **payload,
            },
        )

    def _handle_command(self, message: Message) -> dict[str, object]:
        if message.name == "identify":
            return {
                "controller_id": self._controller_id,
                "firmware_version": self._firmware_version,
                "protocol_version": PROTOCOL_VERSION,
            }

        if message.name == "heartbeat":
            self._controller.record_heartbeat()
            return {}

        if message.name == "set_output":
            name = message.payload["name"]
            enabled = message.payload["enabled"]

            if not isinstance(name, str):
                raise TypeError("Output name must be a string")

            if not isinstance(enabled, bool):
                raise TypeError("Output enabled state must be a Boolean")

            self._controller.set_output(name, enabled)
            return {"name": name, "enabled": enabled}

        if message.name == "safe_state":
            self._controller.apply_safe_state()
            return {"safe_state_active": True}

        if message.name == "rearm":
            self._controller.rearm()
            return {"watchdog_tripped": False}

        if message.name == "status":
            return {
                "outputs": self._controller.outputs,
                "safe_state_active": self._controller.safe_state_active,
                "watchdog_tripped": self._controller.watchdog_tripped,
            }

        if message.name == "read_sensors":
            return {"channels": self._controller.read_sensors()}

        raise ValueError(f"Unknown command: {message.name}")

    @staticmethod
    def _error(message: Message, explanation: str) -> Message:
        return Message(
            message_type=MessageType.RESPONSE,
            name="error",
            payload={
                "reply_to": message.message_id,
                "error": explanation,
            },
        )
