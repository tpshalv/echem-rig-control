from abc import abstractmethod

from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.sensor import Sensor
from rig_control.models import Measurement


class TemperatureProbe(Sensor):
    """Generic temperature instrument with stable, independently named channels."""

    @property
    @abstractmethod
    def channels(self) -> tuple[str, ...]:
        """Configured channel IDs, independent of hardware branding or labels."""

    @abstractmethod
    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        """Return temperature measurements in degC for configured channels."""

    def read_measurement(self) -> Measurement:
        """Single-sensor compatibility: read the first configured channel."""
        return self.read_measurements()[0].measurement
