from rig_control.devices.base import Device
from rig_control.models import DeviceStatus


class SimulatedDevice(Device):
    """Simple device used for development without physical hardware."""

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id
        self._status = DeviceStatus.DISCONNECTED

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    def connect(self) -> None:
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        self._status = DeviceStatus.DISCONNECTED