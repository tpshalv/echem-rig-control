from dataclasses import dataclass
from traceback import format_exc

from rig_control.devices.manager import DeviceManager


@dataclass(frozen=True, slots=True)
class DiagnosticDeviceRow:
    """One device row displayed by the diagnostic screen."""

    device_id: str
    device_type: str
    status: str


@dataclass(frozen=True, slots=True)
class DiagnosticActionResult:
    """User-readable result of a diagnostic action."""

    succeeded: bool
    summary: str
    technical_details: str | None = None


class DiagnosticViewModel:
    """GUI-independent logic for the device diagnostic screen."""

    def __init__(self, device_manager: DeviceManager) -> None:
        self._device_manager = device_manager

    def device_rows(self) -> tuple[DiagnosticDeviceRow, ...]:
        """Return current device information for display."""

        return tuple(
            DiagnosticDeviceRow(
                device_id=summary.device_id,
                device_type=summary.device_type,
                status=summary.status.value,
            )
            for summary in self._device_manager.summaries()
        )

    def connect_device(
        self,
        device_id: str,
    ) -> DiagnosticActionResult:
        """Attempt to connect a device without leaking exceptions to UI."""

        try:
            self._device_manager.connect(device_id)
        except Exception as error:
            return self._failure_result(
                operation="connect",
                device_id=device_id,
                error=error,
            )

        return DiagnosticActionResult(
            succeeded=True,
            summary=(
                f"Device {device_id!r} connected successfully."
            ),
        )

    def disconnect_device(
        self,
        device_id: str,
    ) -> DiagnosticActionResult:
        """Attempt to disconnect a device without crashing the UI."""

        try:
            self._device_manager.disconnect(device_id)
        except Exception as error:
            return self._failure_result(
                operation="disconnect",
                device_id=device_id,
                error=error,
            )

        return DiagnosticActionResult(
            succeeded=True,
            summary=(
                f"Device {device_id!r} disconnected successfully."
            ),
        )

    @staticmethod
    def _failure_result(
        *,
        operation: str,
        device_id: str,
        error: Exception,
    ) -> DiagnosticActionResult:
        return DiagnosticActionResult(
            succeeded=False,
            summary=(
                f"Could not {operation} device {device_id!r}. "
                f"{type(error).__name__}: {error}"
            ),
            technical_details=format_exc(),
        )