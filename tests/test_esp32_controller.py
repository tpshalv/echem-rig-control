from typing import Any

from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.models import DeviceStatus
from rig_control.devices.manager import DeviceManager
from rig_control.control.service import RigControlService
from rig_control.control.commands import CommandSource, RearmController, SetControllerOutput
from rig_control.ui.manual_control.model import ManualControlViewModel
from rig_control.devices.esp32_bus import Esp32Bus


class FakeClient:
    def __init__(self) -> None:
        self.outputs = {"led": False}
        self.watchdog_tripped = True
        self.calls: list[object] = []

    def heartbeat(self) -> None:
        self.calls.append("heartbeat")

    def get_status(self) -> dict[str, Any]:
        self.calls.append("status")
        return {
            "outputs": dict(self.outputs),
            "safe_state_active": not any(self.outputs.values()),
            "watchdog_tripped": self.watchdog_tripped,
        }

    def rearm(self) -> None:
        self.calls.append("rearm")
        self.watchdog_tripped = False

    def set_output(self, name: str, enabled: bool) -> None:
        self.calls.append(("set_output", name, enabled))
        self.outputs[name] = enabled

    def apply_safe_state(self) -> None:
        self.calls.append("safe_state")
        self.outputs["led"] = False


class FakeSession:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.connected = False
        self.status = DeviceStatus.DISCONNECTED

    def connect(self) -> None:
        self.connected = True
        self.status = DeviceStatus.READY

    def disconnect(self) -> None:
        self.connected = False
        self.status = DeviceStatus.DISCONNECTED


def test_controller_rearms_controls_output_and_disconnects_safe() -> None:
    session = FakeSession()
    controller = Esp32Controller(
        "esp32_main_controller",
        Esp32Bus("main_esp32", session),  # type: ignore[arg-type]
        heartbeat_interval_seconds=60,
    )

    controller.connect()
    controller.rearm()
    controller.set_output("led", True)

    assert controller.status is DeviceStatus.READY
    assert controller.controller_status["outputs"]["led"] is True
    assert controller.controller_status["watchdog_tripped"] is False

    controller.disconnect()

    assert session.connected is False
    assert session.client.outputs["led"] is False
    assert controller.status is DeviceStatus.DISCONNECTED
    assert "safe_state" in session.client.calls


def test_connecting_controller_twice_does_not_acquire_bus_twice() -> None:
    session = FakeSession()
    bus = Esp32Bus("main_esp32", session)  # type: ignore[arg-type]
    controller = Esp32Controller(
        "esp32_main_controller",
        bus,
        heartbeat_interval_seconds=60,
    )

    controller.connect()
    controller.connect()

    assert bus.client_count == 1

    controller.disconnect()
    assert bus.client_count == 0


def test_control_service_and_manual_model_control_led() -> None:
    session = FakeSession()
    controller = Esp32Controller(
        "esp32_main_controller",
        Esp32Bus("main_esp32", session),  # type: ignore[arg-type]
        heartbeat_interval_seconds=60,
    )
    manager = DeviceManager()
    manager.register(controller)
    manager.connect(controller.device_id)
    service = RigControlService(manager)

    service.execute(RearmController(controller.device_id, CommandSource.MANUAL))
    service.execute(SetControllerOutput(controller.device_id, "led", True, CommandSource.MANUAL))
    row = ManualControlViewModel(manager, service).controller_rows()[0]

    assert row.led_enabled is True
    assert row.watchdog_tripped is False
    assert row.safe_state_active is False

    manager.disconnect(controller.device_id)
