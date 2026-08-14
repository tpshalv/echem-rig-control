from dataclasses import dataclass

from rig_control.control.commands.common import (
    CommandSource,
    validate_device_id,
    validate_number,
)


@dataclass(frozen=True, slots=True)
class SetPowerSupplyVoltage:
    """Request a new power-supply voltage setpoint."""

    device_id: str
    voltage: float
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(
            self.voltage,
            "Power-supply voltage",
        )


@dataclass(frozen=True, slots=True)
class SetPowerSupplyCurrentLimit:
    """Request a new power-supply current setting/limit."""

    device_id: str
    current: float
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_number(
            self.current,
            "Power-supply current limit",
        )


@dataclass(frozen=True, slots=True)
class SetPowerSupplyOutput:
    """Request that a power-supply output be enabled or disabled."""

    device_id: str
    enabled: bool
    source: CommandSource

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)

        if not isinstance(self.enabled, bool):
            raise TypeError(
                "Power-supply output state must be Boolean"
            )