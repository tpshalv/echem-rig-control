from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
import tkinter as tk

import pytest

from rig_control.control.service import RigControlService
from rig_control.data.annotations import new_annotation, read_annotations, resolve_annotations
from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Measurement
from rig_control.polling import PollingBatch, PollingService
from rig_control.ui.operation import annotation_ui
from rig_control.ui.operation.annotation_dialog import time_context
from rig_control.ui.operation.annotation_overlay import (
    QC_FLAG_COLOUR,
    PlotMapping,
    click_uncertainty,
    cluster_markers,
    describe_note,
    place_on_graph,
)
from rig_control.ui.operation.annotation_ui import AnnotationHub
from rig_control.ui.operation.dashboard_window import DashboardPanel, DashboardSignal
from rig_control.ui.operation.model import OperationViewModel
from rig_control.ui.operation.trend_window import TrendChart
from rig_control.ui.operation.window import OperationWindow


NOW = datetime.now(UTC).replace(microsecond=0)


def make_model(tmp_path):
    manager = DeviceManager()
    manager.register(SimulatedSensor("probe", 25.0, "degC"))
    recorder = ExperimentRecorder(writer_factory=partial(
        DirectoryExperimentWriter, live_export_interval_seconds=0))
    polling = PollingService(manager, interval_seconds=60.0,
                             batch_handler=recorder.record_batch)
    model = OperationViewModel(manager, polling, recorder, RigControlService(manager),
                               profile_id="simulation")
    return model, polling


def feed(model, polling, seconds_ago, value=25.0):
    timestamp = NOW - timedelta(seconds=seconds_ago)
    polling.results.put(PollingBatch(
        started_at=timestamp, finished_at=timestamp,
        measurements=(MeasurementRecord("probe", "temperature",
                                        Measurement(value, "degC", timestamp=timestamp)),),
        failures=(), events=()))
    model.collect_polling_results()


def start(model, tmp_path, notes="testing flow"):
    model.start_monitoring()
    result = model.start_recording(experiment_id="EXP-1", operator="op",
                                   output_directory=tmp_path, notes=notes)
    assert result.succeeded, result.summary


# View model ----------------------------------------------------------------


def test_notes_are_refused_outside_the_recording(tmp_path):
    model, _polling = make_model(tmp_path)
    refused = model.add_annotation("lab_note", "too early", datetime.now(UTC))
    assert not refused.succeeded and "while recording" in refused.summary
    start(model, tmp_path)
    try:
        before = model.add_annotation("lab_note", "x", model.run_started_at - timedelta(seconds=1))
        assert "before this recording" in before.summary
        future = model.add_annotation("lab_note", "x", datetime.now(UTC) + timedelta(minutes=1))
        assert "future" in future.summary
        empty = model.add_annotation("lab_note", "  ", datetime.now(UTC))
        assert not empty.succeeded
        assert model.annotations == ()
    finally:
        model.stop_recording()
        model.stop_monitoring()


def test_add_edit_move_delete_are_journalled_and_shown(tmp_path):
    model, _polling = make_model(tmp_path)
    start(model, tmp_path)
    try:
        assert model.add_annotation("lab_note", "line 2 on", datetime.now(UTC)).succeeded
        note = model.annotations[0]
        assert model.edit_annotation(note.annotation_id, kind="qc_flag", text="leak").succeeded
        moved_to = model.run_started_at
        assert model.edit_annotation(
            note.annotation_id, timestamp=moved_to, origin="graph",
            uncertainty_seconds=5.0, signal=("probe", "temperature")).succeeded
        current = model.annotations[0]
        assert (current.kind, current.text, current.timestamp) == ("qc_flag", "leak", moved_to)
        assert current.edited and current.signal == ("probe", "temperature")
        start_time = moved_to.timestamp()
        assert model.annotations_between(start_time, start_time + 1) == (current,)
        assert model.delete_annotation(note.annotation_id).succeeded
        assert model.annotations == ()
        assert not model.delete_annotation(note.annotation_id).succeeded
        directory = model.experiment_directory
    finally:
        model.stop_recording()
        model.stop_monitoring()
    assert read_annotations(directory) == ()
    # Notes cannot be changed once the run has stopped.
    assert not model.add_annotation("lab_note", "late", datetime.now(UTC)).succeeded


