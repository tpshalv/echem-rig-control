from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from math import ceil, floor
from tkinter import messagebox, ttk

from rig_control.ui.common.theme import INFO_TEXT, MUTED_TEXT, SECTION_FONT
from rig_control.ui.operation.trend_window import (
    TIME_RANGES,
    padded_value_range,
    subsample_readings,
)


LIVE_POINT_LIMIT = 500
MAX_TRACES_PER_PANEL = 8
TRACE_COLOURS = (
    "#0067A3",
    "#D1495B",
    "#2E8B57",
    "#8A5FBF",
    "#D17A00",
    "#008C95",
    "#A23B72",
    "#596157",
)
TIME_TICK_INTERVALS = (
    1,
    2,
    5,
    10,
    20,
    30,
    60,
    120,
    300,
    600,
    1_800,
    3_600,
    7_200,
    21_600,
    43_200,
)


def time_axis_ticks(
    start: float,
    end: float,
    *,
    maximum_count: int = 7,
) -> tuple[tuple[float, str], ...]:
    """Return clock-aligned ticks using a human-friendly time interval."""

    span = max(0.0, end - start)
    target = span / max(1, maximum_count - 1)
    interval = next(
        (candidate for candidate in TIME_TICK_INTERVALS if candidate >= target),
        TIME_TICK_INTERVALS[-1],
    )
    first = ceil(start / interval) * interval
    last = floor(end / interval) * interval
    if last < first:
        first = last = round(((start + end) / 2) / interval) * interval
    time_format = "%H:%M:%S" if interval < 60 else "%H:%M"
    return tuple(
        (timestamp, datetime.fromtimestamp(timestamp).strftime(time_format))
        for timestamp in range(int(first), int(last) + 1, interval)
    )


@dataclass(frozen=True, slots=True)
class DashboardSignal:
    device_id: str
    channel: str
    label: str
    unit: str
    group: str
    is_setpoint: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.device_id, self.channel)


def quadrant_layout(count: int) -> tuple[tuple[int, int, int], ...]:
    """Return row, column and column-span for one to four panels."""

    if count == 1:
        return ((0, 0, 2),)
    if count == 2:
        return ((0, 0, 1), (0, 1, 1))
    if count == 3:
        return ((0, 0, 2), (1, 0, 1), (1, 1, 1))
    if count == 4:
        return ((0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 1))
    raise ValueError("Dashboard must contain between one and four quadrants")


