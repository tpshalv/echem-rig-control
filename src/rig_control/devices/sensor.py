from abc import abstractmethod

from rig_control.devices.base import Device
from rig_control.models import Measurement


class Sensor(Device):
    """A device capable of producing measurements."""

    @abstractmethod
    def read_measurement(self) -> Measurement:
        """Read and return the latest measurement."""