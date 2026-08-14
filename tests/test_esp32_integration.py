import pytest

from rig_control.esp32.client import (
    ControllerClient,
    ControllerCommandError,
)
from rig_control.esp32.protocol_handler import ControllerProtocolHandler
from rig_control.esp32.simulated_controller import SimulatedController
from rig_control.esp32.protocol import Message
from rig_control.transports.loopback import LoopbackTransport


def make_system() -> tuple[
    SimulatedController,
    ControllerClient,
]:
    controller = SimulatedController(
        safe_outputs={
            "pump": False,
            "heater": False,
            "vent_valve": True,
        },
        watchdog_timeout_seconds=5,
    )
    handler = ControllerProtocolHandler(controller)

    def respond(text: str) -> str:
        request = Message.from_json(text)
        response = handler.handle(request)
        return response.to_json()

    transport = LoopbackTransport(respond)
    transport.connect()

    return controller, ControllerClient(transport)


def test_complete_heartbeat_and_output_round_trip() -> None:
    controller, client = make_system()

    client.heartbeat()
    client.set_output("pump", True)

    assert controller.outputs["pump"] is True


def test_complete_status_round_trip() -> None:
    _, client = make_system()

    status = client.get_status()

    assert status["outputs"] == {
        "pump": False,
        "heater": False,
        "vent_valve": True,
    }
    assert status["safe_state_active"] is True
    assert status["watchdog_tripped"] is False


def test_complete_safe_state_round_trip() -> None:
    controller, client = make_system()
    client.heartbeat()
    client.set_output("pump", True)
    client.set_output("vent_valve", False)

    client.apply_safe_state()

    assert controller.outputs == {
        "pump": False,
        "heater": False,
        "vent_valve": True,
    }


def test_controller_error_reaches_pc_client() -> None:
    controller, client = make_system()
    client.heartbeat()

    with pytest.raises(ControllerCommandError, match="Unknown output"):
        client.set_output("missing", True)

    assert controller.outputs["pump"] is False


def test_complete_identification_round_trip() -> None:
    _, client = make_system()

    identity = client.identify()

    assert identity["controller_id"] == "main_controller"
    assert identity["firmware_version"] == "simulated"
    assert identity["protocol_version"] == 1