class MultiTraceCanvasRenderer:
    """Persistent canvas renderer supporting at most two unit scales."""

    def __init__(self, canvas) -> None:
        self._canvas = canvas
        self._axis = canvas.create_line(0, 0, 0, 0, state="hidden")
        self._right_axis = canvas.create_line(0, 0, 0, 0, state="hidden")
        self._empty = canvas.create_text(0, 0, state="hidden")
        self._left_maximum = canvas.create_text(0, 0, state="hidden")
        self._left_minimum = canvas.create_text(0, 0, state="hidden")
        self._right_maximum = canvas.create_text(0, 0, state="hidden")
        self._right_minimum = canvas.create_text(0, 0, state="hidden")
        self._time_ticks = tuple(
            (
                canvas.create_line(0, 0, 0, 0, state="hidden"),
                canvas.create_text(0, 0, state="hidden"),
            )
            for _ in range(7)
        )
        self._lines: dict[tuple[str, str], int] = {}

    @property
    def item_count(self) -> int:
        return 21 + len(self._lines)

    def draw(
        self,
        *,
        width: int,
        height: int,
        traces: tuple[
            tuple[DashboardSignal, tuple[object, ...], str], ...
        ],
        view_start: float | None,
        view_end: float | None,
    ) -> tuple[float, float] | None:
        for item in self._lines.values():
            self._canvas.itemconfigure(item, state="hidden")
        usable = tuple(
            (signal, readings, colour)
            for signal, readings, colour in traces
            if readings
        )
        if not usable:
            self._show_empty(width, height)
            return None

        all_times = [
            reading.timestamp.timestamp()
            for _signal, readings, _colour in usable
            for reading in readings
        ]
        data_start, data_end = min(all_times), max(all_times)
        selected_start = data_start if view_start is None else max(data_start, view_start)
        selected_end = data_end if view_end is None else min(data_end, view_end)
        if selected_end <= selected_start:
            selected_start, selected_end = data_start, data_end

        visible: list[tuple[DashboardSignal, tuple[object, ...], str]] = []
        for signal, readings, colour in usable:
            selected = tuple(
                reading
                for reading in readings
                if selected_start <= reading.timestamp.timestamp() <= selected_end
            )
            if selected:
                visible.append(
                    (signal, subsample_readings(selected, LIVE_POINT_LIMIT), colour)
                )
        if not visible:
            self._show_empty(width, height)
            return (selected_start, selected_end)

        units = tuple(dict.fromkeys(signal.unit or "value" for signal, _, _ in visible))
        left_unit = units[0]
        right_unit = units[1] if len(units) > 1 else None
        ranges: dict[str, tuple[float, float]] = {}
        for unit in units[:2]:
            values = [
                float(reading.value)
                for signal, readings, _colour in visible
                if (signal.unit or "value") == unit
                for reading in readings
            ]
            minimum, maximum = min(values), max(values)
            ranges[unit] = padded_value_range(minimum, maximum, unit)

        left, top = 58, 18
        right = width - (58 if right_unit else 16)
        bottom = height - 34
        self._canvas.itemconfigure(self._empty, state="hidden")
        self._canvas.coords(self._axis, left, top, left, bottom, right, bottom)
        self._canvas.itemconfigure(self._axis, fill=MUTED_TEXT, state="normal")
        if right_unit:
            self._canvas.coords(self._right_axis, right, top, right, bottom)
            self._canvas.itemconfigure(
                self._right_axis, fill=MUTED_TEXT, state="normal"
            )
        else:
            self._canvas.itemconfigure(self._right_axis, state="hidden")

        span = max(selected_end - selected_start, 1e-9)
        for signal, readings, colour in visible:
            unit = signal.unit or "value"
            if unit not in ranges:
                continue
            minimum, maximum = ranges[unit]
            coordinates: list[float] = []
            for reading in readings:
                x = left + (
                    (reading.timestamp.timestamp() - selected_start) / span
                ) * (right - left)
                y = bottom - (
                    (float(reading.value) - minimum) / (maximum - minimum)
                ) * (bottom - top)
                coordinates.extend((x, y))
            item = self._lines.get(signal.key)
            if item is None:
                item = self._canvas.create_line(0, 0, 0, 0, state="hidden")
                self._lines[signal.key] = item
            if len(coordinates) >= 4:
                self._canvas.coords(item, *coordinates)
                self._canvas.itemconfigure(
                    item,
                    fill=colour,
                    width=2,
                    dash=(6, 4) if signal.is_setpoint else (),
                    state="normal",
                )

        left_min, left_max = ranges[left_unit]
        self._set_text(
            self._left_maximum,
            left - 6,
            top,
            f"{left_max:.5g} {left_unit}",
            "e",
        )
        self._set_text(
            self._left_minimum,
            left - 6,
            bottom,
            f"{left_min:.5g} {left_unit}",
            "e",
        )
        if right_unit:
            right_min, right_max = ranges[right_unit]
            self._set_text(
                self._right_maximum,
                right + 6,
                top,
                f"{right_max:.5g} {right_unit}",
                "w",
            )
            self._set_text(
                self._right_minimum,
                right + 6,
                bottom,
                f"{right_min:.5g} {right_unit}",
                "w",
            )
        else:
            self._canvas.itemconfigure(self._right_maximum, state="hidden")
            self._canvas.itemconfigure(self._right_minimum, state="hidden")
        ticks = time_axis_ticks(selected_start, selected_end)
        for index, (mark, label) in enumerate(self._time_ticks):
            if index >= len(ticks):
                self._canvas.itemconfigure(mark, state="hidden")
                self._canvas.itemconfigure(label, state="hidden")
                continue
            timestamp, text = ticks[index]
            x = left + (timestamp - selected_start) / span * (right - left)
            self._canvas.coords(mark, x, bottom, x, bottom + 4)
            self._canvas.itemconfigure(mark, fill=MUTED_TEXT, state="normal")
            self._set_text(label, x, bottom + 7, text, "n")
        return (selected_start, selected_end)

    def _show_empty(self, width: int, height: int) -> None:
        for item in (
            self._axis,
            self._right_axis,
            self._left_maximum,
            self._left_minimum,
            self._right_maximum,
            self._right_minimum,
            *(item for pair in self._time_ticks for item in pair),
        ):
            self._canvas.itemconfigure(item, state="hidden")
        self._canvas.coords(self._empty, width / 2, height / 2)
        self._canvas.itemconfigure(
            self._empty,
            text="Add one or more live signals",
            fill=MUTED_TEXT,
            state="normal",
        )

    def _set_text(
        self,
        item: int,
        x: float,
        y: float,
        text: str,
        anchor: str,
    ) -> None:
        self._canvas.coords(item, x, y)
        self._canvas.itemconfigure(
            item, text=text, anchor=anchor, fill=MUTED_TEXT, state="normal"
        )


