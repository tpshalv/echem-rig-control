from dataclasses import dataclass

from rig_control.devices.base import Device
from rig_control.models import DeviceStatus


class DeviceOperationError(RuntimeError):
    """A named device operation failed."""


@dataclass(frozen=True, slots=True)
class DeviceSummary:
    """Small status record suitable for diagnostics and the UI."""

    device_id: str
    device_type: str
    status: DeviceStatus


class DeviceManager:
    """Registry and control point for all logical rig devices."""

    def __init__(self) -> None:
        self._devices: dict[str, Device] = {}

    @property
    def device_ids(self) -> tuple[str, ...]:
        """Return registered IDs in registration order."""

        return tuple(self._devices)

    def register(self, device: Device) -> None:
        """Register one logical device."""

        if not isinstance(device, Device):
            raise TypeError(
                "Registered object must implement the Device interface"
            )

        if device.device_id in self._devices:
            raise ValueError(
                f"Device ID {device.device_id!r} is already registered"
            )

        self._devices[device.device_id] = device

    def get(self, device_id: str) -> Device:
        """Return a device by its stable ID."""

        try:
            return self._devices[device_id]
        except KeyError as error:
            available = ", ".join(self.device_ids) or "none"

            raise KeyError(
                f"Unknown device ID {device_id!r}. "
                f"Registered device IDs: {available}"
            ) from error

    def summaries(self) -> tuple[DeviceSummary, ...]:
        """Return current status information for every device."""

        return tuple(
            DeviceSummary(
                device_id=device.device_id,
                device_type=type(device).__name__,
                status=device.status,
            )
            for device in self._devices.values()
        )

    def connect(self, device_id: str) -> None:
        """Connect one named device with detailed error context."""

        device = self.get(device_id)

        try:
            device.connect()
        except Exception as error:
            raise DeviceOperationError(
                f"Could not connect device {device_id!r} "
                f"({type(device).__name__}): "
                f"{type(error).__name__}: {error}"
            ) from error

    def disconnect(self, device_id: str) -> None:
        """Disconnect one named device with detailed error context."""

        device = self.get(device_id)

        try:
            device.disconnect()
        except Exception as error:
            raise DeviceOperationError(
                f"Could not disconnect device {device_id!r} "
                f"({type(device).__name__}): "
                f"{type(error).__name__}: {error}"
            ) from error

    def disconnect_all(self) -> tuple[DeviceOperationError, ...]:
        """Disconnect every device, continuing after individual failures."""

        failures: list[DeviceOperationError] = []

        # Reverse order is useful when devices are later registered
        # after the shared services on which they depend.
        for device_id in reversed(self.device_ids):
            try:
                self.disconnect(device_id)
            except DeviceOperationError as error:
                failures.append(error)

        return tuple(failures)