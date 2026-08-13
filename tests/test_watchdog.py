import pytest

from rig_control.watchdog import Watchdog


class FakeClock:
    """Clock controlled directly by tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_watchdog_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        Watchdog(0)


def test_watchdog_has_not_expired_before_first_heartbeat() -> None:
    clock = FakeClock()
    watchdog = Watchdog(timeout_seconds=5, clock=clock)

    clock.advance(20)

    assert watchdog.has_received_heartbeat is False
    assert watchdog.has_expired is False


def test_watchdog_expires_at_timeout() -> None:
    clock = FakeClock()
    watchdog = Watchdog(timeout_seconds=5, clock=clock)
    watchdog.record_heartbeat()

    clock.advance(4.9)
    assert watchdog.has_expired is False

    clock.advance(0.1)
    assert watchdog.has_expired is True


def test_new_heartbeat_restarts_watchdog() -> None:
    clock = FakeClock()
    watchdog = Watchdog(timeout_seconds=5, clock=clock)
    watchdog.record_heartbeat()

    clock.advance(4)
    watchdog.record_heartbeat()
    clock.advance(4)

    assert watchdog.has_expired is False