"""Crosshair graph reader for the live graphs.

With no trace selected it is a screen reader: the time under the cursor and
the cursor's height on each value axis, with no snapping to data. With a
trace selected (double-click it) the crosshair snaps to that trace's nearest
drawn reading. Shift-click two points to measure the change between them.

The maths here is pure and testable; ``GraphReader`` only draws it.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import tkinter as tk

from rig_control.ui.common.theme import MUTED_TEXT, NAVY_INK
from rig_control.ui.operation.annotation_overlay import PlotMapping

#: How far (pixels) a double-click may be from a trace and still select it.
SELECT_DISTANCE = 12.0
#: Double-clicks this close together count as "the same spot" for cycling.
SAME_SPOT_DISTANCE = 6.0

TraceKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ValueAxis:
    unit: str
    minimum: float
    maximum: float

    def value_for_y(self, y: float, mapping: PlotMapping) -> float:
        fraction = (mapping.bottom - y) / max(mapping.bottom - mapping.top, 1e-9)
        return self.minimum + fraction * (self.maximum - self.minimum)

    def y_for_value(self, value: float, mapping: PlotMapping) -> float:
        fraction = (value - self.minimum) / ((self.maximum - self.minimum) or 1e-9)
        return mapping.bottom - fraction * (mapping.bottom - mapping.top)


@dataclass(frozen=True, slots=True)
class TraceGeometry:
    label: str
    unit: str
    #: Drawn points, oldest first, as (time, value, x, y).
    points: tuple[tuple[float, float, float, float], ...]

    def nearest_index(self, x: float) -> int | None:
        if not self.points:
            return None
        xs = [point[2] for point in self.points]
        index = bisect_left(xs, x)
        candidates = [i for i in (index - 1, index) if 0 <= i < len(xs)]
        return min(candidates, key=lambda i: abs(xs[i] - x))

    def y_at(self, x: float) -> float | None:
        index = self.nearest_index(x)
        return None if index is None else self.points[index][3]


@dataclass(frozen=True, slots=True)
class GraphFrame:
    """Everything the reader needs about one drawn frame of a graph."""

    mapping: PlotMapping
    axes: tuple[ValueAxis, ...]
    traces: dict[TraceKey, TraceGeometry] = field(default_factory=dict)

    def axis(self, unit: str) -> ValueAxis | None:
        return next((axis for axis in self.axes if axis.unit == unit), None)


@dataclass(frozen=True, slots=True)
class ReadPoint:
    """A point in data terms, so it stays put while the graph rescales."""

    time: float
    #: (value, unit) per axis; a snapped point has exactly one.
    values: tuple[tuple[float, str], ...]
    snapped: bool = False


def screen_point(frame: GraphFrame, x: float, y: float) -> ReadPoint:
    return ReadPoint(
        frame.mapping.time_for_x(x),
        tuple((axis.value_for_y(y, frame.mapping), axis.unit) for axis in frame.axes),
    )


def snapped_point(frame: GraphFrame, key: TraceKey, x: float) -> ReadPoint | None:
    trace = frame.traces.get(key)
    index = None if trace is None else trace.nearest_index(x)
    if index is None:
        return None
    time, value, _x, _y = trace.points[index]
    return ReadPoint(time, ((value, trace.unit),), snapped=True)


def traces_near(frame: GraphFrame, x: float, y: float) -> list[TraceKey]:
    """Traces within reach of a point, nearest first, judged at that x."""

    distances = []
    for key, trace in frame.traces.items():
        trace_y = trace.y_at(x)
        if trace_y is not None and abs(trace_y - y) <= SELECT_DISTANCE:
            distances.append((abs(trace_y - y), key))
    return [key for _distance, key in sorted(distances)]


def choose_trace(
    candidates: list[TraceKey],
    current: TraceKey | None,
    same_spot: bool,
) -> TraceKey | None:
    """Nearest trace, or the next one along when double-clicking the same spot."""

    if not candidates:
        return None
    if same_spot and current in candidates:
        return candidates[(candidates.index(current) + 1) % len(candidates)]
    return candidates[0]


def format_value(value: float) -> str:
    return f"{value:.4g}"


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


def format_span(seconds: float) -> str:
    """A time difference as people say it: '12.3 s', '2 min 13 s', '1 h 05 min'."""

    seconds = abs(seconds)
    if seconds < 60:
        return f"{seconds:.3g} s"
    whole = round(seconds)
    if whole < 3_600:
        return f"{whole // 60} min {whole % 60:02d} s"
    return f"{whole // 3_600} h {whole % 3_600 // 60:02d} min"


def describe_point(point: ReadPoint) -> str:
    values = "  ".join(f"{format_value(value)} {unit}" for value, unit in point.values)
    return f"{format_time(point.time)}  {values}"


def describe_change(first: ReadPoint, second: ReadPoint) -> list[str]:
    """Δtime, then Δvalue and rate for every unit both points share."""

    seconds = second.time - first.time
    lines = [f"Δt {'-' if seconds < 0 else ''}{format_span(seconds)}"]
    later = dict((unit, value) for value, unit in second.values)
    per_hour = abs(seconds) >= 3_600
    for value, unit in first.values:
        if unit not in later:
            continue
        change = later[unit] - value
        line = f"Δ {format_value(change)} {unit}"
        if seconds:
            rate = change / seconds * (3_600 if per_hour else 60)
            line += f"   ({format_value(rate)} {unit}/{'h' if per_hour else 'min'})"
        lines.append(line)
    return lines


def box_anchor(x: float, y: float, mapping: PlotMapping) -> tuple[str, float, float]:
    """Place a readout beside the cursor, flipping away from the plot edges."""

    horizontal = "w" if x < (mapping.left + mapping.right) / 2 else "e"
    vertical = "s" if y > (mapping.top + mapping.bottom) / 2 else "n"
    return (
        vertical + horizontal,
        x + (12 if horizontal == "w" else -12),
        y + (12 if vertical == "n" else -12),
    )


class GraphReader:
    """Draws the crosshair, readout and measurement for one chart canvas.

    ``frame_provider`` returns the last drawn frame. The owning chart calls
    ``redraw()`` after each refresh so the crosshair survives live updates.
    ``interactive`` enables trace selection and measuring; the single-trace
    popup leaves it off and is only a screen reader.
    """

    def __init__(
        self,
        canvas: tk.Canvas,
        frame_provider: Callable[[], GraphFrame | None],
        *,
        interactive: bool = True,
        on_selection: Callable[[TraceKey | None], None] = lambda _key: None,
    ) -> None:
        self._canvas = canvas
        self._frame_provider = frame_provider
        self._interactive = interactive
        self._on_selection = on_selection
        self.enabled = False
        self.selected: TraceKey | None = None
        self.measurement: list[ReadPoint] = []
        self._pointer: tuple[float, float] | None = None
        self._last_select: tuple[float, float] | None = None
        self._vertical = canvas.create_line(0, 0, 0, 0, dash=(3, 3), fill=MUTED_TEXT, state="hidden")
        self._horizontal = canvas.create_line(0, 0, 0, 0, dash=(3, 3), fill=MUTED_TEXT, state="hidden")
        self._dot = canvas.create_oval(0, 0, 0, 0, outline=NAVY_INK, width=2, state="hidden")
        self._box = canvas.create_rectangle(0, 0, 0, 0, fill="#FFFFF0", outline="#BBBBBB", state="hidden")
        self._text = canvas.create_text(0, 0, fill=NAVY_INK, state="hidden", font=("Segoe UI", 9))
        self._marks = tuple(
            canvas.create_line(0, 0, 0, 0, fill=NAVY_INK, width=2, state="hidden") for _ in range(4)
        )
        self._span = canvas.create_line(0, 0, 0, 0, fill=NAVY_INK, dash=(2, 2), state="hidden")
        self._result_box = canvas.create_rectangle(0, 0, 0, 0, fill="#FFFFF0", outline=NAVY_INK, state="hidden")
        self._result = canvas.create_text(0, 0, fill=NAVY_INK, anchor="nw", state="hidden",
                                          font=("Segoe UI", 9))
        canvas.bind("<Motion>", self._on_motion, add="+")
        canvas.bind("<Leave>", self._on_leave, add="+")
        canvas.bind("<Enter>", self._on_enter, add="+")
        if interactive:
            canvas.bind("<Double-Button-1>", self._on_double_click, add="+")
            canvas.bind("<Shift-Button-1>", self._on_shift_click, add="+")
            canvas.bind("<Escape>", self._on_escape, add="+")

    @property
    def item_count(self) -> int:
        return 12

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self.clear()
        self._canvas.configure(cursor="crosshair" if enabled else "")
        self.redraw()

    def clear(self) -> None:
        changed = self.selected is not None
        self.selected = None
        self.measurement = []
        self._last_select = None
        if changed:
            self._on_selection(None)
        self.redraw()

    def redraw(self) -> None:
        frame = self._frame_provider() if self.enabled else None
        self._draw_crosshair(frame)
        self._draw_measurement(frame)

    # Events -------------------------------------------------------------

    def _on_enter(self, _event: tk.Event) -> None:
        if self.enabled and self._interactive:
            self._canvas.focus_set()  # So Esc reaches the canvas.

    def _on_motion(self, event: tk.Event) -> None:
        self._pointer = (event.x, event.y)
        if self.enabled:
            self._draw_crosshair(self._frame_provider())

    def _on_leave(self, _event: tk.Event) -> None:
        self._pointer = None
        if self.enabled:
            self._draw_crosshair(None)

    def _on_double_click(self, event: tk.Event) -> str | None:
        frame = self._frame_provider()
        if not self.enabled or frame is None or not frame.mapping.contains(event.x, event.y):
            return None
        last = self._last_select
        same_spot = last is not None and (
            abs(last[0] - event.x) <= SAME_SPOT_DISTANCE
            and abs(last[1] - event.y) <= SAME_SPOT_DISTANCE
        )
        chosen = choose_trace(traces_near(frame, event.x, event.y), self.selected, same_spot)
        self._last_select = (event.x, event.y) if chosen is not None else None
        if chosen != self.selected:
            self.selected = chosen
            # A measurement made on one trace means nothing on another.
            self.measurement = []
            self._on_selection(chosen)
        self.redraw()
        return "break"

    def _on_shift_click(self, event: tk.Event) -> str | None:
        frame = self._frame_provider()
        if not self.enabled or frame is None or not frame.mapping.contains(event.x, event.y):
            return None
        point = self._point_at(frame, event.x, event.y)
        if point is not None:
            self.measurement = [point] if len(self.measurement) != 1 else [*self.measurement, point]
        self._draw_measurement(frame)
        return "break"

    def _on_escape(self, _event: tk.Event) -> str | None:
        if not self.enabled:
            return None
        self.clear()
        return "break"

    # Drawing ------------------------------------------------------------

    def _point_at(self, frame: GraphFrame, x: float, y: float) -> ReadPoint | None:
        if self.selected is not None:
            return snapped_point(frame, self.selected, x)
        return screen_point(frame, x, y)

    def _screen_xy(self, frame: GraphFrame, point: ReadPoint) -> tuple[float, float] | None:
        axis = frame.axis(point.values[0][1]) if point.values else None
        if axis is None:
            return None
        return frame.mapping.x_for_time(point.time), axis.y_for_value(point.values[0][0], frame.mapping)

    def _draw_crosshair(self, frame: GraphFrame | None) -> None:
        items = (self._vertical, self._horizontal, self._dot, self._box, self._text)
        pointer = self._pointer
        if frame is None or pointer is None or not frame.mapping.contains(*pointer):
            for item in items:
                self._canvas.itemconfigure(item, state="hidden")
            return
        mapping = frame.mapping
        point = self._point_at(frame, *pointer)
        if point is None:  # The selected trace has gone from view.
            point = screen_point(frame, *pointer)
        x, y = pointer
        if point.snapped:
            x, y = self._screen_xy(frame, point) or pointer
            self._canvas.coords(self._dot, x - 4, y - 4, x + 4, y + 4)
            self._canvas.itemconfigure(self._dot, state="normal")
        else:
            self._canvas.itemconfigure(self._dot, state="hidden")
        self._canvas.coords(self._vertical, x, mapping.top, x, mapping.bottom)
        self._canvas.coords(self._horizontal, mapping.left, y, mapping.right, y)
        text = describe_point(point)
        if point.snapped:
            text = f"{frame.traces[self.selected].label}\n{text}"
        anchor, text_x, text_y = box_anchor(x, y, mapping)
        self._canvas.coords(self._text, text_x, text_y)
        # Shown before measuring it: a hidden item has no bounding box.
        self._canvas.itemconfigure(self._text, text=text, anchor=anchor, state="normal")
        left, top, right, bottom = self._canvas.bbox(self._text) or (0, 0, 0, 0)
        self._canvas.coords(self._box, left - 4, top - 2, right + 4, bottom + 2)
        for item in items[:2] + items[3:]:
            self._canvas.itemconfigure(item, state="normal")
        for item in items:
            self._canvas.tag_raise(item)

    def _draw_measurement(self, frame: GraphFrame | None) -> None:
        drawn = []
        if frame is not None:
            drawn = [xy for point in self.measurement if (xy := self._screen_xy(frame, point))]
        for index, mark in enumerate(self._marks):
            if index // 2 < len(drawn):
                x, y = drawn[index // 2]
                if index % 2 == 0:
                    self._canvas.coords(mark, x - 6, y - 6, x + 6, y + 6)
                else:
                    self._canvas.coords(mark, x - 6, y + 6, x + 6, y - 6)
                self._canvas.itemconfigure(mark, state="normal")
                self._canvas.tag_raise(mark)
            else:
                self._canvas.itemconfigure(mark, state="hidden")
        if len(drawn) == 2 and frame is not None:
            self._canvas.coords(self._span, *drawn[0], *drawn[1])
            text = "\n".join(describe_change(*self.measurement))
            self._canvas.coords(self._result, frame.mapping.left + 8, frame.mapping.top + 6)
            self._canvas.itemconfigure(self._result, text=text, state="normal")
            left, top, right, bottom = self._canvas.bbox(self._result) or (0, 0, 0, 0)
            self._canvas.coords(self._result_box, left - 4, top - 2, right + 4, bottom + 2)
            for item in (self._span, self._result_box, self._result):
                self._canvas.itemconfigure(item, state="normal")
                self._canvas.tag_raise(item)
        else:
            for item in (self._span, self._result_box, self._result):
                self._canvas.itemconfigure(item, state="hidden")
