from rig_control.devices.sensor import Sensor
from rig_control.devices.simulated import SimulatedDevice
from rig_control.models import DeviceStatus, Measurement


class SimulatedSensor(SimulatedDevice, Sensor):
    """Controllable sensor used in tests and software development."""

    def __init__(self, device_id: str, value: float, unit: str) -> None:
        super().__init__(device_id)
        self._value = value
        self._unit = unit

    def set_value(self, value: float) -> None:
        """Change the value returned by subsequent simulated readings."""
        self._value = value

    def read_measurement(self) -> Measurement:
        if self.status is not DeviceStatus.READY:
            raise RuntimeError(f"Device {self.device_id!r} is not ready")

        return Measurement(value=self._value, unit=self._unit)