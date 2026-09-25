"""Explicit registration of detailed instrument editors.

A potentiostat or GC can supply a dedicated panel and service here without
adding its parameters, acquisition methods or dialogs to Home or app settings.
"""

from collections.abc import Callable
import tkinter as tk

from rig_control.instrument_settings.catalog import InstrumentSettingsCatalog
from rig_control.ui.instrument_settings.re72_panel import Re72SettingsPanel
from rig_control.ui.instrument_settings.pump_calibration_panel import PumpCalibrationPanel
from rig_control.ui.instrument_settings.base import InstrumentSettingsPanel


def create_instrument_panels(
    parent: tk.Misc,
    catalog_provider: Callable[[], InstrumentSettingsCatalog],
    scroll_from_event: Callable[[tk.Event], None],
) -> dict[str, InstrumentSettingsPanel]:
    return {
        "RE72 controllers": Re72SettingsPanel(
            parent, lambda: catalog_provider().re72, scroll_from_event,
        ),
        "Pump calibration": PumpCalibrationPanel(
            parent, lambda: catalog_provider().pump_calibration, scroll_from_event,
        ),
    }
