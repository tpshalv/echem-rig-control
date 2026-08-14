import pytest

from rig_control.esp32.protocol_handler import ControllerProtocolHandler
from rig_control.esp32.session import ControllerIdentityError, ControllerSession
from rig_control.esp32.simulated_controller import SimulatedController
from rig_control.models import DeviceStatus, EventSeverity
from rig_control.esp32.protocol import Message
from rig_control.transports.loopback import LoopbackTransport


def make_session() -> ControllerSession:
    controller = SimulatedController(
        safe_outputs={"pump": False},
        watchdog_timeout_seconds=5,
    )
    handler = ControllerProtocolHandler(controller)

    def respond(text: str) -> str:
        request = Message.from_json(text)
        return handler.handle(request).to_json()

    return ControllerSession(
        controller_id="main_controller",
        transport=LoopbackTransport(respond),
    )


def test_session_starts_disconnected() -> None:
    session = make_session()

    assert session.status is DeviceStatus.DISCONNECTED
    assert session.events == ()


def test_session_connects_and_verifies_controller() -> None:
    session = make_session()

    session.connect()

    assert session.status is DeviceStatus.READY
    assert session.events[-1].message == "Controller connected and ready"
    assert session.events[-1].severity is EventSeverity.INFO


def test_session_disconnects_cleanly() -> None:
    session = make_session()
    session.connect()

    session.disconnect()

    assert session.status is DeviceStatus.DISCONNECTED
    assert session.events[-1].message == "Controller disconnected"


def test_session_records_failed_connection() -> None:
    def fail_to_respond(_: str) -> str:
        raise RuntimeError("Simulated communication failure")

    session = ControllerSession(
        controller_id="main_controller",
        transport=LoopbackTransport(fail_to_respond),
    )

    with pytest.raises(RuntimeError, match="communication failure"):
        session.connect()

    assert session.status is DeviceStatus.DISCONNECTED
    assert session.events[-1].severity is EventSeverity.ERROR
    assert session.events[-1].message == "Controller connection failed"


def test_session_rejects_wrong_controller() -> None:
    controller = SimulatedController(
        safe_outputs={"pump": False},
        watchdog_timeout_seconds=5,
    )
    handler = ControllerProtocolHandler(
        controller,
        controller_id="unexpected_controller",
    )

    def respond(text: str) -> str:
        request = Message.from_json(text)
        return handler.handle(request).to_json()

    session = ControllerSession(
        controller_id="main_controller",
        transport=LoopbackTransport(respond),
    )

    with pytest.raises(
        ControllerIdentityError,
        match="unexpected_controller",
    ):
        session.connect()

    assert session.status is DeviceStatus.DISCONNECTED
    assert session.events[-1].severity is EventSeverity.ERROR


def test_session_rejects_incompatible_protocol() -> None:
    controller = SimulatedController(
        safe_outputs={"pump": False},
        watchdog_timeout_seconds=5,
    )
    handler = ControllerProtocolHandler(controller)

    def respond(text: str) -> str:
        request = Message.from_json(text)
        response = handler.handle(request)

        if request.name == "identify":
            response.payload["protocol_version"] = 999

        return response.to_json()

    session = ControllerSession(
        controller_id="main_controller",
        transport=LoopbackTransport(respond),
    )

    with pytest.raises(
        ControllerIdentityError,
        match="incompatible",
    ):
        session.connect()

    assert session.status is DeviceStatus.DISCONNECTED