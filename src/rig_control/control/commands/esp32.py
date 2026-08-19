from dataclasses import dataclass

from rig_control.control.commands.common import CommandSource, validate_device_id


@dataclass(frozen=True, slots=True)
class SetControllerOutput:
    device_id: str
    output_name: str
    enabled: bool
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        if not isinstance(self.output_name, str) or not self.output_name.strip():
            raise ValueError("Controller output name cannot be empty")
        if not isinstance(self.enabled, bool):
            raise TypeError("Controller output state must be Boolean")


@dataclass(frozen=True, slots=True)
class RearmController:
    device_id: str
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
