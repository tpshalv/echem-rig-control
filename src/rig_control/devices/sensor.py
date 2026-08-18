from abc import abstractmethod

from rig_control.devices.base import Device
from rig_control.devices.measurement_source import (
    DeviceMeasurement,
    MeasurementSource,
)
from rig_control.models import Measurement


class Sensor(Device, MeasurementSource):
    """A device capable of producing measurements."""

    @abstractmethod
    def read_measurement(self) -> Measurement:
        """Read and return the latest measurement."""

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        return (DeviceMeasurement("measurement", self.read_measurement()),)
