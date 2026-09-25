from queue import Queue
from datetime import UTC, datetime
from tkinter import ttk
from types import SimpleNamespace
import tkinter as tk

import pytest

from rig_control.ui.operation.model import LiveMeasurementRow
from rig_control.ui.operation.window import (
    OperationWindow,
    format_operation_boolean,
)


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


class FailingModel:
    def collect_polling_results(self) -> int:
        raise RuntimeError("simulated UI tick failure")


def _prepare_tick_window(model) -> OperationWindow:
    window = OperationWindow.__new__(OperationWindow)
    window._connection_results = Queue()
    window._view_model = model
    window._event_sink = None
    window._refresh_failed = False
    window._ui_tick_count = 0
    window._ui_tick_failure_count = 0
    window._last_successful_ui_tick = None
    window._schedule_update = lambda: None
    return window


def test_failed_ui_tick_is_logged_and_rescheduled() -> None:
    recorded = []
    window = _prepare_tick_window(FailingModel())
    window._event_sink = lambda event, details: recorded.append((event, details))
    scheduled = []
    window._schedule_update = lambda: scheduled.append(True)

    window._poll_ui_queue()

    assert scheduled == [True]
    assert len(recorded) == 1
    assert "simulated UI tick failure" in recorded[0][0].message
    assert "Traceback" in recorded[0][1]


def test_repeated_ui_failure_is_not_logged_every_tick() -> None:
    recorded = []
    window = _prepare_tick_window(FailingModel())
    window._event_sink = lambda event, details: recorded.append((event, details))

    window._poll_ui_queue()
    window._poll_ui_queue()

    assert len(recorded) == 1
    assert window._ui_tick_count == 2
    assert window._ui_tick_failure_count == 2


class EmptyModel:
    def collect_polling_results(self) -> int:
        return 0


def test_empty_ui_tick_does_not_redraw_display() -> None:
    window = _prepare_tick_window(EmptyModel())
    redraws = []
    window._update_display = lambda **kwargs: redraws.append(kwargs)

    window._poll_ui_queue()

    assert redraws == []
    assert window._ui_tick_count == 1
    assert window._ui_tick_failure_count == 0
    assert window._last_successful_ui_tick is not None


def test_watchdog_boolean_uses_true_false_without_changing_outputs() -> None:
    assert format_operation_boolean("watchdog_tripped", True) == "True"
    assert format_operation_boolean("watchdog_tripped", False) == "False"
    assert format_operation_boolean("output_enabled", True) == "On"
    assert format_operation_boolean("output_enabled", False) == "Off"


class FakeTree:
    def __init__(self) -> None:
        self.rows = {}
        self.insert_count = 0
        self.update_count = 0
        self.delete_count = 0

    def insert(self, parent, position, *, iid, values) -> None:
        self.rows[iid] = values
        self.insert_count += 1

    def item(self, iid, *, values) -> None:
        self.rows[iid] = values
        self.update_count += 1

    def delete(self, iid) -> None:
        self.rows.pop(iid)
        self.delete_count += 1


class FakeListbox:
    def delete(self, first, last) -> None:
        pass

    def insert(self, position, value) -> None:
        pass


class FakeWidget:
    def configure(self, **kwargs) -> None:
        pass


class DisplayModel:
    is_monitoring = False
    is_recording = False

    def __init__(self, rows) -> None:
        self.rows = rows

    def measurement_rows(self):
        return tuple(self.rows)

    def warnings(self):
        return ()


def test_measurement_display_updates_existing_rows_in_place() -> None:
    timestamp = datetime(2026, 8, 24, tzinfo=UTC)
    model = DisplayModel(
        [LiveMeasurementRow("sensor", "humidity", 50, "%", "good", timestamp)]
    )
    window = OperationWindow.__new__(OperationWindow)
    window._view_model = model
    window._measurements = FakeTree()
    window._warnings = FakeListbox()
    window._monitor_button = FakeWidget()
    window._record_button = FakeWidget()
    window._recording_status = FakeWidget()
    window._measurement_keys = {}
    window._measurement_items = {}
    window._measurement_values = {}
    window._next_measurement_item = 0
    window._displayed_warnings = ()
    window._measurement_rows_created = 0
    window._measurement_rows_updated = 0
    window._measurement_rows_deleted = 0
    window._warning_display_rebuilds = 0
    window._trend_windows = {}

    window._update_display()
    window._update_display()
    model.rows = [
        LiveMeasurementRow("sensor", "humidity", 51, "%", "good", timestamp)
    ]
    window._update_display()
    model.rows = []
    window._update_display()

    assert window._measurements.insert_count == 1
    assert window._measurements.update_count == 1
    assert window._measurements.delete_count == 1
    assert window._measurement_rows_created == 1
    assert window._measurement_rows_updated == 1
    assert window._measurement_rows_deleted == 1


