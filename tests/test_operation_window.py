from queue import Queue
from datetime import UTC, datetime

from rig_control.ui.operation.model import LiveMeasurementRow
from rig_control.ui.operation.window import OperationWindow


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
