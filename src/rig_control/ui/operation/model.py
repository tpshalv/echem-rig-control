from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
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
from rig_control.models import DeviceStatus
from rig_control.rig_profile import DeviceCapability, RigProfile
from rig_control.ui.manual_control.model import ManualControlViewModel


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
    ) -> None:
        self._validate_history_limit(history_limit)
        self._validate_event_limit(event_limit)
        self._device_manager = device_manager
        self._polling_service = polling_service
        self._experiment_recorder = experiment_recorder
        self.manual_control = ManualControlViewModel(
            device_manager,
            control_service,
            measurement_provider=self.latest_measurement,
        )
        self._profile_id = profile_id
        self._profile = profile
        self._measurements: dict[
            tuple[str, str], LiveMeasurementRow
        ] = {}
        self._history_limit = history_limit
        self._measurement_histories: dict[
            tuple[str, str], deque[LiveMeasurementRow]
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
                isinstance(device, PowerSupply) and reading.channel == "voltage"
            ) or (
                isinstance(device, MassFlowController)
                and reading.channel == "setpoint"
            )
            maximum = None
            if isinstance(device, PowerSupply) and reading.channel == "voltage":
                maximum = device.limits.maximum_voltage
            elif isinstance(device, MassFlowController) and reading.channel == "setpoint":
                maximum = device.limits.maximum_flow
            row = OperationChannelRow(
                device_id=reading.device_id,
                device_name=self._device_name(reading.device_id),
                channel=reading.channel,
                channel_name=self._channel_name(reading.channel),
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
            for channel, channel_name, unit in self._expected_measurements(
                device_id, device
            ):
                if (device_id, channel) not in rows:
                    rows[(device_id, channel)] = OperationChannelRow(
                        device_id, device_name, channel, channel_name,
                        None, unit, "", None, device_system,
                        isinstance(device, PowerSupply) and channel == "voltage",
                        "number", 0.0 if isinstance(device, PowerSupply) and channel == "voltage" else None,
                        device.limits.maximum_voltage if isinstance(device, PowerSupply) and channel == "voltage" else None,
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
                rows[(device_id, "current_limit")] = OperationChannelRow(
                    device_id, device_name, "current_limit", "Current limit",
                    device.current_limit, "A", "good", None, device_system,
                    True, "number", 0.0, device.limits.maximum_current,
                )
                rows[(device_id, "output_enabled")] = OperationChannelRow(
                    device_id, device_name, "output_enabled", "Output",
                    device.output_enabled, "", "good", None, device_system,
                    True, "boolean",
                )
                mode = self.manual_control.power_supply_mode(device_id)
                rows[(device_id, "operating_mode")] = OperationChannelRow(
                    device_id, device_name, "operating_mode", "Operating mode",
                    mode.value, "", "good", None, device_system, True, "mode",
                )
            if isinstance(device, Esp32Controller):
                status = device.controller_status
                outputs = status.get("outputs", {})
                led_enabled = bool(outputs.get("led", False)) if isinstance(outputs, dict) else False
                rows[(device_id, "controller_led")] = OperationChannelRow(
                    device_id, device_name, "controller_led", "Controller LED",
                    led_enabled, "", "good", None, device_system, True, "boolean",
                )
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
        elif channel == "voltage":
            result = self.manual_control.set_power_supply_voltage(device_id, float(value))
        elif channel == "current_limit":
            result = self.manual_control.set_power_supply_current(device_id, float(value))
        elif channel == "output_enabled":
            result = self.manual_control.set_power_supply_output(device_id, bool(value))
        elif channel == "operating_mode":
            result = self.manual_control.set_power_supply_operating_mode(
                device_id, PowerSupplyOperatingMode(str(value))
            )
        elif channel == "controller_led":
            result = self.manual_control.set_controller_output(
                device_id, "led", bool(value)
            )
        elif channel == "watchdog_rearm":
            result = self.manual_control.rearm_controller(device_id)
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
        return tuple(
            self._measurement_histories.get((device_id, channel), ())
        )

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
        self._measurement_histories = {
            key: deque(history, maxlen=history_limit)
            for key, history in self._measurement_histories.items()
        }

    def warnings(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._warnings.items()))

    def diagnostic_metrics(self) -> dict[str, object]:
        """Describe UI-retained state for the runtime health journal."""

        return {
            "latest_measurement_count": len(self._measurements),
            "history_channel_count": len(self._measurement_histories),
            "history_point_count": sum(
                len(history) for history in self._measurement_histories.values()
            ),
            "history_limit_per_channel": self._history_limit,
            "warning_count": len(self._warnings),
            "retained_event_count": len(self._events),
            "event_limit": self._events.maxlen,
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
        sample_interval_seconds: float,
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
                extra={"rig_profile_id": self._profile_id},
            )
            self._experiment_recorder.start(
                metadata=metadata,
                root_directory=output_directory,
                sample_interval_seconds=sample_interval_seconds,
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
                    deque(maxlen=self._history_limit),
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
        """Stop recording/polling and disconnect all devices."""

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