class InteractionTree:
    def __init__(self, column: str) -> None:
        self.column = column

    def identify_column(self, _x) -> str:
        return self.column

    def identify_row(self, _y) -> str:
        return "row"


def test_double_click_on_set_value_edits_but_channel_opens_trend() -> None:
    window = OperationWindow.__new__(OperationWindow)
    tree = InteractionTree("#4")
    window._channel_trees = {None: tree}
    window._tree_item_keys = {(None, "row"): ("supply", "current_limit")}
    window._channel_rows = {
        ("supply", "current_limit"): SimpleNamespace(writable=True)
    }
    edited = []
    trended = []
    window._begin_cell_edit = lambda event, system: edited.append((event, system))
    window._open_selected_trend = lambda event, system: trended.append((event, system))
    event = SimpleNamespace(x=1, y=1)

    window._handle_channel_double_click(event, None)
    tree.column = "#2"
    window._handle_channel_double_click(event, None)

    assert len(edited) == 1
    assert len(trended) == 1


def test_watchdog_action_works_from_any_column() -> None:
    window = OperationWindow.__new__(OperationWindow)
    tree = InteractionTree("#1")
    window._channel_trees = {None: tree}
    window._tree_item_keys = {(None, "row"): ("esp32", "watchdog_rearm")}
    window._channel_rows = {
        ("esp32", "watchdog_rearm"): SimpleNamespace(
            writable=True, editor="action"
        )
    }
    actions = []
    window._begin_cell_edit = lambda event, system: actions.append((event, system))
    window._open_selected_trend = lambda event, system: None

    window._handle_channel_double_click(SimpleNamespace(x=1, y=1), None)

    assert len(actions) == 1


class FakeCellTree(ttk.Frame):
    """A real Tk widget (so it can parent the combobox _begin_cell_edit
    creates) that fakes just enough Treeview identification/geometry to
    drive that method without a fully populated tree."""

    def identify_row(self, _y) -> str:
        return "row"

    def identify_column(self, _x) -> str:
        return "#4"

    def bbox(self, _item_id, _column=None):
        return (5, 5, 80, 20)


@pytest.mark.parametrize("editor_kind", ["direction", "running"])
def test_unknown_pump_state_leaves_the_editor_blank_instead_of_guessing(root, editor_kind):
    # Regression test: an unread direction/running value is None, and the
    # editor must not pre-select "None" (direction) or "Off" (running) as
    # if that were a real, confirmed state.
    window = OperationWindow.__new__(OperationWindow)
    window._editor = None
    window._editor_apply = None
    tree = FakeCellTree(root)
    window._channel_trees = {None: tree}
    window._tree_item_keys = {(None, "row"): ("pump1", editor_kind)}
    window._channel_rows = {
        ("pump1", editor_kind): SimpleNamespace(writable=True, editor=editor_kind, value=None)
    }

    window._begin_cell_edit(SimpleNamespace(x=10, y=10), None)

    assert window._editor.get() == ""


@pytest.mark.parametrize("recording", [True, False])
def test_export_excel_uses_active_run_or_selected_folder(recording, monkeypatch):
    calls = []
    exports = []
    model = SimpleNamespace(
        is_recording=recording, experiment_directory="previous-run",
        export_excel=lambda directory: exports.append(directory),
    )
    window = OperationWindow.__new__(OperationWindow)
    window._root = None
    window._view_model = model
    window._action_in_progress = False
    window._entries = {}
    window._run_operation_action = lambda description, action: action()

    def choose(**kwargs):
        calls.append(kwargs)
        return "selected-run"

    monkeypatch.setattr("rig_control.ui.operation.window.filedialog.askdirectory", choose)
    window._export_excel()
    assert exports == [None if recording else "selected-run"]
    assert len(calls) == (0 if recording else 1)


def test_cancel_excel_folder_selection_does_not_export(monkeypatch):
    window = OperationWindow.__new__(OperationWindow)
    window._root = None
    window._action_in_progress = False
    window._view_model = SimpleNamespace(is_recording=False, experiment_directory="run")
    window._run_operation_action = lambda *args: pytest.fail("Cancelled export ran")
    monkeypatch.setattr("rig_control.ui.operation.window.filedialog.askdirectory", lambda **kwargs: "")
    window._export_excel()
