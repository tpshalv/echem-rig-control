from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import Mock

import pytest

from rig_control.app_settings import SETTING_DEFINITIONS, default_app_settings
from rig_control.devices.manager import DeviceManager
from rig_control.instrument_settings.re72 import Re72SettingsService
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.ui.device_setup.model import DeviceSetupViewModel
from rig_control.ui.device_setup.window import DeviceSetupWindow
from rig_control.ui.home.model import AppSettingRow
from rig_control.ui.home.settings_window import SettingsWindow


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def test_settings_separates_application_files_from_instrument_actions(root):
    settings = default_app_settings()
    service = Re72SettingsService(
        DeviceManager(), load_rig_profile("rig-profile.example.toml"),
        require_write_access=lambda: None,
    )
    service.read_settings = Mock(side_effect=AssertionError("Unexpected hardware read"))
    model = Mock()
    model.settings = settings
    model.settings_path = Path("settings.toml")
    model.setting_rows.return_value = tuple(
        AppSettingRow(item.key, item.label, item.description, str(settings.values[item.key]))
        for item in SETTING_DEFINITIONS
    )
    model.instrument_settings = SimpleNamespace(re72=service)
    window = SettingsWindow(root, model)
    root.update_idletasks()
    assert window._file_frame.winfo_manager() == "grid"
    window._sidebar.selection_set("instruments")
    window._on_category_selected(None)
    assert window._sidebar.selection() == ("RE72 controllers",)
    assert window._file_frame.winfo_manager() == ""
    assert window._save_button.winfo_manager() == ""
    panel = window._instrument_panels["RE72 controllers"]
    panel.apply_changes = Mock(return_value=True)
    assert window._apply()
    panel.apply_changes.assert_called_once()
    model.apply_setting_text.assert_not_called()
    assert panel._re72_entries
    service.read_settings.assert_not_called()
    window._sidebar.selection_set("General")
    window._on_category_selected(None)
    assert window._save_button.winfo_manager() == "grid"


def test_device_setup_constructs_and_opens_extracted_add_dialogs(root):
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        serial_port_provider=lambda: (),
    )
    window = DeviceSetupWindow(root, model)
    open_dialogs = (
        window._alicat_dialogs._open_add_alicat,
        window._esp32_dialogs._open_add_esp32,
        window._temperature_probe_dialogs._open_add_temperature_probe,
        window._guardian_dialogs._open_add_guardian,
        window._power_supply_dialogs._open_add_keithley,
    )
    for open_dialog in open_dialogs:
        open_dialog()
        dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
        assert len(dialogs) == 1
        dialogs[0].destroy()
