import pytest

from rig_control.read_retry import retry_read


def test_retry_read_recovers_with_exponential_backoff() -> None:
    calls = 0
    delays = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("temporary glitch")
        return "ok"

    assert retry_read(
        operation, attempts=3, initial_delay_seconds=0.1,
        sleep_fn=delays.append,
    ) == "ok"
    assert calls == 3
    assert delays == [0.1, 0.2]


def test_retry_read_reraises_final_error() -> None:
    def fail() -> None:
        raise OSError("still unavailable")

    with pytest.raises(OSError, match="still unavailable"):
        retry_read(
            fail, attempts=2, initial_delay_seconds=0,
            sleep_fn=lambda _delay: None,
        )
