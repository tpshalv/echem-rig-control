from pathlib import Path

from rig_control.app_settings import AppSettings
from rig_control.control.service import RigControlService
from rig_control.device_factory import create_device_manager
from rig_control.devices.manager import DeviceManager
from rig_control.event_logging import TechnicalEventLogger
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event
from rig_control.polling import PollingService
from rig_control.rig_profile import RigProfile


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
            event_sink=self.technical_log.record,
        )
        self.control_service = RigControlService(self.device_manager)
        self.experiment_recorder = ExperimentRecorder()
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
        )
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    def stop_feature_services(self) -> tuple[str, ...]:
        failures: list[str] = []
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