class DashboardPanel(ttk.LabelFrame):
    def __init__(self, parent, *, signal_provider, history_provider) -> None:
        super().__init__(parent, text="LIVE GRAPH", padding=6)
        self._signal_provider = signal_provider
        self._history_provider = history_provider
        self._selected: dict[tuple[str, str], DashboardSignal] = {}
        self._visible: dict[tuple[str, str], tk.BooleanVar] = {}
        self._legend_widgets: list[tk.Widget] = []
        self._view_start: float | None = None
        self._view_end: float | None = None
        self._follow_live = tk.BooleanVar(value=True)
        self._time_range = tk.StringVar(value="Last 10 minutes")
        self._pan_origin: tuple[int, float, float] | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        self._group = ttk.Combobox(
            controls,
            values=("All", "Temperatures", "Flows", "Humidity", "Pressure", "Electrical"),
            state="readonly",
            width=14,
        )
        self._group.set("All")
        self._group.grid(row=0, column=0, padx=(0, 4))
        self._signal = ttk.Combobox(controls, state="readonly")
        self._signal.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(controls, text="Add", command=self._add_selected).grid(
            row=0, column=2, padx=(0, 4)
        )
        range_picker = ttk.Combobox(
            controls,
            textvariable=self._time_range,
            values=tuple(TIME_RANGES),
            state="readonly",
            width=16,
        )
        range_picker.grid(row=0, column=3, padx=(0, 4))
        range_picker.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.reset_view(),
        )
        ttk.Button(controls, text="Reset", command=self.reset_view).grid(
            row=0, column=4
        )
        ttk.Button(controls, text="Clear", command=self._clear_signals).grid(
            row=0, column=5, padx=(4, 0)
        )
        ttk.Checkbutton(
            controls,
            text="Follow live",
            variable=self._follow_live,
            command=self.refresh,
        ).grid(row=0, column=6, padx=(8, 0))
        self._group.bind("<<ComboboxSelected>>", lambda _event: self._refresh_choices())

        self._legend = ttk.Frame(self)
        self._legend.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        self._canvas = tk.Canvas(
            self,
            background="white",
            highlightthickness=1,
            highlightbackground="#BBBBBB",
        )
        self._canvas.grid(row=2, column=0, sticky="nsew")
        ttk.Label(
            self,
            text=(
                "Up to 500 representative points are displayed for this period; "
                "full-resolution data is retained in the experiment recording."
            ),
            foreground=MUTED_TEXT,
        ).grid(row=3, column=0, sticky="w", pady=(3, 0))
        self._renderer = MultiTraceCanvasRenderer(self._canvas)
        self._canvas.bind("<Configure>", lambda _event: self.refresh())
        self._canvas.bind("<MouseWheel>", self._zoom)
        self._canvas.bind("<ButtonPress-1>", self._start_pan)
        self._canvas.bind("<B1-Motion>", self._pan)
        self._refresh_choices()

    def refresh(self) -> None:
        self._refresh_choices()
        traces = []
        for index, signal in enumerate(self._selected.values()):
            if not self._visible[signal.key].get():
                continue
            traces.append(
                (
                    signal,
                    self._history_provider(
                        *signal.key,
                        TIME_RANGES[self._time_range.get()],
                    ),
                    TRACE_COLOURS[index % len(TRACE_COLOURS)],
                )
            )
        if self._follow_live.get():
            self._view_start = None
            self._view_end = None
        selected = self._renderer.draw(
            width=max(self._canvas.winfo_width(), 100),
            height=max(self._canvas.winfo_height(), 100),
            traces=tuple(traces),
            view_start=self._view_start,
            view_end=self._view_end,
        )
        if selected is not None and not self._follow_live.get():
            self._view_start, self._view_end = selected

    def reset_view(self) -> None:
        self._view_start = None
        self._view_end = None
        self._follow_live.set(True)
        self.refresh()

    def diagnostic_metrics(self) -> dict[str, int]:
        return {
            "selected_traces": len(self._selected),
            "visible_traces": sum(
                variable.get() for variable in self._visible.values()
            ),
            "canvas_items": self._renderer.item_count,
        }

    def _refresh_choices(self) -> None:
        group = self._group.get()
        choices = tuple(
            signal
            for signal in self._signal_provider()
            if group == "All" or signal.group == group
        )
        self._choice_map = {signal.label: signal for signal in choices}
        self._signal.configure(values=tuple(self._choice_map))
        if self._signal.get() not in self._choice_map:
            self._signal.set(next(iter(self._choice_map), ""))

    def _add_selected(self) -> None:
        signal = self._choice_map.get(self._signal.get())
        if signal is None or signal.key in self._selected:
            return
        if len(self._selected) >= MAX_TRACES_PER_PANEL:
            messagebox.showinfo(
                "Graph is full",
                f"A graph can contain up to {MAX_TRACES_PER_PANEL} signals.",
                parent=self,
            )
            return
        units = {item.unit or "value" for item in self._selected.values()}
        units.add(signal.unit or "value")
        if len(units) > 2:
            messagebox.showinfo(
                "Too many unit scales",
                "A graph can use at most two different units. Use another quadrant for the additional signal.",
                parent=self,
            )
            return
        self._selected[signal.key] = signal
        self._visible[signal.key] = tk.BooleanVar(value=True)
        self._rebuild_legend()
        self.refresh()

    def _rebuild_legend(self) -> None:
        for widget in self._legend_widgets:
            widget.destroy()
        self._legend_widgets.clear()
        for column, signal in enumerate(self._selected.values()):
            colour = TRACE_COLOURS[column % len(TRACE_COLOURS)]
            checkbox = tk.Checkbutton(
                self._legend,
                text=signal.label,
                variable=self._visible[signal.key],
                command=self.refresh,
                foreground=colour,
                selectcolor="white",
            )
            checkbox.grid(row=0, column=column, sticky="w", padx=(0, 8))
            self._legend_widgets.append(checkbox)

    def _clear_signals(self) -> None:
        self._selected.clear()
        self._visible.clear()
        self._rebuild_legend()
        self.reset_view()

    def _zoom(self, event: tk.Event) -> str:
        if self._view_start is None or self._view_end is None:
            self._follow_live.set(False)
            self.refresh()
        if self._view_start is None or self._view_end is None:
            return "break"
        factor = 0.8 if event.delta > 0 else 1.25
        width = max(self._canvas.winfo_width(), 1)
        fraction = min(max(event.x / width, 0.0), 1.0)
        span = self._view_end - self._view_start
        anchor = self._view_start + span * fraction
        new_span = max(span * factor, 1.0)
        self._view_start = anchor - new_span * fraction
        self._view_end = self._view_start + new_span
        self._follow_live.set(False)
        self.refresh()
        return "break"

    def _start_pan(self, event: tk.Event) -> None:
        if self._view_start is None or self._view_end is None:
            self._follow_live.set(False)
            self.refresh()
        if self._view_start is not None and self._view_end is not None:
            self._pan_origin = (event.x, self._view_start, self._view_end)

    def _pan(self, event: tk.Event) -> None:
        if self._pan_origin is None:
            return
        origin_x, start, end = self._pan_origin
        shift = -(event.x - origin_x) / max(self._canvas.winfo_width(), 1) * (end - start)
        self._view_start = start + shift
        self._view_end = end + shift
        self._follow_live.set(False)
        self.refresh()


