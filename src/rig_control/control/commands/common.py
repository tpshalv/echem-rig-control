from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class CommandSource(StrEnum):
    """Which part of the software requested a control action."""

    MANUAL = "manual"
    DIAGNOSTIC = "diagnostic"
    RECIPE = "recipe"
    SAFETY_SYSTEM = "safety_system"


@dataclass(frozen=True, slots=True)
class EnterDeviceSafeState:
    """Request the explicitly defined safe state for one device."""

    device_id: str
    source: CommandSource = CommandSource.SAFETY_SYSTEM

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)


def validate_device_id(device_id: object) -> None:
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("Control-command device ID cannot be empty")


def validate_number(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise TypeError(f"{field_name} must be an int or float")

    if not isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")