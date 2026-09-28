from dataclasses import dataclass

from rig_control.control.commands.common import (
    CommandSource,
    validate_device_id,
    validate_number,
)


@dataclass(frozen=True, slots=True)
class SetHotplateTemperature:
    """Request a new hotplate target temperature in degC.

    Hardware, rig and run ceilings are enforced by the driver, which knows
    the detected model's rating.
    """

    device_id: str
    temperature: float
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(self.temperature, "Hotplate temperature")


@dataclass(frozen=True, slots=True)
class SetHotplateSpeed:
    """Request a new hotplate stirring speed in rpm."""

    device_id: str
    rpm: float
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(self.rpm, "Hotplate stir speed")


@dataclass(frozen=True, slots=True)
class SetHotplateHeating:
    """Request that hotplate heating be started or stopped."""

    device_id: str
    enabled: bool
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        if not isinstance(self.enabled, bool):
            raise TypeError("Hotplate heating state must be Boolean")


@dataclass(frozen=True, slots=True)
class SetHotplateStirring:
    """Request that hotplate stirring be started or stopped."""

    device_id: str
    enabled: bool
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        if not isinstance(self.enabled, bool):
            raise TypeError("Hotplate stirring state must be Boolean")
