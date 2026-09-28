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
    #: The unit the value is in. Empty means the device's own configured
    #: unit, which is what a recipe written against that device means.
    unit: str = ""

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(self.flow, "MFC flow")
        if self.unit and self.unit.strip().casefold() not in {"sccm", "slpm"}:
            raise ValueError("MFC flow unit must be SCCM or SLPM")