class LiveDashboardWindow:
    def __init__(self, parent, *, signal_provider, history_provider, on_close) -> None:
        self._on_close = on_close
        self._window = tk.Toplevel(parent)
        self._window.title("Comprehensive live dashboard")
        self._window.geometry("1200x760")
        self._window.minsize(850, 560)
        self._window.protocol("WM_DELETE_WINDOW", self.close)
        self._window.columnconfigure(0, weight=1)
        self._window.rowconfigure(1, weight=1)

        header = ttk.Frame(self._window, padding=(10, 8))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Live dashboard", font=SECTION_FONT).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Label(header, text="Quadrants:").grid(row=0, column=1)
        self._count = tk.IntVar(value=4)
        count = ttk.Spinbox(
            header,
            from_=1,
            to=4,
            width=4,
            textvariable=self._count,
            command=self._apply_layout,
        )
        count.grid(row=0, column=2, padx=(4, 12))
        count.bind("<Return>", lambda _event: self._apply_layout())
        ttk.Label(
            header,
            text="Mouse wheel: zoom time  •  drag: pan  •  Reset: return to live",
        ).grid(row=0, column=3)

        self._body = ttk.Frame(self._window, padding=(8, 0, 8, 8))
        self._body.grid(row=1, column=0, sticky="nsew")
        self._panels = tuple(
            DashboardPanel(
                self._body,
                signal_provider=signal_provider,
                history_provider=history_provider,
            )
            for _ in range(4)
        )
        self._apply_layout()

    @property
    def exists(self) -> bool:
        return bool(self._window.winfo_exists())

    def focus(self) -> None:
        self._window.deiconify()
        self._window.lift()
        self._window.focus_set()

    def refresh(self) -> None:
        if not self.exists:
            return
        for panel in self._panels[: self._valid_count()]:
            panel.refresh()

    def close(self) -> None:
        if self.exists:
            self._window.destroy()
        self._on_close()

    def diagnostic_metrics(self) -> dict[str, int]:
        active = self._panels[: self._valid_count()]
        panel_metrics = tuple(panel.diagnostic_metrics() for panel in active)
        return {
            "active_quadrants": len(active),
            "selected_traces": sum(
                metrics["selected_traces"] for metrics in panel_metrics
            ),
            "visible_traces": sum(
                metrics["visible_traces"] for metrics in panel_metrics
            ),
            "canvas_items": sum(
                metrics["canvas_items"] for metrics in panel_metrics
            ),
        }

    def _valid_count(self) -> int:
        try:
            return min(max(int(self._count.get()), 1), 4)
        except (TypeError, ValueError, tk.TclError):
            return 4

    def _apply_layout(self) -> None:
        count = self._valid_count()
        self._count.set(count)
        for panel in self._panels:
            panel.grid_forget()
        for row in range(2):
            self._body.rowconfigure(row, weight=1)
        for column in range(2):
            self._body.columnconfigure(column, weight=1)
        for panel, (row, column, span) in zip(
            self._panels,
            quadrant_layout(count),
            strict=False,
        ):
            panel.grid(
                row=row,
                column=column,
                columnspan=span,
                sticky="nsew",
                padx=4,
                pady=4,
            )
