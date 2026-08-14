from dataclasses import dataclass

from rig_control.control.commands.common import (
    CommandSource,
    validate_device_id,
    validate_number,
)


@dataclass(frozen=True, slots=True)
class SetMfcFlow:
    """Request a new MFC flow setpoint."""

    device_id: str
    flow: float
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(self.flow, "MFC flow")