from abc import ABC, abstractmethod

from rig_control.models import DeviceStatus


class Device(ABC):
    """Common interface implemented by every hardware device."""

    # True only where disconnect() and connect() merely release and re-read
    # the instrument, so polling may cycle them unattended after a FAULTED
    # read. Drivers whose disconnect applies a safe state (pump stop, output
    # off) must keep this False: an automatic retry would change the rig.
    passive_reconnect: bool = False

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