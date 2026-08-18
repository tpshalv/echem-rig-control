from abc import ABC, abstractmethod
from dataclasses import dataclass

from rig_control.models import Measurement


@dataclass(frozen=True, slots=True)
class DeviceMeasurement:
    """One named measurement returned during a device poll."""

    channel: str
    measurement: Measurement

    def __post_init__(self) -> None:
        if not isinstance(self.channel, str) or not self.channel.strip():
            raise ValueError("Measurement channel cannot be empty")
        if not isinstance(self.measurement, Measurement):
            raise TypeError("Device measurement must contain a Measurement")


class MeasurementSource(ABC):
    """Capability implemented by devices that can be polled."""

    @abstractmethod
    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        """Read the device once and return its named numeric channels."""
