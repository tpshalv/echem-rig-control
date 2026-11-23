import json
from pathlib import Path

from rig_control.app_settings import AppSettings
from rig_control.display_units import EXPORT_UNITS_KEY
from rig_control.supply_limits import SupplyLimitGuard, instrument_maximums
from rig_control.calibrated_channels import CalibratedChannels
from rig_control.cell_area import CellArea
from rig_control.control.service import RigControlService
from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.run_context import capture_run_context
from rig_control.device_factory import create_device_manager
from rig_control.devices.manager import DeviceManager
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.event_logging import TechnicalEventLogger
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event
from rig_control.polling import PollingService
from rig_control.rig_profile import RigProfile
from rig_control.runtime_diagnostics import RuntimeDiagnostics
from rig_control.recipes.execution import RecipeRunner
from rig_control.flow.calibration import LeakCheckStore, load_preferences, preferences_path
from rig_control.flow.indicator import ExcessFlowIndicator
from rig_control.flow.leak_check import LeakCheckRunner


class ApplicationSession:
    """Own all shared runtime objects for one selected rig profile."""

    def __init__(
        self,
        profile: RigProfile,
        settings: AppSettings,
        *,
        settings_directory: str | Path = ".",
    ) -> None:
        self.profile = profile
        self.settings = settings
        log_path = Path(settings.technical_log_path)
        if not log_path.is_absolute():
            log_path = Path(settings_directory) / log_path
        self.technical_log = TechnicalEventLogger(log_path)
        self.technical_log.record(
            Event(source="application", message="Application session started.")
        )
        self.device_manager: DeviceManager = create_device_manager(
            profile,
            event_sink=self.record_control_event,
            pressure_policy=settings.pressure_policy,
            keithley_2280s_nplc=settings.keithley_2280s_nplc,
        )
        self.control_service = RigControlService(
            self.device_manager, event_sink=self.record_control_event,
        )
        self.experiment_recorder = ExperimentRecorder(
            context_provider=self._recording_context,
            start_guard=lambda: self.excess_flow.lock,
            writer_factory=lambda root_directory: DirectoryExperimentWriter(
                root_directory,
                export_bin_seconds=settings.export_bin_seconds,
                live_export_interval_seconds=(
                    settings.live_export_interval_seconds
                ),
            )
        )
        self.cell_area = CellArea(is_locked=lambda: self.experiment_recorder.is_recording)
        self.calibrated_channels = CalibratedChannels(
            is_recording=lambda: self.experiment_recorder.is_recording,
            cell_area=self.cell_area,
        )
        #: Every current and voltage sent to a supply must pass these (decisions/0028).
        self.supply_limits = settings.supply_limits
        self.control_service.limit_guard = SupplyLimitGuard(
            self.supply_limits, lambda: self.cell_area.cm2, instrument_maximums(self.device_manager))
        self.recipe_runner = RecipeRunner(
            self.control_service,
            self.device_manager,
            supply_limits=self.supply_limits,
            recorder=self.experiment_recorder,
            event_sink=lambda event: self.record_control_event(event),
            pump_calibrations=self.calibrated_channels.pumps,
            cell_area=self.cell_area,
        )
        leak_check_store = LeakCheckStore()
        self.excess_flow = ExcessFlowIndicator(self.device_manager, profile.profile_id, store=leak_check_store,
            is_recording=lambda: self.experiment_recorder.is_recording,
            is_paused=lambda: self.leak_check.is_running,
            supply_id=load_preferences(preferences_path(profile.profile_id)).get("supply_id", ""))
        self.polling_service = PollingService(
            self.device_manager,
            publish_interval_seconds=settings.publish_interval_seconds,
            device_intervals_seconds={
                role.device_id: role.poll_interval_seconds
                for role in profile.enabled_roles
                if role.poll_interval_seconds is not None
            },
            batch_handler=self.experiment_recorder.record_batch,
            event_sink=self.technical_log.record,
            derived_readings=self.calibrated_channels.add_to,
            batch_transform=self._process_flow_batch,
            reconnect_delay_seconds=settings.device_reconnect_interval_seconds,
            reconnect_attempts=settings.device_reconnect_attempts,
        )
        self.leak_check = LeakCheckRunner(self.device_manager, self.control_service, leak_check_store, profile,
            monitoring=lambda: self.polling_service.is_running,
            event_sink=lambda event: self.record_control_event(event))
        diagnostic_path = log_path.with_name("runtime-health.jsonl")
        self.runtime_diagnostics = RuntimeDiagnostics(
            diagnostic_path,
            metric_providers={
                "polling": self.polling_service.diagnostic_metrics,
                "recorder": self.experiment_recorder.diagnostic_metrics,
                "esp32_heartbeats": self._esp32_heartbeat_metrics,
            },
            event_sink=self.technical_log.record,
        )
        self.runtime_diagnostics.start()
        self._closed = False

    def _process_flow_batch(self, batch):
        self.leak_check.observe(batch)
        return self.excess_flow.process(batch)

    def _recording_context(self):
        return {**capture_run_context(self.profile, self.settings, self.device_manager),
                **self.calibrated_channels.snapshot(self.device_manager),
                **self.excess_flow.snapshot(),
                **self.cell_area.snapshot(),
                # Fixed at the start, so a run's CSV and Excel never mix units.
                EXPORT_UNITS_KEY: json.dumps(self.settings.display_units.as_dict(), ensure_ascii=False)}

    def record_control_event(self, event: Event, details: str | None = None) -> None:
        """Send commands to both the technical log and the active run journal."""
        failures = []
        for record in (
            lambda: self.technical_log.record(event, details),
            lambda: self.experiment_recorder.record_event(event),
        ):
            try:
                record()
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError("; ".join(failures))

    def _esp32_heartbeat_metrics(self) -> dict[str, object]:
        return {
            device_id: device.heartbeat_diagnostics()
            for device_id in self.device_manager.device_ids
            if isinstance(
                (device := self.device_manager.get(device_id)),
                Esp32Controller,
            )
        }

    @property
    def is_closed(self) -> bool:
        return self._closed

    def stop_feature_services(self) -> tuple[str, ...]:
        failures: list[str] = []
        if self.leak_check.is_running:
            self.leak_check.stop()
            if not self.leak_check.join(10):
                self.control_service.enter_global_safe_state()
                failures.append("Leak check did not stop within 10 seconds; safe state requested")
        if self.recipe_runner.is_running:
            self.recipe_runner.stop()
            result = self.recipe_runner.join(10.0)
            if result is None:
                failures.append("Recipe did not stop within 10 seconds")
        if self.experiment_recorder.is_recording:
            try:
                self.experiment_recorder.stop()
            except Exception as error:
                failures.append(f"Could not stop recording: {error}")
        if self.polling_service.is_running:
            try:
                self.polling_service.stop()
            except Exception as error:
                failures.append(f"Could not stop polling: {error}")
        return tuple(failures)

    def close(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        failures = list(self.stop_feature_services())
        failures.extend(str(error) for error in self.device_manager.disconnect_all())
        try:
            self.runtime_diagnostics.stop()
        except Exception as error:
            failures.append(f"Could not stop runtime diagnostics: {error}")
        self.technical_log.record(
            Event(
                source="application",
                message=(
                    "Application session stopped cleanly."
                    if not failures
                    else "Application session stopped with problems: "
                    + "; ".join(failures)
                ),
            )
        )
        self.technical_log.close()
        self._closed = True
        return tuple(failures)
