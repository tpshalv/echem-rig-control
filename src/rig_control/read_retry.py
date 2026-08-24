from collections.abc import Callable
from time import sleep
from typing import TypeVar


T = TypeVar("T")


def retry_read(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    initial_delay_seconds: float = 0.05,
    backoff: float = 2.0,
    sleep_fn: Callable[[float], None] = sleep,
) -> T:
    """Retry an idempotent read with a short exponential backoff."""

    if attempts < 1:
        raise ValueError("Read retry attempts must be at least one")
    if initial_delay_seconds < 0:
        raise ValueError("Read retry delay cannot be negative")
    if backoff < 1:
        raise ValueError("Read retry backoff must be at least one")
    delay = initial_delay_seconds
    for attempt in range(attempts):
        try:
            return operation()
        except Exception:
            if attempt == attempts - 1:
                raise
            sleep_fn(delay)
            delay *= backoff
    raise AssertionError("unreachable")
