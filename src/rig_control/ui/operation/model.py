from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import json
from queue import Empty
from traceback import format_exc

from rig_control.data.experiment import ExperimentMetadata
from rig_control.devices.manager import DeviceManager
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, Measurement, Quality
from rig_control.polling import PollingService
from rig_control.control.service import RigControlService
from rig_control.devices.mass_flow_controller import MassFlowController
from rig_control.devices.power_supply import PowerSupply, PowerSupplyOperatingMode
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.lumel_re72 import LumelRe72
from rig_control.devices.temperature_probe import TemperatureProbe
from rig_control.models import DeviceStatus
from rig_control.rig_profile import DeviceCapability, RigProfile
from rig_control.ui.manual_control.model import ManualControlViewModel
from rig_control.ui.manual_control.types import PowerSupplyManualSafety


@dataclass(frozen=True, slots=True)
class OperationActionResult:
    succeeded: bool
    summary: str
    technical_details: str | None = None


@dataclass(frozen=True, slots=True)
class LiveMeasurementRow:
    device_id: str
    channel: str
    value: float
    unit: str
    quality: str
    timestamp: datetime


@dataclass(slots=True)
class _HistoryBucket:
    index: int
    minimum: LiveMeasurementRow
    maximum: LiveMeasurementRow
    latest: LiveMeasurementRow

    def add(self, row: LiveMeasurementRow) -> None:
        if row.value < self.minimum.value:
            self.minimum = row
        if row.value > self.maximum.value:
            self.maximum = row
        self.latest = row

    def representatives(self) -> tuple[LiveMeasurementRow, ...]:
        unique = {
            row.timestamp: row
            for row in (self.minimum, self.maximum, self.latest)
        }
        return tuple(sorted(unique.values(), key=lambda row: row.timestamp))


