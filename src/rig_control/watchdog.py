from collections.abc import Callable
from time import monotonic


class Watchdog:
    """Detect when an expected heartbeat has stopped arriving."""

    def __init__(
        self,
        timeout_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Watchdog timeout must be greater than zero")

        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._last_heartbeat: float | None = None

    @property
    def has_received_heartbeat(self) -> bool:
        return self._last_heartbeat is not None

    @property
    def has_expired(self) -> bool:
        if self._last_heartbeat is None:
            return False

        return self._clock() - self._last_heartbeat >= self._timeout_seconds

    def record_heartbeat(self) -> None:
        """Record that a valid heartbeat has just arrived."""

        self._last_heartbeat = self._clock()