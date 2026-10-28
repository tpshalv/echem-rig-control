from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import tkinter as tk

import pytest

from rig_control.ui.operation import annotation_ui
from rig_control.ui.operation.annotation_overlay import PlotMapping
from rig_control.ui.operation.annotation_ui import AnnotationHub
from rig_control.ui.operation.dashboard_window import DashboardPanel, DashboardSignal, faded
from rig_control.ui.operation.graph_reader import (
    GraphFrame,
    ReadPoint,
    TraceGeometry,
    ValueAxis,
    choose_trace,
    describe_change,
    format_span,
    screen_point,
    snapped_point,
    traces_near,
)
from rig_control.ui.operation.trend_window import TrendChart


NOW = datetime.now(UTC).replace(microsecond=0)
MAPPING = PlotMapping(left=100, top=0, right=300, bottom=100, start=1_000, end=1_200)


def trace(label, unit, *points):
    return TraceGeometry(label, unit, tuple(points))


FRAME = GraphFrame(
    MAPPING,
    (ValueAxis("V", 0.0, 10.0), ValueAxis("%RH", 20.0, 60.0)),
    {
        ("supply", "voltage"): trace("Voltage", "V", (1_000, 2.0, 100, 80), (1_100, 2.5, 200, 75)),
        ("probe", "humidity"): trace("Humidity", "%RH", (1_000, 40.0, 100, 50), (1_100, 41.0, 200, 47.5)),
        ("probe", "dew"): trace("Dew point", "%RH", (1_000, 41.0, 100, 47.5), (1_100, 42.0, 200, 45)),
    },
)


def test_screen_reader_reads_the_cursor_on_every_axis_without_snapping() -> None:
    point = screen_point(FRAME, 150, 25)

    assert point.time == pytest.approx(1_050)
    assert point.values == (
        (pytest.approx(7.5), "V"),
        (pytest.approx(50.0), "%RH"),
    )
    assert not point.snapped


def test_selected_trace_snaps_to_its_nearest_drawn_reading() -> None:
    point = snapped_point(FRAME, ("supply", "voltage"), 160)

    assert point == ReadPoint(1_100, ((2.5, "V"),), snapped=True)


def test_double_clicks_on_the_same_spot_cycle_through_close_traces() -> None:
    near = traces_near(FRAME, 200, 46)

    assert near == [("probe", "dew"), ("probe", "humidity")]
    first = choose_trace(near, None, same_spot=False)
    second = choose_trace(near, first, same_spot=True)
    third = choose_trace(near, second, same_spot=True)
    assert (first, second, third) == (near[0], near[1], near[0])
    # Elsewhere, the nearest trace is picked afresh.
    assert choose_trace(near, second, same_spot=False) == near[0]
    # Empty space selects nothing, returning to the screen reader.
    assert choose_trace(traces_near(FRAME, 200, 10), second, same_spot=False) is None


def test_measurement_gives_change_and_a_readable_rate() -> None:
    first = ReadPoint(0, ((40.0, "%RH"), (2.0, "V")))
    later = ReadPoint(133, ((41.2, "%RH"), (2.0, "V")))

    assert describe_change(first, later) == [
        "Δt 2 min 13 s",
        "Δ 1.2 %RH   (0.5414 %RH/min)",
        "Δ 0 V   (0 V/min)",
    ]
    hours = describe_change(ReadPoint(0, ((1.0, "V"),)), ReadPoint(7_200, ((3.0, "V"),)))
    assert hours == ["Δt 2 h 00 min", "Δ 2 V   (1 V/h)"]
    assert format_span(12.34) == "12.3 s"


def test_faded_colour_moves_towards_white() -> None:
    assert faded("#000000", 0.5) == "#808080"
    assert faded("#0067A3", 0) == "#0067A3"


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def reading(seconds_ago, value):
    return SimpleNamespace(value=value, unit="V", quality="good",
                           timestamp=NOW - timedelta(seconds=seconds_ago))


class FakeNotes:
    is_recording = True
    run_started_at = NOW - timedelta(hours=1)

    def annotations_between(self, _start, _end):
        return ()

    def nearest_reading(self, *_args):
        return None


def make_panel(root, monkeypatch, opened):
    monkeypatch.setattr(annotation_ui, "AnnotationDialog",
                        lambda parent, **kwargs: opened.append(kwargs))
    history = {
        ("a", "low"): (reading(60, 1.0), reading(30, 1.5), reading(0, 1.0)),
        ("b", "high"): (reading(60, 9.0), reading(30, 8.0), reading(0, 9.0)),
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
    return panel


def press(panel, mode):
    button = panel._mode_buttons[0 if mode == "reader" else 1]
    button.invoke()


def test_mode_buttons_are_exclusive_and_toggle_off(root, monkeypatch) -> None:
    panel = make_panel(root, monkeypatch, [])
    assert panel.mode is None

    press(panel, "reader")
    assert panel.mode == "reader" and panel._reader.enabled
    assert "Shift-click" in panel._mode_hint.cget("text")
    press(panel, "note")
    assert panel.mode == "note" and not panel._reader.enabled
    assert panel._notes.adds_on_double_click
    press(panel, "note")
    assert panel.mode is None
    assert not panel._notes.adds_on_double_click
    assert panel._mode_hint.cget("text") == ""


def test_double_click_adds_a_note_only_in_note_mode(root, monkeypatch) -> None:
    opened = []
    panel = make_panel(root, monkeypatch, opened)
    mapping = panel._renderer.mapping
    click = SimpleNamespace(x=(mapping.left + mapping.right) / 2, y=mapping.top + 5)

    panel._notes._on_double_click(click)
    assert opened == []
    press(panel, "reader")
    panel._notes._on_double_click(click)
    assert opened == []
    press(panel, "note")
    panel._notes._on_double_click(click)
    assert len(opened) == 1


def test_reader_selects_highlights_and_measures_a_trace(root, monkeypatch) -> None:
    panel = make_panel(root, monkeypatch, [])
    press(panel, "reader")
    frame = panel._renderer.frame
    high = frame.traces[("b", "high")]
    _time, _value, x, y = high.points[1]

    panel._reader._on_double_click(SimpleNamespace(x=x, y=y))
    assert panel._reader.selected == ("b", "high")
    line = panel._renderer._lines[("b", "high")]
    other = panel._renderer._lines[("a", "low")]
    assert float(panel._canvas.itemcget(line, "width")) == 3
    assert panel._canvas.itemcget(other, "fill") != panel._canvas.itemcget(line, "fill")

    first, last = high.points[0], high.points[2]
    panel._reader._on_shift_click(SimpleNamespace(x=first[2], y=first[3]))
    panel._reader._on_shift_click(SimpleNamespace(x=last[2] - 1, y=last[3]))
    assert [point.values for point in panel._reader.measurement] == [
        ((9.0, "V"),), ((9.0, "V"),)
    ]
    assert panel._canvas.itemcget(panel._reader._result, "state") == "normal"

    panel._reader._on_escape(None)
    assert panel._reader.selected is None and panel._reader.measurement == []


def test_trend_popup_reader_is_always_on_and_only_reads_the_screen(root) -> None:
    chart = TrendChart(root, history_provider=lambda: (reading(60, 20), reading(0, 21)))
    chart.pack()
    root.update()
    chart.refresh()

    assert chart._reader.enabled
    mapping = chart._frame.mapping
    chart._reader._on_motion(SimpleNamespace(x=mapping.left + 10, y=mapping.top + 10))
    assert chart._canvas.itemcget(chart._reader._text, "state") == "normal"
    assert chart._canvas.itemcget(chart._reader._dot, "state") == "hidden"
