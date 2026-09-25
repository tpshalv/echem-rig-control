from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from rig_control.devices.base import Device


class PumpDirection(StrEnum):
    FORWARD = "forward"
    REVERSE = "reverse"


@dataclass(frozen=True, slots=True)
class PumpLimits:
    """Configured operating limits for one peristaltic pump."""

    maximum_speed_rpm: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.maximum_speed_rpm, bool)
            or not isinstance(self.maximum_speed_rpm, (int, float))
        ):
            raise TypeError("Maximum speed must be an int or float")

        if not isfinite(float(self.maximum_speed_rpm)):
            raise ValueError("Maximum speed must be finite")

        if self.maximum_speed_rpm <= 0:
            raise ValueError("Maximum speed must be greater than zero")


class Pump(Device):
    """Common capability required from a peristaltic pump.

    Speed is expressed in RPM, the pump's own controllable quantity.
    Converting a desired flow rate (ml/min) to RPM depends on an external,
    user-measured calibration that is expected to be replaced from time to
    time; that conversion is deliberately kept out of this interface and
    belongs to the calibration service and the control layer built on top
    of it, not to the driver.
    """

    @property
    @abstractmethod
    def limits(self) -> PumpLimits:
        """Return the software-configured speed limit."""

    @property
    @abstractmethod
    def speed_setpoint_rpm(self) -> float | None:
        """Return the last confirmed speed setpoint, or None if unknown."""

    @abstractmethod
    def set_speed_rpm(self, rpm: float) -> None:
        """Set the requested pump speed in RPM."""

    @property
    @abstractmethod
    def direction(self) -> PumpDirection | None:
        """Return the last confirmed running direction, or None if unknown."""

    @abstractmethod
    def set_direction(self, direction: PumpDirection) -> None:
        """Set the pump's running direction."""

    @property
    @abstractmethod
    def running(self) -> bool | None:
        """Return whether the pump is currently running, or None if unknown."""

    @abstractmethod
    def start(self) -> None:
        """Start the pump at its current speed and direction."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the pump."""

    @abstractmethod
    def enter_safe_state(self) -> None:
        """Stop the pump."""