def test_new_recording_starts_with_no_notes(tmp_path):
    model, _polling = make_model(tmp_path)
    start(model, tmp_path)
    model.add_annotation("lab_note", "first run", datetime.now(UTC))
    model.stop_recording()
    assert len(model.annotations) == 1  # Still shown on the graphs after stopping.
    model.start_recording(experiment_id="EXP-2", operator="op", output_directory=tmp_path)
    try:
        assert model.annotations == ()
    finally:
        model.stop_recording()
        model.stop_monitoring()


def test_nearest_reading_is_a_real_reading(tmp_path):
    model, polling = make_model(tmp_path)
    assert model.nearest_reading("probe", "temperature", NOW) is None
    for seconds_ago in (30, 20, 10):
        feed(model, polling, seconds_ago)
    reading = model.nearest_reading("probe", "temperature", NOW - timedelta(seconds=18))
    assert reading.timestamp == NOW - timedelta(seconds=20)


def test_run_review_reads_back_start_notes_notes_and_latest_review(tmp_path):
    model, _polling = make_model(tmp_path)
    start(model, tmp_path, notes="testing flow at 5 mL/min")
    model.add_annotation("qc_flag", "bubble", datetime.now(UTC))
    model.stop_recording()
    model.stop_monitoring()
    context = model.run_review_context()
    assert context.experiment_id == "EXP-1"
    assert context.start_notes == "testing flow at 5 mL/min"
    assert [note.text for note in context.annotations] == ["bubble"]
    assert context.review is None
    assert model.save_run_review(context.directory, "minor", " switched to leak test ").succeeded
    review = model.run_review_context(context.directory).review
    assert (review.overall_qc, review.end_notes) == ("minor", "switched to leak test")
    with pytest.raises(ValueError, match="metadata.json"):
        model.run_review_context(tmp_path)


# Placement maths -----------------------------------------------------------


MAPPING = PlotMapping(left=100, top=10, right=900, bottom=300, start=0.0, end=800.0)


def test_mapping_round_trips_time_and_x():
    assert MAPPING.seconds_per_pixel == 1.0
    assert MAPPING.x_for_time(250.0) == 350
    assert MAPPING.time_for_x(350) == 250.0
    assert MAPPING.contains(500, 100) and not MAPPING.contains(50, 100)


@pytest.mark.parametrize(("window_seconds", "expected"), [
    (600, 2), (3_600, 10), (28_800, 120), (86_400, 300),
])
def test_click_uncertainty_follows_zoom(window_seconds, expected):
    mapping = PlotMapping(0, 0, 800, 100, 0.0, float(window_seconds))
    assert click_uncertainty(mapping) == expected


def test_click_snaps_only_to_a_nearby_reading():
    readings = {}

    def nearest(_clicked):
        return readings.get("time")

    readings["time"] = datetime.fromtimestamp(253.0, UTC)
    near = place_on_graph(350, MAPPING, nearest)  # Clicked at t=250.
    assert near.snapped and near.timestamp.timestamp() == 253.0
    assert near.uncertainty_seconds == 2.0
    readings["time"] = datetime.fromtimestamp(290.0, UTC)
    far = place_on_graph(350, MAPPING, nearest)
    assert not far.snapped and far.timestamp.timestamp() == 250.0
    readings.clear()
    assert not place_on_graph(350, MAPPING, nearest).snapped


def note_at(seconds, kind="lab_note", text="note"):
    return resolve_annotations([new_annotation(
        kind=kind, text=text, timestamp=datetime.fromtimestamp(seconds, UTC),
        origin="typed")])[0]


