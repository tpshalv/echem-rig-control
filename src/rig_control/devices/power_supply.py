from abc import abstractmethod
from dataclasses import dataclass

from rig_control.devices.base import Device
from rig_control.models import Measurement


@dataclass(frozen=True, slots=True)
class PowerSupplyLimits:
    """Hard configuration limits applied by the PC software."""

    maximum_voltage: float
    maximum_current: float
    maximum_power: float

    def __post_init__(self) -> None:
        if self.maximum_voltage <= 0:
            raise ValueError("Maximum voltage must be greater than zero")

        if self.maximum_current <= 0:
            raise ValueError("Maximum current must be greater than zero")

        if self.maximum_power <= 0:
            raise ValueError("Maximum power must be greater than zero")


class PowerSupply(Device):
    """Common capability required from a programmable DC supply."""

    @property
    @abstractmethod
    def limits(self) -> PowerSupplyLimits:
        """Return the software-configured operating limits."""

    @property
    @abstractmethod
    def voltage_setpoint(self) -> float:
        """Return the configured voltage setpoint in volts."""

    @property
    @abstractmethod
    def current_limit(self) -> float:
        """Return the configured current limit in amperes."""

    @property
    @abstractmethod
    def output_enabled(self) -> bool:
        """Return whether output is reported as enabled."""

    @abstractmethod
    def set_voltage(self, voltage: float) -> None:
        """Set the requested voltage in volts."""

    @abstractmethod
    def set_current_limit(self, current: float) -> None:
        """Set the requested current limit in amperes."""

    @abstractmethod
    def set_output_enabled(self, enabled: bool) -> None:
        """Enable or disable the DC output."""

    @abstractmethod
    def measure_voltage(self) -> Measurement:
        """Return measured output voltage."""

    @abstractmethod
    def measure_current(self) -> Measurement:
        """Return measured output current."""

    @abstractmethod
    def enter_safe_state(self) -> None:
        """Immediately request the configured safe state."""