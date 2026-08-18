from typing import Any

from rig_control.diagnostics import esp32
from rig_control.diagnostics.esp32 import run_bring_up


class FakeClient:
    def __init__(self, *, fail_status_after_on: bool = False) -> None:
        self.output = False
        self.calls: list[object] = []
        self.fail_status_after_on = fail_status_after_on
        self.watchdog_tripped = False

    def identify(self) -> dict[str, Any]:
        self.calls.append("identify")
        return {"controller_id": "esp32_main_controller"}

    def heartbeat(self) -> None:
        self.calls.append("heartbeat")

    def get_status(self) -> dict[str, Any]:
        self.calls.append("status")
        if self.output and self.fail_status_after_on:
            raise TimeoutError("status timeout")
        return {
            "outputs": {"led": self.output},
            "safe_state_active": not self.output,
            "watchdog_tripped": self.watchdog_tripped,
        }

    def set_output(self, name: str, enabled: bool) -> None:
        self.calls.append(("set_output", name, enabled))
        if self.watchdog_tripped:
            from rig_control.esp32.client import ControllerCommandError

            raise ControllerCommandError("watchdog tripped")
        self.output = enabled

    def apply_safe_state(self) -> None:
        self.calls.append("safe_state")
        self.output = False

    def rearm(self) -> None:
        self.calls.append("rearm")
        self.watchdog_tripped = False


class FakeSession:
    instances: list["FakeSession"] = []
    fail_status_after_on = False

    def __init__(self, controller_id: str, transport: object) -> None:
        self.controller_id = controller_id
        self.transport = transport
        self.client = FakeClient(fail_status_after_on=self.fail_status_after_on)
        self.connected = False
        self.disconnected = False
        self.instances.append(self)

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True


def test_bring_up_runs_expected_checklist_and_leaves_output_off(capsys) -> None:
    FakeSession.instances.clear()
    FakeSession.fail_status_after_on = False

    def expire_watchdog(seconds: float) -> None:
        if seconds > 2:
            client = FakeSession.instances[-1].client
            client.output = False
            client.watchdog_tripped = True

    passed = run_bring_up("COM7", session_factory=FakeSession, sleep=expire_watchdog)

    session = FakeSession.instances[-1]
    assert passed is True
    assert session.controller_id == "esp32_main_controller"
    assert session.client.output is False
    assert session.disconnected is True
    assert session.client.calls == [
        "identify",
        "heartbeat",
        "status",
        "rearm",
        ("set_output", "led", True),
        "status",
        ("set_output", "led", False),
        "safe_state",
        "heartbeat",
        "rearm",
        ("set_output", "led", True),
        "status",
        ("set_output", "led", True),
        "heartbeat",
        "status",
        "rearm",
        ("set_output", "led", True),
        "status",
        ("set_output", "led", False),
    ]
    output = capsys.readouterr().out
    assert "[PASS] Status reports led ON" in output
    assert "[PASS] Watchdog forced safe state and latched" in output
    assert "[PASS] Heartbeat does not clear watchdog latch" in output
    assert "[PASS] Status reports led ON after recovery" in output
    assert "BRING-UP PASSED" in output


def test_failure_after_output_on_applies_safe_state(capsys) -> None:
    FakeSession.instances.clear()
    FakeSession.fail_status_after_on = True

    passed = run_bring_up("COM7", session_factory=FakeSession)

    session = FakeSession.instances[-1]
    assert passed is False
    assert session.client.output is False
    assert "safe_state" in session.client.calls
    assert session.disconnected is True
    output = capsys.readouterr().out
    assert "[FAIL] Status reports led ON" in output
    assert "[PASS] Cleanup apply safe state" in output
    assert "BRING-UP FAILED" in output


def test_main_returns_failure_exit_code(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def fail(port: str, **options: object) -> bool:
        recorded["port"] = port
        recorded.update(options)
        return False

    monkeypatch.setattr(esp32, "run_bring_up", fail)

    result = esp32.main(["COM7"])

    assert result == 1
    assert recorded["port"] == "COM7"
    assert recorded["controller_id"] == "esp32_main_controller"
    assert recorded["output_name"] == "led"
    assert recorded["baud_rate"] == 115200
    assert recorded["watchdog_timeout_seconds"] == 15.0
    assert recorded["recovery_hold_seconds"] == 2.0
