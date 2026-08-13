from abc import ABC, abstractmethod

from rig_control.models import DeviceStatus


class Device(ABC):
    """Common interface implemented by every hardware device."""

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Return the device's unique configured identifier."""

    @property
    @abstractmethod
    def status(self) -> DeviceStatus:
        """Return the device's current status."""

    @abstractmethod
    def connect(self) -> None:
        """Connect to the device and establish its state."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the device cleanly."""