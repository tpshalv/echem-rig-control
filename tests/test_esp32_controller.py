from typing import Any

from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.models import DeviceStatus
from rig_control.devices.manager import DeviceManager
from rig_control.control.service import RigControlService
from rig_control.ui.manual_control.model import ManualControlViewModel
from rig_control.devices.esp32_bus import Esp32Bus


class FakeClient:
    def __init__(self) -> None:
        self.outputs = {"auxiliary_output": False}
        self.watchdog_tripped = True
        self.calls: list[object] = []
        self.heartbeat_error: Exception | None = None

    def heartbeat(self) -> None:
        self.calls.append("heartbeat")
        if self.heartbeat_error is not None:
            raise self.heartbeat_error

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
        self.outputs["auxiliary_output"] = False


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
    assert controller.controller_status["watchdog_tripped"] is False
    controller.set_output("auxiliary_output", True)

    assert controller.status is DeviceStatus.READY
    assert controller.controller_status["outputs"]["auxiliary_output"] is True
    assert controller.controller_status["watchdog_tripped"] is False

    controller.disconnect()

    assert session.connected is False
    assert session.client.outputs["auxiliary_output"] is False
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


def test_heartbeat_failures_recovery_and_watchdog_trip_are_diagnostic() -> None:
    session = FakeSession()
    events = []
    controller = Esp32Controller(
        "esp32_main_controller",
        Esp32Bus("main_esp32", session),  # type: ignore[arg-type]
        heartbeat_interval_seconds=60,
        event_sink=lambda event, details: events.append((event, details)),
    )
    controller.connect()
    session.client.heartbeat_error = OSError("temporary USB failure")

    controller._heartbeat_once()
    controller._heartbeat_once()

    failed = controller.heartbeat_diagnostics()
    assert failed["consecutive_failures"] == 2
    assert failed["total_failures"] == 2
    assert "temporary USB failure" in failed["last_error"]
    assert sum("heartbeat communication failed" in event.message for event, _ in events) == 1

    session.client.heartbeat_error = None
    session.client.watchdog_tripped = True
    controller._last_status_refresh = 0.0
    controller._heartbeat_once()

    recovered = controller.heartbeat_diagnostics()
    assert recovered["consecutive_failures"] == 0
    assert recovered["recoveries"] == 1
    assert recovered["watchdog_tripped"] is True
    assert any("communication recovered" in event.message for event, _ in events)
    assert any("watchdog tripped" in event.message for event, _ in events)
    controller.disconnect()


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

    model = ManualControlViewModel(manager, service)
    assert model.rearm_controller(controller.device_id).succeeded
    assert model.set_controller_output(controller.device_id, "auxiliary_output", True).succeeded
    assert controller.controller_status["safe_state_active"] is False
    assert controller.controller_status["watchdog_tripped"] is False

    manager.disconnect(controller.device_id)
