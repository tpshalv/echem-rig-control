from rig_control.esp32.protocol_handler import ControllerProtocolHandler
from rig_control.esp32.simulated_controller import SimulatedController
from rig_control.esp32.protocol import Message, MessageType


def make_handler() -> tuple[
    SimulatedController,
    ControllerProtocolHandler,
]:
    controller = SimulatedController(
        safe_outputs={"pump": False, "vent_valve": True},
        watchdog_timeout_seconds=5,
    )
    return controller, ControllerProtocolHandler(controller)


def command(name: str, payload: dict | None = None) -> Message:
    return Message(
        message_type=MessageType.COMMAND,
        name=name,
        payload=payload or {},
    )


def test_heartbeat_command_is_acknowledged() -> None:
    _, handler = make_handler()
    request = command("heartbeat")

    response = handler.handle(request)

    assert response.message_type is MessageType.RESPONSE
    assert response.name == "ok"
    assert response.payload["reply_to"] == request.message_id


def test_set_output_command_changes_controller_output() -> None:
    controller, handler = make_handler()
    handler.handle(command("heartbeat"))

    response = handler.handle(
        command("set_output", {"name": "pump", "enabled": True})
    )

    assert response.name == "ok"
    assert controller.outputs["pump"] is True


def test_status_command_returns_controller_state() -> None:
    _, handler = make_handler()

    response = handler.handle(command("status"))

    assert response.name == "ok"
    assert response.payload["outputs"] == {
        "pump": False,
        "vent_valve": True,
    }
    assert response.payload["safe_state_active"] is True


def test_unknown_command_returns_error_response() -> None:
    _, handler = make_handler()
    request = command("do_something_mysterious")

    response = handler.handle(request)

    assert response.name == "error"
    assert response.payload["reply_to"] == request.message_id
    assert "Unknown command" in response.payload["error"]


def test_invalid_output_value_returns_error_response() -> None:
    controller, handler = make_handler()
    handler.handle(command("heartbeat"))

    response = handler.handle(
        command("set_output", {"name": "pump", "enabled": "yes"})
    )

    assert response.name == "error"
    assert "Boolean" in response.payload["error"]
    assert controller.outputs["pump"] is False


def test_non_command_message_is_rejected() -> None:
    _, handler = make_handler()
    message = Message(
        message_type=MessageType.EVENT,
        name="test",
    )

    response = handler.handle(message)

    assert response.name == "error"
    assert response.payload["reply_to"] == message.message_id


def test_identify_returns_controller_information() -> None:
    controller = SimulatedController(
        safe_outputs={"pump": False},
        watchdog_timeout_seconds=5,
    )
    handler = ControllerProtocolHandler(
        controller,
        controller_id="rig_esp32",
        firmware_version="0.1.0",
    )

    response = handler.handle(command("identify"))

    assert response.name == "ok"
    assert response.payload["controller_id"] == "rig_esp32"
    assert response.payload["firmware_version"] == "0.1.0"
    assert response.payload["protocol_version"] == 1


def test_read_sensors_returns_generic_named_channels() -> None:
    channels = [
        {"name": "temperature", "value": 24.0, "unit": "degC", "quality": "good"},
        {"name": "humidity", "value": 50.0, "unit": "%RH", "quality": "bad"},
    ]
    controller = SimulatedController(
        safe_outputs={"pump": False},
        watchdog_timeout_seconds=5,
        sensor_channels=channels,
    )

    response = ControllerProtocolHandler(controller).handle(command("read_sensors"))

    assert response.name == "ok"
    assert response.payload["channels"] == channels
