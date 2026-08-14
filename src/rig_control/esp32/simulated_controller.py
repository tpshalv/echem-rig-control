from collections.abc import Callable
from time import monotonic

from rig_control.watchdog import Watchdog


class SimulatedController:
    """ESP32-like controller used to test outputs and watchdog behaviour."""

    def __init__(
        self,
        safe_outputs: dict[str, bool],
        watchdog_timeout_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._safe_outputs = safe_outputs.copy()
        self._outputs = safe_outputs.copy()
        self._watchdog = Watchdog(watchdog_timeout_seconds, clock)
        self._safe_state_active = True
        self._watchdog_tripped = False

    @property
    def outputs(self) -> dict[str, bool]:
        """Return a copy of the current output states."""

        return self._outputs.copy()

    @property
    def safe_state_active(self) -> bool:
        return self._safe_state_active

    @property
    def watchdog_tripped(self) -> bool:
        """Return whether communication loss has latched the safe state."""

        return self._watchdog_tripped

    def record_heartbeat(self) -> None:
        self._watchdog.record_heartbeat()

    def set_output(self, name: str, enabled: bool) -> None:
        if name not in self._outputs:
            raise KeyError(f"Unknown output: {name}")

        if not self._watchdog.has_received_heartbeat:
            raise RuntimeError("Cannot control outputs before first heartbeat")

        if self._watchdog.has_expired:
            self._trip_watchdog()

        if self._watchdog_tripped:
            raise RuntimeError("Cannot control outputs: watchdog trip is latched")

        self._outputs[name] = enabled
        self._safe_state_active = self._outputs == self._safe_outputs

    def check_watchdog(self) -> None:
        """Latch and apply the safe state after communication loss."""

        if self._watchdog.has_expired:
            self._trip_watchdog()

    def rearm(self) -> None:
        """Permit control again after communication has been verified."""

        if not self._watchdog.has_received_heartbeat:
            raise RuntimeError("Cannot rearm before receiving a heartbeat")

        if self._watchdog.has_expired:
            raise RuntimeError("Cannot rearm with an expired heartbeat")

        self._watchdog_tripped = False

    def _trip_watchdog(self) -> None:
        self._watchdog_tripped = True
        self.apply_safe_state()

    def apply_safe_state(self) -> None:
        """Immediately command every output to its configured safe value."""

        self._outputs = self._safe_outputs.copy()
        self._safe_state_active = True