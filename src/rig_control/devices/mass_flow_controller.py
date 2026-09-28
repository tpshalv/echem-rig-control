from abc import abstractmethod
from dataclasses import dataclass
from math import isfinite

from rig_control.devices.base import Device
from rig_control.devices.measurement_source import (
    DeviceMeasurement,
    MeasurementSource,
)
from rig_control.models import Measurement


def mass_flow_unit_factor(unit: str) -> float:
    """Return how many SCCM one of this unit is.

    Only units this software can convert exactly are listed. Anything else
    raises, so a flow is never sent to an instrument under a unit that was
    assumed rather than known.
    """

    factors = {"sccm": 1.0, "slpm": 1000.0}
    try:
        return factors[unit.strip().casefold()]
    except (AttributeError, KeyError) as error:
        raise ValueError(f"Unsupported mass-flow unit {unit!r}") from error


def convert_mass_flow(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a mass-flow rate between two known units."""

    if from_unit.strip().casefold() == to_unit.strip().casefold():
        return float(value)
    return float(value) * mass_flow_unit_factor(from_unit) / mass_flow_unit_factor(to_unit)


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


class MassFlowController(Device, MeasurementSource):
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
    def set_flow_setpoint(self, flow: float, unit: str = "") -> None:
        """Set the requested mass-flow setpoint.

        The unit travels with the value: an empty unit means the device's own
        configured unit, and any other unit is converted before use. A unit
        that cannot be converted is refused rather than assumed, so a value
        entered as SCCM can never be acted on as SLPM.
        """

    @abstractmethod
    def measure_flow(self) -> Measurement:
        """Return the measured mass flow."""

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        return (DeviceMeasurement("mass_flow", self.measure_flow()),)

    @abstractmethod
    def enter_safe_state(self) -> None:
        """Request the configured safe state."""
