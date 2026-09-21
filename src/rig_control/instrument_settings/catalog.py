"""Entry point for instrument configuration services.

Add services here when new complex instrument drivers arrive. Instrument
parameters remain instrument-specific; they are not application preferences.
"""

from collections.abc import Callable
from rig_control.devices.manager import DeviceManager
from rig_control.models import EventSink
from rig_control.rig_profile import RigProfile
from rig_control.instrument_settings.re72 import Re72SettingsService


class InstrumentSettingsCatalog:
    def __init__(
        self, manager: DeviceManager, profile: RigProfile, *,
        require_write_access: Callable[[], None], event_sink: EventSink | None = None,
    ) -> None:
        self.re72 = Re72SettingsService(
            manager, profile, require_write_access=require_write_access,
            event_sink=event_sink,
        )
