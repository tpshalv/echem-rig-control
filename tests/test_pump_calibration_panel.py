import tkinter as tk
from unittest.mock import Mock

import pytest

from rig_control.devices.manager import DeviceManager
from rig_control.devices.pump_calibration import CalibrationPoint
from rig_control.instrument_settings.pump_calibration import (
    PumpCalibrationService,
    PumpCalibrationSettingsService,
)
from rig_control.rig_profile import RigProfile
from rig_control.ui.instrument_settings.pump_calibration_panel import PumpCalibrationPanel
from tests.test_pump_calibration import _StubPump


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    # A real, un-mocked messagebox call blocks waiting for a live click in
    # this (non-headless) test environment. Stub every dialog by default so
    # a missing per-test mock hangs pytest instead of a human; tests that
    # need to inspect a call use the returned mocks.
    mocks = {name: Mock(return_value=True) for name in
             ("showerror", "showinfo", "showwarning", "askyesno")}
    for name, mock in mocks.items():
        monkeypatch.setattr(
            f"rig_control.ui.instrument_settings.pump_calibration_panel.messagebox.{name}", mock,
        )
    return mocks


def make_panel(root, tmp_path, *, allow_write=True):
    manager = DeviceManager()
    manager.register(_StubPump("pump1"))
    service = PumpCalibrationSettingsService(
        manager, RigProfile("rig", "Rig", (), ()),
        PumpCalibrationService(directory=tmp_path),
        require_write_access=(lambda: None) if allow_write else _deny,
    )
    panel = PumpCalibrationPanel(root, lambda: service, lambda _event: None)
    panel._operator_var.set("operator")
    return panel, service


def _deny() -> None:
    raise RuntimeError("Close active feature screens first.")


def enter_points(panel, points):
    for (row, speed_entry, flow_entry), (rpm, flow) in zip(panel._point_rows, points):
        speed_entry.delete(0, "end"); speed_entry.insert(0, str(rpm))
        flow_entry.delete(0, "end"); flow_entry.insert(0, str(flow))


def test_panel_lists_configured_pump_on_construction(root, tmp_path):
    panel, _service = make_panel(root, tmp_path)
    assert panel._device_var.get() == "pump1"


def test_fit_enables_accept_and_decline(root, tmp_path):
    panel, _service = make_panel(root, tmp_path)
    enter_points(panel, [(10.0, 20.0), (20.0, 42.0), (30.0, 61.0)])

    panel._fit()

    assert str(panel._accept_button.cget("state")) == "normal"
    assert str(panel._decline_button.cget("state")) == "normal"
    assert "Fit:" in panel._status.cget("text")
    assert panel._candidate is not None


def test_accept_saves_calibration_and_clears_the_form(root, tmp_path):
    panel, service = make_panel(root, tmp_path)
    enter_points(panel, [(10.0, 20.0), (20.0, 42.0), (30.0, 61.0)])
    panel._fit()

    panel._accept()

    assert service.active_calibration("pump1") is not None
    assert panel._candidate is None
    assert str(panel._accept_button.cget("state")) == "disabled"
    assert all(speed.get() == "" and flow.get() == "" for _row, speed, flow in panel._point_rows)


def test_decline_discards_the_candidate_without_saving(root, tmp_path):
    panel, service = make_panel(root, tmp_path)
    enter_points(panel, [(10.0, 20.0), (20.0, 42.0), (30.0, 61.0)])
    panel._fit()

    panel._decline()

    assert service.active_calibration("pump1") is None
    assert panel._candidate is None


def test_accept_blocked_without_write_access_leaves_candidate_pending(root, tmp_path, dialogs):
    panel, service = make_panel(root, tmp_path, allow_write=False)
    enter_points(panel, [(10.0, 20.0), (20.0, 42.0), (30.0, 61.0)])
    panel._fit()

    panel._accept()

    assert dialogs["showerror"].called
    assert service.active_calibration("pump1") is None
    assert panel._candidate is not None  # Not silently discarded on a blocked accept.


def test_fit_with_too_few_points_shows_an_error_and_stays_disabled(root, tmp_path, dialogs):
    panel, _service = make_panel(root, tmp_path)
    enter_points(panel, [(10.0, 20.0)])

    panel._fit()

    assert dialogs["showerror"].called
    assert panel._candidate is None
    assert str(panel._accept_button.cget("state")) == "disabled"


def test_remove_point_row_respects_the_minimum(root, tmp_path):
    panel, _service = make_panel(root, tmp_path)
    initial = len(panel._point_rows)

    row, _speed, _flow = panel._point_rows[0]
    panel._remove_point_row(row)
    assert len(panel._point_rows) == initial - 1

    while len(panel._point_rows) > 2:
        panel._remove_point_row(panel._point_rows[0][0])
    row, _speed, _flow = panel._point_rows[0]
    panel._remove_point_row(row)
    assert len(panel._point_rows) == 2  # MINIMUM_POINT_ROWS is enforced.


def test_add_point_row_grows_the_form(root, tmp_path):
    panel, _service = make_panel(root, tmp_path)
    before = len(panel._point_rows)

    panel._add_point_row()

    assert len(panel._point_rows) == before + 1


def test_corrupted_history_file_does_not_crash_the_panel(root, tmp_path):
    (tmp_path / "pump1.json").write_text("not valid json", encoding="utf-8")

    panel, _service = make_panel(root, tmp_path)  # Must not raise.

    assert "Could not read calibration history" in panel._status.cget("text")


def test_resize_redraw_does_not_re_read_history_from_disk(root, tmp_path):
    panel, service = make_panel(root, tmp_path)
    service.accept(service.fit_candidate("pump1", "operator",
        [CalibrationPoint(10.0, 20.0), CalibrationPoint(20.0, 42.0)]))
    panel.refresh()  # Loads the accepted calibration into the cache.
    original_history = panel._history_cache

    # Delete the file from under the cache; a pure resize/redraw must not
    # try to re-read it.
    (tmp_path / "pump1.json").unlink()
    panel._refresh_chart(reload=False)

    assert panel._history_cache == original_history
    assert "Could not read" not in panel._status.cget("text")
