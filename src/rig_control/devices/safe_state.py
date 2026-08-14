from typing import Protocol, runtime_checkable


@runtime_checkable
class SafeStateCapable(Protocol):
    """Device capability for entering a defined safe state."""

    def enter_safe_state(self) -> None:
        """Immediately request the device's safe state."""