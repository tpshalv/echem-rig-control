from abc import abstractmethod
from dataclasses import dataclass
from math import isfinite

from rig_control.devices.base import Device
from rig_control.models import Measurement


@dataclass(frozen=True, slots=True)
class MassFlowControllerLimits:
    """Configured operating limits for one mass flow controller."""

    maximum_flow: float
    flow_unit: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.maximum_flow, bool)
            or not isinstance(self.maximum_flow, (int, float))
        ):
            raise TypeError("Maximum flow must be an int or float")

        if not isfinite(float(self.maximum_flow)):
            raise ValueError("Maximum flow must be finite")

        if self.maximum_flow <= 0:
            raise ValueError("Maximum flow must be greater than zero")

        if (
            not isinstance(self.flow_unit, str)
            or not self.flow_unit.strip()
        ):
            raise ValueError("Flow unit cannot be empty")


class MassFlowController(Device):
    """Common capability required from a mass flow controller."""

    @property
    @abstractmethod
    def limits(self) -> MassFlowControllerLimits:
        """Return the software-configured flow limits."""

    @property
    @abstractmethod
    def flow_setpoint(self) -> float:
        """Return the requested flow setpoint."""

    @abstractmethod
    def set_flow_setpoint(self, flow: float) -> None:
        """Set the requested mass-flow setpoint."""

    @abstractmethod
    def measure_flow(self) -> Measurement:
        """Return the measured mass flow."""

    @abstractmethod
    def enter_safe_state(self) -> None:
        """Request the configured safe state."""