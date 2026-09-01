from typing import Any

from rig_control.diagnostics import esp32
from rig_control.diagnostics.esp32 import run_bring_up


class FakeClient:
    def __init__(self, *, fail_status: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_status = fail_status

    def identify(self) -> dict[str, Any]:
        self.calls.append("identify")
        return {"controller_id": "esp32_main_controller"}

    def heartbeat(self) -> None:
        self.calls.append("heartbeat")

    def get_status(self) -> dict[str, Any]:
        self.calls.append("status")
        if self.fail_status:
            raise TimeoutError("status timeout")
        return {"outputs": {}, "safe_state_active": True, "watchdog_tripped": False}

    def rearm(self) -> None:
        self.calls.append("rearm")

    def read_sensors(self) -> list[dict[str, Any]]:
        self.calls.append("read_sensors")
        return []


class FakeSession:
    instances: list["FakeSession"] = []
    fail_status = False

    def __init__(self, controller_id: str, transport: object) -> None:
        self.controller_id = controller_id
        self.transport = transport
        self.client = FakeClient(fail_status=self.fail_status)
        self.disconnected = False
        self.instances.append(self)

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        self.disconnected = True


def test_bring_up_runs_non_destructive_checklist(capsys) -> None:
    FakeSession.instances.clear()
    FakeSession.fail_status = False
    assert run_bring_up("COM7", session_factory=FakeSession) is True
    session = FakeSession.instances[-1]
    assert session.controller_id == "esp32_main_controller"
    assert session.disconnected is True
    assert session.client.calls == [
        "identify", "heartbeat", "status", "rearm", "read_sensors"
    ]
    assert "BRING-UP PASSED" in capsys.readouterr().out


def test_failure_still_disconnects(capsys) -> None:
    FakeSession.instances.clear()
    FakeSession.fail_status = True
    assert run_bring_up("COM7", session_factory=FakeSession) is False
    assert FakeSession.instances[-1].disconnected is True
    assert "BRING-UP FAILED" in capsys.readouterr().out


def test_main_returns_failure_exit_code(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def fail(port: str, **options: object) -> bool:
        recorded["port"] = port
        recorded.update(options)
        return False

    monkeypatch.setattr(esp32, "run_bring_up", fail)
    assert esp32.main(["COM7"]) == 1
    assert recorded == {
        "port": "COM7",
        "controller_id": "esp32_main_controller",
        "baud_rate": 115200,
        "timeout_seconds": 2.0,
    }
