import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Protocol

from rig_control.ui.common.theme import ERROR_TEXT, INFO_TEXT, MUTED_TEXT, TITLE_FONT


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
        self._canvas.bind("<Configure>", lambda _event: self.refresh())

    def refresh(self) -> None:
        readings = self._history_provider()
        self._canvas.delete("all")
        width = max(self._canvas.winfo_width(), 100)
        height = max(self._canvas.winfo_height(), 100)

        if not readings:
            self._canvas.create_text(
                width / 2,
                height / 2,
                text="No readings available yet",
                fill=MUTED_TEXT,
            )
            self._current.configure(text="No readings")
            return

        latest = readings[-1]
        self._current.configure(
            text=(
                f"Current: {latest.value:.6g} {latest.unit}  |  "
                f"Quality: {latest.quality.upper()}  |  "
                f"{len(readings)} retained readings"
            )
        )

        left, top, right, bottom = 64, 22, width - 18, height - 42
        if right <= left or bottom <= top:
            return
        values = [reading.value for reading in readings]
        minimum = min(values)
        maximum = max(values)
        if maximum == minimum:
            padding = max(abs(maximum) * 0.05, 1.0)
            minimum -= padding
            maximum += padding

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

        self._canvas.create_line(
            left, top, left, bottom, right, bottom, fill=MUTED_TEXT
        )
        points = [point(index) for index in range(len(readings))]
        if len(points) > 1:
            self._canvas.create_line(
                *[value for pair in points for value in pair],
                fill=INFO_TEXT,
                width=2,
            )
        for reading, (x, y) in zip(readings, points, strict=True):
            colour = INFO_TEXT if reading.quality == "good" else ERROR_TEXT
            self._canvas.create_oval(
                x - 2, y - 2, x + 2, y + 2, fill=colour, outline=colour
            )

        self._canvas.create_text(
            left - 8, top, text=f"{maximum:.6g}", anchor="e"
        )
        self._canvas.create_text(
            left - 8, bottom, text=f"{minimum:.6g}", anchor="e"
        )
        self._canvas.create_text(
            left,
            bottom + 18,
            text=readings[0].timestamp.astimezone().strftime("%H:%M:%S"),
            anchor="w",
        )
        self._canvas.create_text(
            right,
            bottom + 18,
            text=readings[-1].timestamp.astimezone().strftime("%H:%M:%S"),
            anchor="e",
        )


class TrendWindow:
    """Small live plot for one device measurement channel."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        device_id: str,
        channel: str,
        history_provider: Callable[[], tuple[TrendReading, ...]],
        on_close: Callable[[], None],
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
        self._chart = TrendChart(
            self._window,
            history_provider=history_provider,
        )
        self._chart.grid(
            row=1, column=0, sticky="nsew", padx=12, pady=(4, 12)
        )
        self.refresh()

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
