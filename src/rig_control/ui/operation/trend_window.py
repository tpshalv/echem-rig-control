from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Protocol

from rig_control.ui.common.theme import ERROR_TEXT, INFO_TEXT, MUTED_TEXT, TITLE_FONT


MAX_RENDERED_POINTS = 2_000
TIME_RANGES = {
    "Last 10 minutes": 600,
    "Last 30 minutes": 1_800,
    "Last hour": 3_600,
    "Last 8 hours": 28_800,
    "Whole run": None,
}


def padded_value_range(
    minimum: float,
    maximum: float,
    unit: str = "",
) -> tuple[float, float]:
    """Add modest visual breathing room without producing absurd bounds."""

    span = maximum - minimum
    magnitude = max(abs(minimum), abs(maximum))
    if span > 0:
        padding = max(span * 0.1, magnitude * 0.01, 0.01)
    else:
        padding = max(magnitude * 0.05, 0.1)
    lower, upper = minimum - padding, maximum + padding
    normalised_unit = unit.strip().lower().replace(" ", "")
    if "%" in normalised_unit or normalised_unit in {
        "percent",
        "percentage",
        "rh",
        "%rh",
    }:
        lower = max(0.0, lower)
        upper = min(100.0, upper)
    if upper <= lower:
        # Handles a percentage signal fixed exactly at 0 or 100.
        if lower >= 100:
            lower, upper = 95.0, 100.0
        else:
            lower, upper = 0.0, 5.0
    return lower, upper


class TrendCanvasRenderer:
    """Reuse a bounded set of Tk canvas items across chart refreshes."""

    def __init__(self, canvas) -> None:
        self._canvas = canvas
        self._axis = canvas.create_line(0, 0, 0, 0, state="hidden")
        self._series = canvas.create_line(0, 0, 0, 0, state="hidden")
        self._empty = canvas.create_text(0, 0, state="hidden")
        self._maximum = canvas.create_text(0, 0, state="hidden")
        self._minimum = canvas.create_text(0, 0, state="hidden")
        self._first_time = canvas.create_text(0, 0, state="hidden")
        self._last_time = canvas.create_text(0, 0, state="hidden")
        self._points: list[int] = []

    @property
    def item_count(self) -> int:
        return 7 + len(self._points)

    def show_empty(self, width: int, height: int) -> None:
        self._canvas.coords(self._empty, width / 2, height / 2)
        self._canvas.itemconfigure(
            self._empty,
            text="No readings available yet",
            fill=MUTED_TEXT,
            state="normal",
        )
        self._set_plot_state("hidden")

    def draw(
        self,
        *,
        bounds: tuple[float, float, float, float],
        points: list[tuple[float, float]],
        colours: list[str],
        maximum_text: str,
        minimum_text: str,
        first_time_text: str,
        last_time_text: str,
    ) -> None:
        left, top, right, bottom = bounds
        self._canvas.itemconfigure(self._empty, state="hidden")
        self._canvas.coords(self._axis, left, top, left, bottom, right, bottom)
        self._canvas.itemconfigure(
            self._axis,
            fill=MUTED_TEXT,
            state="normal",
        )
        if len(points) > 1:
            self._canvas.coords(
                self._series,
                *[value for pair in points for value in pair],
            )
            self._canvas.itemconfigure(
                self._series,
                fill=INFO_TEXT,
                width=2,
                state="normal",
            )
        else:
            self._canvas.itemconfigure(self._series, state="hidden")

        while len(self._points) < len(points):
            self._points.append(
                self._canvas.create_oval(0, 0, 0, 0, state="hidden")
            )
        for index, item in enumerate(self._points):
            if index >= len(points):
                self._canvas.itemconfigure(item, state="hidden")
                continue
            x, y = points[index]
            colour = colours[index]
            self._canvas.coords(item, x - 2, y - 2, x + 2, y + 2)
            self._canvas.itemconfigure(
                item,
                fill=colour,
                outline=colour,
                state="normal",
            )

        labels = (
            (self._maximum, left - 8, top, maximum_text, "e"),
            (self._minimum, left - 8, bottom, minimum_text, "e"),
            (self._first_time, left, bottom + 18, first_time_text, "w"),
            (self._last_time, right, bottom + 18, last_time_text, "e"),
        )
        for item, x, y, text, anchor in labels:
            self._canvas.coords(item, x, y)
            self._canvas.itemconfigure(
                item,
                text=text,
                anchor=anchor,
                state="normal",
            )

    def _set_plot_state(self, state: str) -> None:
        for item in (
            self._axis,
            self._series,
            self._maximum,
            self._minimum,
            self._first_time,
            self._last_time,
            *self._points,
        ):
            self._canvas.itemconfigure(item, state=state)


def subsample_readings(readings: tuple[TrendReading, ...], limit: int = MAX_RENDERED_POINTS):
    """Thin display data evenly while preserving both endpoints."""

    if len(readings) <= limit:
        return readings
    step = (len(readings) - 1) / (limit - 1)
    indices = [round(index * step) for index in range(limit)]
    indices[0] = 0
    indices[-1] = len(readings) - 1
    return tuple(readings[index] for index in indices)


