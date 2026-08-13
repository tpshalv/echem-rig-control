import pytest

from rig_control.controllers.simulated import SimulatedController


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_controller(
    clock: FakeClock,
) -> SimulatedController:
    return SimulatedController(
        safe_outputs={
            "pump": False,
            "heater": False,
            "vent_valve": True,
        },
        watchdog_timeout_seconds=5,
        clock=clock,
    )


def test_controller_starts_in_configured_safe_state() -> None:
    controller = make_controller(FakeClock())

    assert controller.outputs == {
        "pump": False,
        "heater": False,
        "vent_valve": True,
    }
    assert controller.safe_state_active is True


def test_controller_rejects_output_before_first_heartbeat() -> None:
    controller = make_controller(FakeClock())

    with pytest.raises(RuntimeError, match="before first heartbeat"):
        controller.set_output("pump", True)


def test_controller_accepts_output_after_heartbeat() -> None:
    controller = make_controller(FakeClock())
    controller.record_heartbeat()

    controller.set_output("pump", True)

    assert controller.outputs["pump"] is True
    assert controller.safe_state_active is False


def test_controller_rejects_unknown_output() -> None:
    controller = make_controller(FakeClock())
    controller.record_heartbeat()

    with pytest.raises(KeyError, match="Unknown output"):
        controller.set_output("missing", True)


def test_watchdog_expiry_applies_safe_state() -> None:
    clock = FakeClock()
    controller = make_controller(clock)
    controller.record_heartbeat()
    controller.set_output("pump", True)
    controller.set_output("heater", True)
    controller.set_output("vent_valve", False)

    clock.advance(5)
    controller.check_watchdog()

    assert controller.outputs == {
        "pump": False,
        "heater": False,
        "vent_valve": True,
    }
    assert controller.safe_state_active is True


def test_fresh_heartbeat_prevents_safe_state() -> None:
    clock = FakeClock()
    controller = make_controller(clock)
    controller.record_heartbeat()
    controller.set_output("pump", True)

    clock.advance(4)
    controller.record_heartbeat()
    clock.advance(4)
    controller.check_watchdog()

    assert controller.outputs["pump"] is True


def test_returning_heartbeat_does_not_clear_watchdog_trip() -> None:
    clock = FakeClock()
    controller = make_controller(clock)
    controller.record_heartbeat()
    controller.set_output("pump", True)

    clock.advance(5)
    controller.check_watchdog()
    controller.record_heartbeat()

    assert controller.watchdog_tripped is True

    with pytest.raises(RuntimeError, match="latched"):
        controller.set_output("pump", True)


def test_controller_can_be_explicitly_rearmed() -> None:
    clock = FakeClock()
    controller = make_controller(clock)
    controller.record_heartbeat()
    controller.set_output("pump", True)

    clock.advance(5)
    controller.check_watchdog()
    controller.record_heartbeat()
    controller.rearm()
    controller.set_output("pump", True)

    assert controller.watchdog_tripped is False
    assert controller.outputs["pump"] is True


def test_controller_cannot_rearm_with_expired_heartbeat() -> None:
    clock = FakeClock()
    controller = make_controller(clock)
    controller.record_heartbeat()
    clock.advance(5)
    controller.check_watchdog()

    with pytest.raises(RuntimeError, match="expired heartbeat"):
        controller.rearm()