def test_close_notes_share_one_marker_and_off_screen_notes_are_skipped():
    notes = [note_at(10), note_at(13, "qc_flag"), note_at(40), note_at(-5), note_at(900)]
    clusters = cluster_markers(notes, MAPPING)
    assert [len(cluster.notes) for cluster in clusters] == [2, 1]
    assert clusters[0].colour == QC_FLAG_COLOUR
    assert clusters[0].x == pytest.approx(111.5)


def test_note_just_after_the_newest_reading_waits_at_the_right_edge():
    clusters = cluster_markers([note_at(830)], MAPPING)
    assert [cluster.x for cluster in clusters] == [MAPPING.right]


def test_hover_text_says_when_how_and_what():
    started = datetime.fromtimestamp(0, UTC)
    text = describe_note(note_at(125, "qc_flag", "bubble"), started)
    assert "(+2:05 into run)" in text
    assert "QC flag" in text and "bubble" in text and "typed" in text


def test_time_context_reads_naturally():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert time_context(now - timedelta(seconds=3), now=now).endswith("just now")
    assert time_context(now - timedelta(seconds=45), now=now).endswith("~45 s ago")
    assert time_context(now - timedelta(minutes=6), now=now).endswith("~6 min ago")


# Tk wiring -----------------------------------------------------------------


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def reading(seconds_ago, value):
    return SimpleNamespace(value=value, unit="degC", quality="good",
                           timestamp=NOW - timedelta(seconds=seconds_ago))


class FakeNotes:
    def __init__(self, notes=(), recording=True):
        self.annotations = tuple(notes)
        self.is_recording = recording
        self.run_started_at = NOW - timedelta(hours=1)

    def annotations_between(self, start, end):
        return tuple(n for n in self.annotations if start <= n.timestamp.timestamp() <= end)

    def nearest_reading(self, *_args):
        return None


def marker_states(canvas):
    return [canvas.itemcget(item, "state") for item in canvas.find_withtag("annotation-marker")
            if canvas.type(item) == "line"]


def test_trend_markers_follow_the_shared_toggle(root):
    model = FakeNotes([note_at((NOW - timedelta(seconds=30)).timestamp())])
    hub = AnnotationHub(root, model, on_changed=lambda: None, on_status=lambda _t: None)
    chart = TrendChart(root, history_provider=lambda: (reading(60, 20), reading(0, 21)),
                       annotations=hub, signal=("probe", "temperature"))
    chart.pack()
    root.update()
    chart.refresh()
    assert marker_states(chart._canvas) == ["normal"]
    hub.show.set(False)
    chart.refresh()
    assert marker_states(chart._canvas) == ["hidden"]


def test_double_click_places_a_note_from_the_frame_on_screen(root, monkeypatch):
    opened = []
    model = FakeNotes()
    hub = AnnotationHub(root, model, on_changed=lambda: None, on_status=lambda _t: None)
    monkeypatch.setattr(annotation_ui, "AnnotationDialog",
                        lambda parent, **kwargs: opened.append(kwargs))
    chart = TrendChart(root, history_provider=lambda: (reading(60, 20), reading(0, 21)),
                       annotations=hub, signal=("probe", "temperature"))
    chart.pack()
    root.update()
    chart.refresh()
    mapping = chart._notes._mapping
    middle = (mapping.left + mapping.right) / 2
    chart._notes._on_double_click(SimpleNamespace(x=middle, y=mapping.top + 5))
    assert len(opened) == 1
    assert "probe.temperature" in opened[0]["context"]
    # Outside the plot area nothing opens.
    chart._notes._on_double_click(SimpleNamespace(x=mapping.left - 20, y=mapping.top + 5))
    assert len(opened) == 1
    # A point from monitoring before the run started is refused up front.
    model.run_started_at = NOW - timedelta(seconds=10)
    chart._notes._on_double_click(SimpleNamespace(x=middle, y=mapping.top + 5))
    assert len(opened) == 1
    model.run_started_at = NOW - timedelta(hours=1)
    # Without a recording, nothing opens and the operator is told why.
    model.is_recording = False
    chart._notes._on_double_click(SimpleNamespace(x=middle, y=mapping.top + 5))
    assert len(opened) == 1


