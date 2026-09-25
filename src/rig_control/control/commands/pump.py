from dataclasses import dataclass

from rig_control.control.commands.common import (
    CommandSource,
    validate_device_id,
    validate_number,
)
from rig_control.devices.pump import PumpDirection


@dataclass(frozen=True, slots=True)
class SetPumpSpeed:
    """Request a new pump speed setpoint, in the pump's own native RPM.

    Converting a desired flow rate to RPM using a calibration happens
    above the control layer, before this command is constructed; the
    control service only ever talks to a pump in its own native unit,
    the same way it talks to an MFC in the MFC's own native flow unit.
    """

    device_id: str
    rpm: float
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(self.rpm, "Pump speed")
        if self.rpm < 0:
            raise ValueError("Pump speed cannot be negative")


@dataclass(frozen=True, slots=True)
class SetPumpDirection:
    """Request a new pump running direction."""

    device_id: str
    direction: PumpDirection
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        if not isinstance(self.direction, PumpDirection):
            raise TypeError("Pump direction must be a PumpDirection")


@dataclass(frozen=True, slots=True)
class SetPumpRunning:
    """Request that a pump be started or stopped."""

    device_id: str
    running: bool
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        if not isinstance(self.running, bool):
            raise TypeError("Pump running state must be Boolean")
