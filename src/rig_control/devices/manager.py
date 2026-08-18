from dataclasses import dataclass
from contextlib import contextmanager
from collections.abc import Iterator
from threading import RLock
from traceback import format_exc

from rig_control.devices.base import Device
from rig_control.models import DeviceStatus, Event, EventSeverity, EventSink


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

    def __init__(self, event_sink: EventSink | None = None) -> None:
        self._devices: dict[str, Device] = {}
        self._operation_locks: dict[str, RLock] = {}
        self._event_sink = event_sink
        self._event_sink_failures: list[str] = []

    @property
    def device_ids(self) -> tuple[str, ...]:
        """Return registered IDs in registration order."""

        return tuple(self._devices)

    @property
    def event_sink_failures(self) -> tuple[str, ...]:
        """Return logging failures without masking device operations."""

        return tuple(self._event_sink_failures)

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
        self._operation_locks[device.device_id] = RLock()

    @contextmanager
    def operation(self, device_id: str) -> Iterator[Device]:
        """Serialize communication with one device and yield it."""

        device = self.get(device_id)
        lock = self._operation_locks[device_id]
        with lock:
            yield device

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
            with self.operation(device_id):
                device.connect()
        except Exception as error:
            self._emit(
                Event(
                    source=device_id,
                    severity=EventSeverity.ERROR,
                    message=(
                        "Device connection failed: "
                        f"{type(error).__name__}: {error}"
                    ),
                ),
                format_exc(),
            )
            raise DeviceOperationError(
                f"Could not connect device {device_id!r} "
                f"({type(device).__name__}): "
                f"{type(error).__name__}: {error}"
            ) from error

        self._emit(
            Event(
                source=device_id,
                message="Device connected successfully.",
            )
        )

    def disconnect(self, device_id: str) -> None:
        """Disconnect one named device with detailed error context."""

        device = self.get(device_id)

        try:
            with self.operation(device_id):
                device.disconnect()
        except Exception as error:
            self._emit(
                Event(
                    source=device_id,
                    severity=EventSeverity.ERROR,
                    message=(
                        "Device disconnection failed: "
                        f"{type(error).__name__}: {error}"
                    ),
                ),
                format_exc(),
            )
            raise DeviceOperationError(
                f"Could not disconnect device {device_id!r} "
                f"({type(device).__name__}): "
                f"{type(error).__name__}: {error}"
            ) from error

        self._emit(
            Event(
                source=device_id,
                message="Device disconnected successfully.",
            )
        )

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

    def _emit(
        self,
        event: Event,
        technical_details: str | None = None,
    ) -> None:
        if self._event_sink is not None:
            try:
                self._event_sink(event, technical_details)
            except Exception as error:
                self._event_sink_failures.append(
                    "Could not record device event for "
                    f"{event.source!r}: {type(error).__name__}: {error}"
                )