def test_move_here_uses_the_next_click_in_the_plot(root):
    moved = []
    note = note_at((NOW - timedelta(seconds=30)).timestamp())
    model = FakeNotes([note])
    hub = AnnotationHub(root, model, on_changed=lambda: None, on_status=lambda _t: None)
    hub.move = lambda moving, placement, signal: moved.append((moving, placement, signal))
    chart = TrendChart(root, history_provider=lambda: (reading(60, 20), reading(0, 21)),
                       annotations=hub, signal=("probe", "temperature"))
    chart.pack()
    root.update()
    chart.refresh()
    mapping = chart._notes._mapping
    chart._notes._start_move(note)
    assert chart._notes.moving is note
    chart._notes._on_click(SimpleNamespace(x=mapping.right - 1, y=mapping.top + 5))
    assert chart._notes.moving is None
    assert moved[0][0] is note and moved[0][2] == ("probe", "temperature")
    assert moved[0][1].timestamp.timestamp() == pytest.approx(mapping.time_for_x(mapping.right - 1))


def test_dashboard_picks_the_trace_under_the_cursor(root):
    history = {
        ("a", "low"): (reading(60, 1.0), reading(0, 1.0)),
        ("b", "high"): (reading(60, 9.0), reading(0, 9.0)),
    }
    signals = (DashboardSignal("a", "low", "A low", "V", "All"),
               DashboardSignal("b", "high", "B high", "V", "All"))
    hub = AnnotationHub(root, FakeNotes(), on_changed=lambda: None, on_status=lambda _t: None)
    panel = DashboardPanel(root, signal_provider=lambda: signals,
                           history_provider=lambda device, channel, _seconds:
                           history[(device, channel)],
                           annotations=hub)
    panel.pack(fill="both", expand=True)
    root.geometry("800x500")
    root.update()
    for signal in signals:
        panel._signal.set(signal.label)
        panel._add_selected()
    root.update()
    panel.refresh()
    mapping = panel._renderer.mapping
    x = (mapping.left + mapping.right) / 2
    assert panel._renderer.trace_nearest(x, mapping.top + 2) == ("b", "high")
    assert panel._renderer.trace_nearest(x, mapping.bottom - 2) == ("a", "low")


def test_note_box_takes_the_time_when_typing_starts(root, tmp_path, monkeypatch):
    model, _polling = make_model(tmp_path)
    reviews = []
    monkeypatch.setattr("rig_control.ui.operation.window.RunReviewDialog",
                        lambda parent, context, on_done: reviews.append((context, on_done)))
    window = OperationWindow(root, model, profile_name="test",
                             default_output_directory=str(tmp_path))
    try:
        window._update_display()
        assert str(window._note_entry.cget("state")) == "disabled"
        start(model, tmp_path)
        window._update_display()
        assert str(window._note_entry.cget("state")) == "normal"

        window._note_key_pressed(SimpleNamespace(char="l"))
        typed_at = window._note_started_at
        window._note_entry.insert(0, "line 2 reconnected")
        window._note_kind.set("QC flag")
        window._add_typed_note()
        note = model.annotations[0]
        assert (note.text, note.kind, note.timestamp) == ("line 2 reconnected", "qc_flag", typed_at)
        assert window._note_entry.get() == "" and window._note_started_at is None
        assert window._note_kind.get() == "Lab note"
        assert "QC flag · line 2 reconnected" in window._recent_notes.cget("text")

        # Stopping offers the review straight away; saving refreshes the Excel file.
        window._run_in_background = lambda action, complete: complete(action(), None)
        window._stop_recording()
        assert len(reviews) == 1
        context, on_done = reviews[0]
        assert context.start_notes == "testing flow"
        on_done(("severe", "pump stalled at 40 min"))
        assert context.review is None
        assert model.run_review_context(context.directory).review.overall_qc == "severe"
        assert window._review_export_directory is None
        assert window._recording_finalising is False
    finally:
        window.cancel_updates()
        model.stop_monitoring()