class _ChannelHistory:
    """Bounded recent and multi-resolution history for one live signal."""

    _LEVELS = ((10, 360), (60, 480), (600, 1_008))
    _OVERVIEW_LIMIT = 500

    def __init__(self, recent_limit: int) -> None:
        self.recent: deque[LiveMeasurementRow] = deque(maxlen=recent_limit)
        self.levels: dict[int, deque[_HistoryBucket]] = {
            seconds: deque(maxlen=count) for seconds, count in self._LEVELS
        }
        self.overview: list[LiveMeasurementRow] = []

    def append(self, row: LiveMeasurementRow) -> None:
        self.recent.append(row)
        timestamp = row.timestamp.timestamp()
        for seconds, buckets in self.levels.items():
            index = int(timestamp // seconds)
            if buckets and buckets[-1].index == index:
                buckets[-1].add(row)
            else:
                buckets.append(_HistoryBucket(index, row, row, row))
        self.overview.append(row)
        if len(self.overview) > self._OVERVIEW_LIMIT * 2:
            self.overview = _condense_rows(self.overview, self._OVERVIEW_LIMIT)

    def recent_rows(self) -> tuple[LiveMeasurementRow, ...]:
        return tuple(self.recent)

    def rows_for_period(
        self,
        seconds: int | None,
        limit: int = 500,
    ) -> tuple[LiveMeasurementRow, ...]:
        if seconds is None:
            return tuple(_condense_rows(self.overview, limit))
        if not self.recent:
            return ()
        latest = self.recent[-1].timestamp.timestamp() if self.recent else 0.0
        cutoff = latest - seconds
        run_start = self.overview[0].timestamp.timestamp()
        required_start = max(cutoff, run_start)

        recent_rows = list(self.recent)
        if recent_rows[0].timestamp.timestamp() <= required_start:
            selected = [
                row
                for row in recent_rows
                if row.timestamp.timestamp() >= cutoff
            ]
            return tuple(_condense_rows(selected, limit))

        for level_seconds, _count in self._LEVELS:
            rows = [
                row
                for bucket in self.levels[level_seconds]
                for row in bucket.representatives()
                if row.timestamp.timestamp() >= cutoff
            ]
            if rows and rows[0].timestamp.timestamp() <= (
                required_start + level_seconds
            ):
                return tuple(_condense_rows(rows, limit))

        return tuple(_condense_rows(self.overview, limit))

    def set_recent_limit(self, limit: int) -> None:
        self.recent = deque(self.recent, maxlen=limit)

    def retained_count(self) -> int:
        return (
            len(self.recent)
            + len(self.overview)
            + sum(len(buckets) for buckets in self.levels.values())
        )


def _condense_rows(
    rows: list[LiveMeasurementRow],
    limit: int,
) -> list[LiveMeasurementRow]:
    """Preserve endpoints and local extrema while bounding display points."""

    if len(rows) <= limit:
        return list(rows)
    interior = rows[1:-1]
    bucket_count = max(1, (limit - 2) // 2)
    bucket_size = max(1, (len(interior) + bucket_count - 1) // bucket_count)
    selected = [rows[0]]
    for start in range(0, len(interior), bucket_size):
        bucket = interior[start : start + bucket_size]
        extrema = {
            min(bucket, key=lambda row: row.value).timestamp:
                min(bucket, key=lambda row: row.value),
            max(bucket, key=lambda row: row.value).timestamp:
                max(bucket, key=lambda row: row.value),
        }
        selected.extend(sorted(extrema.values(), key=lambda row: row.timestamp))
    selected.append(rows[-1])
    if len(selected) > limit:
        step = (len(selected) - 1) / (limit - 1)
        selected = [selected[round(index * step)] for index in range(limit)]
    return selected


@dataclass(frozen=True, slots=True)
class OperationChannelRow:
    """One measured or adjustable value shown on the Operation screen."""

    device_id: str
    device_name: str
    channel: str
    channel_name: str
    value: float | bool | str | None
    unit: str
    quality: str
    timestamp: datetime | None
    system: str
    writable: bool = False
    editor: str = "number"
    minimum: float | None = None
    maximum: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.device_id, self.channel)


class OperationViewModel:
    """GUI-independent coordination for monitoring and recording."""

    def __init__(
        self,
        device_manager: DeviceManager,
        polling_service: PollingService,
        experiment_recorder: ExperimentRecorder,
        control_service: RigControlService,
        *,
        profile_id: str,
        profile: RigProfile | None = None,
        history_limit: int = 120,
        event_limit: int = 1_000,
        power_supply_safety: PowerSupplyManualSafety | None = None,
    ) -> None:
        self._validate_history_limit(history_limit)
        self._validate_event_limit(event_limit)
        self._device_manager = device_manager
        self._polling_service = polling_service
        self._experiment_recorder = experiment_recorder
        self.manual_control = ManualControlViewModel(
            device_manager,
            control_service,
            power_supply_safety=power_supply_safety,
        )
        self._profile_id = profile_id
        self._profile = profile
        self._measurements: dict[
            tuple[str, str], LiveMeasurementRow
        ] = {}
        self._history_limit = history_limit
        self._measurement_histories: dict[
            tuple[str, str], _ChannelHistory
        ] = {}
        self._warnings: dict[str, str] = {}
        self._events: deque[Event] = deque(maxlen=event_limit)

    @property
    def is_monitoring(self) -> bool:
        return self._polling_service.is_running

    @property
    def is_recording(self) -> bool:
        return self._experiment_recorder.is_recording

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    @property
    def history_limit(self) -> int:
        return self._history_limit

    def measurement_rows(self) -> tuple[LiveMeasurementRow, ...]:
        return tuple(
            self._measurements[key]
            for key in sorted(self._measurements)
        )

    def systems(self) -> tuple[str, ...]:
        """Return only systems represented by configured devices."""

        systems = {
            self._system_for_device(device_id)
            for device_id in self._device_manager.device_ids
        }
        preferred = ("Electrical", "Thermal", "Gas flow", "Other")
        return tuple(
            sorted(
                systems,
                key=lambda value: (
                    preferred.index(value) if value in preferred else len(preferred),
                    value.casefold(),
                ),
            )
        )

    def channel_rows(self, system: str | None = None) -> tuple[OperationChannelRow, ...]:
        """Join polled readings and typed controls into unified UI rows."""

        rows: dict[tuple[str, str], OperationChannelRow] = {}
        for reading in self.measurement_rows():
            device = self._device_manager.get(reading.device_id)
            writable = (
                isinstance(device, MassFlowController)
                and reading.channel == "setpoint"
            ) or (
                isinstance(device, LumelRe72)
                and reading.channel == "target_setpoint"
            )
            maximum = None
            channel_name = self._labelled_channel_name(reading.device_id, reading.channel)
            if isinstance(device, MassFlowController) and reading.channel == "setpoint":
                maximum = device.limits.maximum_flow
            row = OperationChannelRow(
                device_id=reading.device_id,
                device_name=self._device_name(reading.device_id),
                channel=reading.channel,
                channel_name=channel_name,
                value=reading.value,
                unit=reading.unit,
                quality=reading.quality,
                timestamp=reading.timestamp,
                system=self._system_for_device(reading.device_id),
                writable=writable,
                minimum=0.0 if writable else None,
                maximum=maximum,
            )
            rows[row.key] = row

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)
            if device.status is DeviceStatus.DISCONNECTED:
                continue
            device_name = self._device_name(device_id)
            device_system = self._system_for_device(device_id)
            supply_mode = (
                self.manual_control.power_supply_mode(device_id)
                if isinstance(device, PowerSupply)
                else None
            )
            for channel, channel_name, unit in self._expected_measurements(
                device_id, device
            ):
                if (device_id, channel) not in rows:
                    rows[(device_id, channel)] = OperationChannelRow(
                        device_id, device_name, channel, channel_name,
                        None,
                        unit, "", None, device_system,
                    )
            if isinstance(device, PowerSupply):
                voltage_name = (
                    "Voltage setpoint"
                    if supply_mode is PowerSupplyOperatingMode.CONSTANT_VOLTAGE
                    else "Voltage limit"
                )
                rows[(device_id, "voltage_setpoint")] = OperationChannelRow(
                    device_id, device_name, "voltage_setpoint", voltage_name,
                    device.voltage_setpoint, "V", "good", None, device_system,
                    True, "number", 0.0, device.limits.maximum_voltage,
                    )
            if isinstance(device, MassFlowController):
                key = (device_id, "setpoint")
                if key not in rows:
                    rows[key] = OperationChannelRow(
                        device_id, device_name, "setpoint", "Flow setpoint",
                        device.flow_setpoint, device.limits.flow_unit, "good", None,
                        device_system, True, "number", 0.0,
                        device.limits.maximum_flow,
                    )
            if isinstance(device, PowerSupply):
                mode = supply_mode
                current_limit_name = (
                    "Current setpoint"
                    if mode is PowerSupplyOperatingMode.CONSTANT_CURRENT
                    else "Current limit"
                )
                rows[(device_id, "current_limit")] = OperationChannelRow(
                    device_id, device_name, "current_limit", current_limit_name,
                    device.current_limit, "A", "good", None, device_system,
                    True, "number", 0.0, device.limits.maximum_current,
                )
                rows[(device_id, "output_enabled")] = OperationChannelRow(
                    device_id, device_name, "output_enabled", "Output",
                    device.output_enabled, "", "good", None, device_system,
                    True, "boolean",
                )
                rows[(device_id, "operating_mode")] = OperationChannelRow(
                    device_id, device_name, "operating_mode", "Operating mode",
                    mode.value, "", "good", None, device_system, True, "mode",
                )
            if isinstance(device, Esp32Controller):
                status = device.controller_status
                rows[(device_id, "watchdog_tripped")] = OperationChannelRow(
                    device_id, device_name, "watchdog_tripped", "Watchdog tripped",
                    status.get("watchdog_tripped") is True, "", "good", None,
                    device_system,
                )
                rows[(device_id, "watchdog_rearm")] = OperationChannelRow(
                    device_id, device_name, "watchdog_rearm", "Watchdog",
                    "Rearm", "", "good", None, device_system, True, "action",
                )
                rows[(device_id, "safe_state_active")] = OperationChannelRow(
                    device_id, device_name, "safe_state_active", "Safe state active",
                    status.get("safe_state_active") is True, "", "good", None,
                    device_system,
                )

        selected = (
            row for row in rows.values()
            if system is None or row.system == system
        )
        return tuple(sorted(selected, key=lambda row: (
            row.system.casefold(), row.device_name.casefold(), row.channel_name.casefold()
        )))

    def apply_channel_value(
        self, device_id: str, channel: str, value: float | bool | str
    ) -> OperationActionResult:
        """Apply one editable channel through existing typed commands."""

        if channel == "setpoint":
            result = self.manual_control.set_mfc_flow(device_id, float(value))
        elif channel == "voltage_setpoint":
            result = self.manual_control.set_power_supply_voltage(device_id, float(value))
        elif channel == "current_limit":
            result = self.manual_control.set_power_supply_current(device_id, float(value))
        elif channel == "output_enabled":
            result = self.manual_control.set_power_supply_output(device_id, bool(value))
        elif channel == "operating_mode":
            result = self.manual_control.set_power_supply_operating_mode(
                device_id, PowerSupplyOperatingMode(str(value))
            )
        elif channel == "watchdog_rearm":
            result = self.manual_control.rearm_controller(device_id)
        elif channel == "target_setpoint":
            result = self.manual_control.set_temperature_setpoint(
                device_id, float(value)
            )
        else:
            return OperationActionResult(False, f"Channel {channel!r} is read-only.")
        return OperationActionResult(
            result.succeeded, result.summary, result.technical_details
        )

    def _device_name(self, device_id: str) -> str:
        if self._profile is None:
            return device_id
        try:
            return self._profile.get_role(device_id).friendly_name
        except KeyError:
            return device_id

    def _expected_measurements(self, device_id: str, device: object) -> tuple[tuple[str, str, str], ...]:
        if isinstance(device, TemperatureProbe):
            return tuple((channel, self._labelled_channel_name(device_id, channel), "degC")
                         for channel in device.channels)
        if isinstance(device, PowerSupply):
            return (("voltage", "Voltage", "V"), ("current", "Current draw", "A"))
        if isinstance(device, MassFlowController):
            return (("mass_flow", "Measured mass flow", device.limits.flow_unit),)
        if self._profile is None:
            return (("measurement", "Measurement", ""),)
        try:
            role = self._profile.get_role(device_id)
        except KeyError:
            return ()
        capability = role.capability
        if capability is DeviceCapability.MASS_FLOW_METER:
            unit = str(role.settings.get("flow_unit", ""))
            return (("mass_flow", "Measured mass flow", unit),)
        if capability is DeviceCapability.TEMPERATURE_SENSOR:
            return (("temperature", "Temperature", ""),)
        if capability is DeviceCapability.HUMIDITY_SENSOR:
            return (("humidity", "Humidity", ""),)
        if capability in {
            DeviceCapability.PRESSURE_SENSOR_ABSOLUTE,
            DeviceCapability.PRESSURE_SENSOR_RELATIVE,
            DeviceCapability.PRESSURE_SENSOR_DIFFERENTIAL,
        }:
            return (("pressure", "Pressure", ""),)
        return ()

    def _system_for_device(self, device_id: str) -> str:
        if self._profile is None:
            return "Other"
        try:
            role = self._profile.get_role(device_id)
        except KeyError:
            return "Other"
        if role.system is not None:
            return role.system.strip()
        capability = role.capability
        if capability in {
            DeviceCapability.DC_POWER_SUPPLY,
            DeviceCapability.POTENTIOSTAT,
        }:
            return "Electrical"
        if capability in {
            DeviceCapability.TEMPERATURE_SENSOR,
            DeviceCapability.HUMIDITY_SENSOR,
            DeviceCapability.TEMPERATURE_CONTROLLER,
        }:
            return "Thermal"
        if capability in {
            DeviceCapability.MASS_FLOW_CONTROLLER,
            DeviceCapability.MASS_FLOW_METER,
            DeviceCapability.PRESSURE_SENSOR_ABSOLUTE,
            DeviceCapability.PRESSURE_SENSOR_RELATIVE,
            DeviceCapability.PRESSURE_SENSOR_DIFFERENTIAL,
        }:
            return "Gas flow"
        return "Other"

    def _labelled_channel_name(self, device_id: str, channel: str) -> str:
        if self._profile is not None:
            try:
                label = self._profile.get_role(device_id).channel_labels.get(channel)
            except KeyError:
                label = None
            if label:
                return f"{label} ({channel})"
        return self._channel_name(channel)

    @staticmethod
    def _channel_name(channel: str) -> str:
        names = {
            "mass_flow": "Mass flow",
            "volumetric_flow": "Volumetric flow",
            "absolute_pressure": "Absolute pressure",
            "gas_temperature": "Gas temperature",
            "totalized_flow": "Totalized flow",
            "setpoint": "Flow setpoint",
            "current": "Current draw",
            "voltage": "Voltage",
        }
        return names.get(channel, channel.replace("_", " ").capitalize())

    def measurement_history(
        self,
        device_id: str,
        channel: str,
    ) -> tuple[LiveMeasurementRow, ...]:
        history = self._measurement_histories.get((device_id, channel))
        return () if history is None else history.recent_rows()

    def measurement_history_for_period(
        self,
        device_id: str,
        channel: str,
        seconds: int | None,
    ) -> tuple[LiveMeasurementRow, ...]:
        """Return at most 500 representative points over a selected period."""

        history = self._measurement_histories.get((device_id, channel))
        return () if history is None else history.rows_for_period(seconds)

    def latest_measurement(
        self,
        device_id: str,
        channel: str,
    ) -> Measurement | None:
        row = self._measurements.get((device_id, channel))
        if row is None:
            return None
        return Measurement(
            value=row.value,
            unit=row.unit,
            timestamp=row.timestamp,
            quality=Quality(row.quality),
        )

    def set_history_limit(self, history_limit: int) -> None:
        """Change retained points while preserving the newest readings."""

        self._validate_history_limit(history_limit)
        if history_limit == self._history_limit:
            return

        self._history_limit = history_limit
        for history in self._measurement_histories.values():
            history.set_recent_limit(history_limit)

    def warnings(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._warnings.items()))

    def diagnostic_metrics(self) -> dict[str, object]:
        """Describe UI-retained state for the runtime health journal."""

        heartbeat_diagnostics = {
            device_id: device.heartbeat_diagnostics()
            for device_id in self._device_manager.device_ids
            if isinstance(
                (device := self._device_manager.get(device_id)),
                Esp32Controller,
            )
        }
        return {
            "latest_measurement_count": len(self._measurements),
            "history_channel_count": len(self._measurement_histories),
            "history_point_count": sum(
                history.retained_count()
                for history in self._measurement_histories.values()
            ),
            "history_limit_per_channel": self._history_limit,
            "warning_count": len(self._warnings),
            "retained_event_count": len(self._events),
            "event_limit": self._events.maxlen,
            "esp32_heartbeats": heartbeat_diagnostics,
        }

    def connect_all(self) -> tuple[OperationActionResult, ...]:
        results: list[OperationActionResult] = []
        for device_id in self._device_manager.device_ids:
            try:
                self._device_manager.connect(device_id)
            except Exception as error:
                results.append(
                    OperationActionResult(
                        False,
                        f"Could not connect {device_id!r}: "
                        f"{type(error).__name__}: {error}",
                        format_exc(),
                    )
                )
                continue
            results.append(
                OperationActionResult(
                    True,
                    f"Connected {device_id!r}.",
                )
            )
        for default_result in self.manual_control.initialize_manual_power_supply_defaults():
            results.append(
                OperationActionResult(
                    default_result.succeeded,
                    default_result.summary,
                    default_result.technical_details,
                )
            )
        return tuple(results)

    def start_monitoring(self) -> OperationActionResult:
        try:
            self._polling_service.start()
        except Exception as error:
            return self._failure("start monitoring", error)
        return OperationActionResult(True, "Background monitoring started.")

    def stop_monitoring(self) -> OperationActionResult:
        if self.is_recording:
            return OperationActionResult(
                False,
                "Stop experiment recording before stopping monitoring.",
            )
        try:
            self._polling_service.stop()
        except Exception as error:
            return self._failure("stop monitoring", error)
        return OperationActionResult(True, "Background monitoring stopped.")

    def start_recording(
        self,
        *,
        experiment_id: str,
        operator: str,
        output_directory: str | Path,
        notes: str | None = None,
    ) -> OperationActionResult:
        if not self.is_monitoring:
            return OperationActionResult(
                False,
                "Start monitoring before starting experiment recording.",
            )
        if not str(output_directory).strip():
            return OperationActionResult(
                False,
                "Choose an experiment output folder before recording.",
            )
        try:
            metadata = ExperimentMetadata(
                experiment_id=experiment_id,
                operator=operator,
                notes=notes or None,
                extra={
                    "rig_profile_id": self._profile_id,
                    **({"channel_labels_json": json.dumps({
                        role.device_id: dict(role.channel_labels)
                        for role in self._profile.enabled_roles if role.channel_labels
                    }, ensure_ascii=False)} if self._profile is not None else {}),
                },
            )
            self._experiment_recorder.start(
                metadata=metadata,
                root_directory=output_directory,
            )
        except Exception as error:
            return self._failure("start experiment recording", error)
        return OperationActionResult(
            True,
            f"Recording experiment {experiment_id!r}.",
        )

    def stop_recording(self) -> OperationActionResult:
        try:
            self._experiment_recorder.stop()
        except Exception as error:
            return self._failure("stop experiment recording", error)
        return OperationActionResult(True, "Experiment recording completed.")

    def collect_polling_results(self) -> int:
        """Drain queued batches; intended for a Tk ``after`` callback."""

        count = 0
        while True:
            try:
                batch = self._polling_service.results.get_nowait()
            except Empty:
                return count

            count += 1
            successful_ids: set[str] = set()
            for record in batch.measurements:
                measurement = record.measurement
                key = (record.device_id, record.channel)
                row = LiveMeasurementRow(
                    device_id=record.device_id,
                    channel=record.channel,
                    value=measurement.value,
                    unit=measurement.unit,
                    quality=measurement.quality.value,
                    timestamp=measurement.timestamp,
                )
                self._measurements[key] = row
                history = self._measurement_histories.setdefault(
                    key,
                    _ChannelHistory(self._history_limit),
                )
                history.append(row)
                successful_ids.add(record.device_id)

            for device_id in successful_ids:
                self._warnings.pop(device_id, None)
            for failure in batch.failures:
                self._warnings[failure.device_id] = (
                    f"{failure.error_type}: {failure.message}"
                )
                for key, row in tuple(self._measurements.items()):
                    if row.device_id == failure.device_id:
                        self._measurements[key] = replace(
                            row,
                            quality=Quality.STALE.value,
                        )
            self._events.extend(batch.events)

    def shutdown(self) -> tuple[str, ...]:
        """Stop recording and polling for this screen.

        Does not disconnect devices - Operation and Diagnostics share one
        device manager, so device disconnection is only ever done centrally
        by ApplicationSession.close() (see HomeViewModel.close()).
        """

        failures: list[str] = []
        if self.is_recording:
            try:
                self._experiment_recorder.stop()
            except Exception as error:
                failures.append(
                    f"Could not stop recording: {type(error).__name__}: {error}"
                )
        if self.is_monitoring:
            try:
                self._polling_service.stop()
            except Exception as error:
                failures.append(
                    f"Could not stop monitoring: {type(error).__name__}: {error}"
                )
        return tuple(failures)

    @staticmethod
    def _failure(operation: str, error: Exception) -> OperationActionResult:
        return OperationActionResult(
            False,
            f"Could not {operation}: {type(error).__name__}: {error}",
            format_exc(),
        )

    @staticmethod
    def _validate_history_limit(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("Trend history limit must be an integer")
        if value < 2:
            raise ValueError("Trend history limit must be at least 2")

    @staticmethod
    def _validate_event_limit(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError("Event history limit must be an integer")
        if value <= 0:
            raise ValueError("Event history limit must be positive")
