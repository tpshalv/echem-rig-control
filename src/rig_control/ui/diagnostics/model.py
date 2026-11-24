from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from statistics import fmean
from time import perf_counter
from traceback import format_exc

from rig_control.devices.measurement_source import MeasurementSource
from rig_control.devices.pump import Pump
from rig_control.diagnostics.device_dump import (
    dump_device,
    dump_unavailable_reason,
    write_dump,
)
from rig_control.devices.manager import DeviceManager
from rig_control.models import DeviceStatus
from rig_control.runtime_diagnostics import RuntimeDiagnostics, RuntimeHealthPoint


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

    def __init__(
        self,
        device_manager: DeviceManager,
        runtime_diagnostics: RuntimeDiagnostics | None = None,
    ) -> None:
        self._device_manager = device_manager
        self._runtime_diagnostics = runtime_diagnostics

    def runtime_health_history(self) -> tuple[RuntimeHealthPoint, ...]:
        if self._runtime_diagnostics is None:
            return ()
        return self._runtime_diagnostics.health_history()

    def runtime_health_overview(self) -> tuple[RuntimeHealthPoint, ...]:
        if self._runtime_diagnostics is None:
            return ()
        return self._runtime_diagnostics.overview_health_history()

    def latest_runtime_health(self) -> RuntimeHealthPoint | None:
        if self._runtime_diagnostics is None:
            return None
        return self._runtime_diagnostics.latest_health_point()

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

    def dump_unavailable_reason(self, device_id: str) -> str:
        """Return why this device's settings cannot be dumped, or "".

        The screen uses this to disable the option and say what is missing,
        rather than offering something that would fail.
        """

        try:
            device = self._device_manager.get(device_id)
        except Exception:
            return "Select a device."
        return dump_unavailable_reason(device)

    def dump_device_settings(self, device_id: str) -> DiagnosticActionResult:
        """Save a read-only record of one device's settings."""

        try:
            device = self._device_manager.get(device_id)
            device_type = next(
                (
                    summary.device_type
                    for summary in self._device_manager.summaries()
                    if summary.device_id == device_id
                ),
                "",
            )
            # Take the bus the same way every other diagnostic action does,
            # so a dump never interleaves with a polling read.
            with self._device_manager.operation(device_id):
                dump = dump_device(device, device_type)
            path = write_dump(dump)
        except Exception as error:
            return self._failure_result(
                operation="dump settings for",
                device_id=device_id,
                error=error,
            )

        answered = len(dump.queries) - len(dump.failures)
        summary = [
            f"Saved a settings dump for {device_id!r}: {answered} of "
            f"{len(dump.queries)} queries answered.",
            f"File: {path}",
        ]
        if dump.failures:
            summary.append(
                "Unanswered: "
                + ", ".join(query.command for query in dump.failures)
            )
        return DiagnosticActionResult(
            succeeded=True,
            summary="\n".join(summary),
            technical_details=dump.to_text(),
        )

    def connect_device(
        self,
        device_id: str,
    ) -> DiagnosticActionResult:
        """Connect a device, or reconnect it if it faulted.

        A faulted driver keeps its port open but refuses commands, so a plain
        connect would only say it is already connected; it is cycled instead
        (disconnecting applies its safe state).  The window asks first.
        """

        status = DeviceStatus.DISCONNECTED
        try:
            status = self._device_manager.get(device_id).status
            if status not in (DeviceStatus.DISCONNECTED, DeviceStatus.FAULTED):
                return DiagnosticActionResult(
                    succeeded=True, summary=f"Device {device_id!r} is already connected.")
            if status is DeviceStatus.FAULTED:
                self._device_manager.reconnect(device_id)
            else:
                self._device_manager.connect(device_id)
        except Exception as error:
            return self._failure_result(
                operation="reconnect" if status is DeviceStatus.FAULTED else "connect",
                device_id=device_id,
                error=error,
            )

        return DiagnosticActionResult(
            succeeded=True,
            summary=(
                f"Device {device_id!r} "
                f"{'reconnected' if status is DeviceStatus.FAULTED else 'connected'} successfully."
            ),
        )

    def faulted_devices(self, device_ids: Sequence[str] | None = None) -> tuple[str, ...]:
        """Which of these devices (default: all) a connect would cycle."""

        return tuple(
            device_id for device_id in (device_ids or self._device_manager.device_ids)
            if self._device_manager.get(device_id).status is DeviceStatus.FAULTED
        )

    def connect_all(self, *, reconnect_faulted: bool) -> DiagnosticActionResult:
        """Connect every device that is not connected; faulted ones only if agreed."""

        done: list[str] = []
        problems: list[str] = []
        details: list[str] = []
        for device_id in self._device_manager.device_ids:
            status = self._device_manager.get(device_id).status
            if status not in (DeviceStatus.DISCONNECTED, DeviceStatus.FAULTED):
                continue
            if status is DeviceStatus.FAULTED and not reconnect_faulted:
                problems.append(f"{device_id}: faulted, left as it is")
                continue
            result = self.connect_device(device_id)
            if result.succeeded:
                done.append(device_id)
            else:
                problems.append(result.summary)
                if result.technical_details:
                    details.append(result.technical_details)
        lines = [f"Connected: {', '.join(done)}." if done else "Nothing needed connecting."]
        if problems:
            lines += ["", "Problems:"] + [f"• {problem}" for problem in problems]
        return DiagnosticActionResult(
            succeeded=not problems,
            summary="\n".join(lines),
            technical_details="\n\n".join(details) or None,
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

    def test_communication(
        self,
        device_id: str,
        *,
        attempts: int = 10,
    ) -> DiagnosticActionResult:
        """Measure safe, end-to-end request latency for real hardware."""

        if attempts <= 0:
            raise ValueError("Communication-test attempts must be positive")

        try:
            device = self._device_manager.get(device_id)
            if "simulated" in type(device).__module__.lower():
                raise TypeError(
                    "Communication timing is only available for real devices"
                )
            if device.status not in (DeviceStatus.CONNECTED, DeviceStatus.READY):
                raise RuntimeError("Connect the device before testing communication")

            # A pump's readings are its cached setpoint and never touch the
            # bus, so timing them would report a meaningless ~0 ms.
            if isinstance(device, MeasurementSource) and not isinstance(device, Pump):
                probe_name = "read_measurements"
                probe = device.read_measurements
            else:
                probe = getattr(device, "refresh_status", None)
                probe_name = "refresh_status"
                if not callable(probe):
                    raise TypeError(
                        "This device has no non-destructive communication probe"
                    )
        except Exception as error:
            return self._failure_result(
                operation="test communication with",
                device_id=device_id,
                error=error,
            )

        timings_ms: list[float] = []
        failures: list[str] = []
        consecutive_failures = 0
        for attempt in range(1, attempts + 1):
            started = perf_counter()
            try:
                with self._device_manager.operation(device_id):
                    probe()
            except Exception as error:
                failures.append(
                    f"Attempt {attempt}: {type(error).__name__}: {error}"
                )
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break
            else:
                timings_ms.append((perf_counter() - started) * 1000.0)
                consecutive_failures = 0

        attempted = len(timings_ms) + len(failures)
        lines = [
            f"Communication test for {device_id!r}: "
            f"{len(timings_ms)}/{attempted} requests succeeded.",
            f"Probe: {probe_name} (driver-to-device round trip).",
        ]
        if timings_ms:
            ordered = sorted(timings_ms)
            percentile_95 = ordered[ceil(0.95 * len(ordered)) - 1]
            lines.append(
                "Latency: "
                f"min {ordered[0]:.1f} ms, "
                f"average {fmean(ordered):.1f} ms, "
                f"p95 {percentile_95:.1f} ms, "
                f"max {ordered[-1]:.1f} ms."
            )
        if attempted < attempts:
            lines.append("Stopped after 3 consecutive failures.")
        if failures:
            lines.append(f"Failures: {len(failures)}.")

        return DiagnosticActionResult(
            succeeded=not failures,
            summary="\n".join(lines),
            technical_details="\n".join(failures) if failures else None,
        )