class TrendReading(Protocol):
    value: float
    unit: str
    quality: str

    @property
    def timestamp(self): ...


class TrendChart(ttk.Frame):
    """Reusable current-value label and time-series canvas."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        history_provider: Callable[[], tuple[TrendReading, ...]],
    ) -> None:
        super().__init__(parent)
        self._history_provider = history_provider
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._current = ttk.Label(self, text="No readings")
        self._current.grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._canvas = tk.Canvas(
            self,
            background="white",
            highlightthickness=1,
            highlightbackground="#BBBBBB",
        )
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._renderer = TrendCanvasRenderer(self._canvas)
        self._canvas.bind("<Configure>", lambda _event: self.refresh())

    def refresh(self) -> None:
        retained = self._history_provider()
        readings = subsample_readings(retained)
        width = max(self._canvas.winfo_width(), 100)
        height = max(self._canvas.winfo_height(), 100)

        if not readings:
            self._renderer.show_empty(width, height)
            self._current.configure(text="No readings")
            return

        latest = readings[-1]
        self._current.configure(
            text=(
                f"Current: {latest.value:.6g} {latest.unit}  |  "
                f"Quality: {latest.quality.upper()}  |  "
                f"{len(retained)} retained readings"
                + (f"  |  {len(readings)} displayed" if len(readings) < len(retained) else "")
            )
        )

        left, top, right, bottom = 64, 22, width - 18, height - 42
        if right <= left or bottom <= top:
            return
        values = [reading.value for reading in readings]
        minimum = min(values)
        maximum = max(values)
        minimum, maximum = padded_value_range(
            minimum,
            maximum,
            latest.unit,
        )

        timestamps = [reading.timestamp.timestamp() for reading in readings]
        first_time = timestamps[0]
        time_span = timestamps[-1] - first_time

        def point(index: int) -> tuple[float, float]:
            if time_span > 0:
                x_fraction = (timestamps[index] - first_time) / time_span
            elif len(readings) > 1:
                x_fraction = index / (len(readings) - 1)
            else:
                x_fraction = 0.5
            y_fraction = (values[index] - minimum) / (maximum - minimum)
            return (
                left + x_fraction * (right - left),
                bottom - y_fraction * (bottom - top),
            )

        points = [point(index) for index in range(len(readings))]
        self._renderer.draw(
            bounds=(left, top, right, bottom),
            points=points,
            colours=[
                INFO_TEXT if reading.quality == "good" else ERROR_TEXT
                for reading in readings
            ],
            maximum_text=f"{maximum:.6g}",
            minimum_text=f"{minimum:.6g}",
            first_time_text=readings[0]
            .timestamp.astimezone()
            .strftime("%H:%M:%S"),
            last_time_text=readings[-1]
            .timestamp.astimezone()
            .strftime("%H:%M:%S"),
        )


class TrendWindow:
    """Small live plot for one device measurement channel."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        device_id: str,
        channel: str,
        history_provider: Callable[[int | None], tuple[TrendReading, ...]],
        on_close: Callable[[], None],
        history_limit: int = 120,
    ) -> None:
        self._on_close = on_close
        self._window = tk.Toplevel(parent)
        self._window.title(
            f"Live trend — {device_id} / {channel.replace('_', ' ')}"
        )
        self._window.geometry("700x420")
        self._window.minsize(450, 300)
        self._window.columnconfigure(0, weight=1)
        self._window.rowconfigure(1, weight=1)
        self._window.protocol("WM_DELETE_WINDOW", self.close)
        self._window.bind("<Escape>", lambda _event: self.close())

        heading = ttk.Frame(self._window, padding=(12, 10, 12, 4))
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading,
            text=f"{device_id} — {channel.replace('_', ' ').title()}",
            font=TITLE_FONT,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(heading, text="Show:").grid(row=0, column=1, padx=(12, 4))
        self._time_range = tk.StringVar(value="Last 10 minutes")
        range_picker = ttk.Combobox(
            heading,
            textvariable=self._time_range,
            values=tuple(TIME_RANGES),
            state="readonly",
            width=16,
        )
        range_picker.grid(row=0, column=2)
        range_picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self._history_provider = history_provider
        self._chart = TrendChart(
            self._window,
            history_provider=self._visible_history,
        )
        self._chart.grid(
            row=1, column=0, sticky="nsew", padx=12, pady=(4, 12)
        )
        ttk.Label(
            self._window,
            text=(
                "Up to 500 representative points are displayed; full-resolution "
                "data is retained in the experiment recording."
            ),
            foreground=MUTED_TEXT,
        ).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 8))
        self.refresh()

    def _visible_history(self) -> tuple[TrendReading, ...]:
        return self._history_provider(TIME_RANGES[self._time_range.get()])

    @property
    def exists(self) -> bool:
        return bool(self._window.winfo_exists())

    def refresh(self) -> None:
        if not self.exists:
            return
        self._chart.refresh()

    def focus(self) -> None:
        self._window.deiconify()
        self._window.lift()
        self._window.focus_set()

    def close(self) -> None:
        if self.exists:
            self._window.destroy()
        self._on_close()
