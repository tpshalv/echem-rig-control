"""Linear calibration editor, hosted by the Settings window.

One panel class serves both pump speed-to-flow and EZO-HUM CO2 RH
calibrations; only the wording differs. Enter a handful of measured
(instrument, true) points, name the calibration (today's date by default),
and review the fitted line and R^2 against the chart before deciding whether
to accept it. Accepting appends it to that device's calibration history and
makes it active; declining discards it, leaving no trace. The chart also
draws the device's previous calibrations faintly, so a drifting fit is
visible at a glance, and the saved list below it opens any past calibration
to show its points and notes.
"""

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, replace
from tkinter import messagebox, ttk

from rig_control.devices.linear_calibration import Calibration, CalibrationPoint
from rig_control.instrument_settings.calibration import (
    CalibrationCandidate,
    CalibrationSettingsService,
    today_calibration_name,
)
from rig_control.ui.common.theme import HAIRLINE, INFO_TEXT, MUTED_TEXT, SECTION_FONT, WARNING_TEXT
from rig_control.ui.instrument_settings.base import InstrumentSettingsPanel


MINIMUM_POINT_ROWS = 2


@dataclass(frozen=True, slots=True)
class CalibrationPanelText:
    """The wording that makes the shared panel read as one kind of calibration."""

    title: str
    device_label: str
    instructions: str
    x_header: str
    y_header: str
    x_unit: str
    y_unit: str


PUMP_PANEL_TEXT = CalibrationPanelText(
    title="Pump calibration",
    device_label="Pump:",
    instructions=(
        "Run the pump at a few speeds, measure the flow at each, and enter the pairs "
        "below. Fit shows the line before anything is saved; accept it to make it "
        "active, or decline to discard it."
    ),
    x_header="Speed (rpm)",
    y_header="Flow (ml/min)",
    x_unit="rpm",
    y_unit="ml/min",
)

HUMIDITY_PANEL_TEXT = CalibrationPanelText(
    title="Humidity calibration (CO2)",
    device_label="Probe:",
    instructions=(
        "EZO-HUM probes read low in a CO2 ambient. Hold the probe over saturated salt "
        "solutions under CO2, and enter what the probe reports against each salt's "
        "reference RH. Once accepted, every reading also records a CO2-corrected RH "
        "estimate next to the raw value."
    ),
    x_header="Probe RH (%)",
    y_header="Reference RH (%)",
    x_unit="%RH",
    y_unit="%RH",
)


def _draw_calibration_chart(
    canvas: tk.Canvas,
    width: int,
    height: int,
    *,
    text: CalibrationPanelText,
    history: tuple[Calibration, ...],
    candidate: CalibrationCandidate | None,
    selected: Calibration | None = None,
) -> None:
    canvas.delete("all")
    left, top, right, bottom = 56, 14, max(width - 16, 60), max(height - 34, 30)
    if right <= left or bottom <= top:
        return

    x_values = [point.x for calibration in history for point in calibration.points]
    y_values = [point.y for calibration in history for point in calibration.points]
    if candidate is not None:
        x_values += [point.x for point in candidate.points]
        y_values += [point.y for point in candidate.points]

    if not x_values:
        canvas.create_text(
            (left + right) / 2, (top + bottom) / 2,
            text="No calibration points yet", fill=MUTED_TEXT,
        )
        return

    x_span = max(max(x_values) - min(x_values), 1e-6)
    y_span = max(max(y_values) - min(y_values), 1e-6)
    min_x = min(x_values) - x_span * 0.08
    max_x = max(x_values) + x_span * 0.08
    min_y = min(0.0, min(y_values) - y_span * 0.08)
    max_y = max(y_values) + y_span * 0.08

    def x(value: float) -> float:
        return left + (value - min_x) / (max_x - min_x) * (right - left)

    def y(value: float) -> float:
        return bottom - (value - min_y) / (max_y - min_y) * (bottom - top)

    def fit_line(calibration_fit, **options) -> None:
        canvas.create_line(
            x(min_x), y(calibration_fit.y_for_x(min_x)),
            x(max_x), y(calibration_fit.y_for_x(max_x)), **options,
        )

    def dots(points, colour: str) -> None:
        for point in points:
            point_x, point_y = x(point.x), y(point.y)
            canvas.create_oval(
                point_x - 3, point_y - 3, point_x + 3, point_y + 3, fill=colour, outline=colour,
            )

    canvas.create_line(left, top, left, bottom, fill=MUTED_TEXT)
    canvas.create_line(left, bottom, right, bottom, fill=MUTED_TEXT)
    canvas.create_text(left - 6, top, anchor="ne", fill=MUTED_TEXT, text=f"{max_y:.3g}")
    canvas.create_text(left - 6, bottom, anchor="se", fill=MUTED_TEXT, text=f"{min_y:.3g} {text.y_unit}")
    canvas.create_text(left, bottom + 16, anchor="n", fill=MUTED_TEXT, text=f"{min_x:.3g}")
    canvas.create_text(right, bottom + 16, anchor="n", fill=MUTED_TEXT, text=f"{max_x:.3g} {text.x_unit}")

    previous, active = (history[:-1], history[-1]) if history else ((), None)
    for calibration in previous:
        fit_line(calibration.fit, fill=HAIRLINE, width=1, dash=(2, 2))
    if active is not None:
        fit_line(active.fit, fill=MUTED_TEXT, width=2)
    if selected is not None:
        fit_line(selected.fit, fill=WARNING_TEXT, width=2)
        dots(selected.points, WARNING_TEXT)
    if candidate is not None:
        fit_line(candidate.fit, fill=INFO_TEXT, width=2)
        dots(candidate.points, INFO_TEXT)

    canvas.create_text(
        right, top, anchor="ne", fill=MUTED_TEXT,
        text=(
            f"{len(history)} saved calibration(s)"
            + (f" — showing {selected.name}" if selected is not None else "")
            + (" — reviewing a new fit" if candidate is not None else "")
        ),
    )


class CalibrationPanel(InstrumentSettingsPanel):
    def __init__(
        self, parent: tk.Misc, service_provider: Callable[[], CalibrationSettingsService],
        scroll_from_event: Callable[[tk.Event], None],
        text: CalibrationPanelText = PUMP_PANEL_TEXT,
    ) -> None:
        super().__init__(parent, padding=(4, 0))
        self._window = self.winfo_toplevel()
        self._service_provider = service_provider
        self._scroll_from_event = scroll_from_event
        self._text = text
        self._device_var = tk.StringVar(master=self)
        self._name_var = tk.StringVar(master=self, value=today_calibration_name())
        self._operator_var = tk.StringVar(master=self)
        self._notes_var = tk.StringVar(master=self)
        self._point_rows: list[tuple[ttk.Frame, ttk.Entry, ttk.Entry]] = []
        self._candidate: CalibrationCandidate | None = None
        self._selected: Calibration | None = None
        self._history_cache: tuple[Calibration, ...] = ()
        self._history_cache_device: str | None = None
        self._build()
        self.refresh()

    @property
    def _service(self) -> CalibrationSettingsService:
        return self._service_provider()

    def refresh(self) -> None:
        device_ids = self._service.device_ids()
        self._device_chooser.configure(values=device_ids)
        if self._device_var.get() not in device_ids:
            self._device_var.set(device_ids[0] if device_ids else "")
            self._clear_candidate()
        self._refresh_chart()

    def apply_changes(self) -> bool:
        # Calibrations are accepted explicitly, not through the shared Apply
        # button - there is nothing pending here for it to commit.
        return True

    def _build(self) -> None:
        text = self._text
        ttk.Label(self, text=text.title, font=SECTION_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        chooser = ttk.Frame(self)
        chooser.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(chooser, text=text.device_label).grid(row=0, column=0, padx=(0, 6))
        self._device_chooser = ttk.Combobox(
            chooser, textvariable=self._device_var, state="readonly", width=24,
        )
        self._device_chooser.grid(row=0, column=1, padx=(0, 12))
        self._device_chooser.bind("<<ComboboxSelected>>", lambda _event: self._device_changed())
        ttk.Label(chooser, text="Operator:").grid(row=0, column=2, padx=(0, 6))
        ttk.Entry(chooser, textvariable=self._operator_var, width=18).grid(row=0, column=3)

        ttk.Label(self, text=text.instructions, wraplength=560).grid(
            row=2, column=0, sticky="w", pady=(0, 8)
        )

        self._points_frame = ttk.Frame(self)
        self._points_frame.grid(row=3, column=0, sticky="w")
        header = ttk.Frame(self._points_frame)
        header.grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=text.x_header, width=16).grid(row=0, column=0)
        ttk.Label(header, text=text.y_header, width=16).grid(row=0, column=1)
        self._rows_container = ttk.Frame(self._points_frame)
        self._rows_container.grid(row=1, column=0, sticky="w")
        for _ in range(MINIMUM_POINT_ROWS + 1):
            self._add_point_row()

        add_row = ttk.Frame(self)
        add_row.grid(row=4, column=0, sticky="w", pady=(4, 8))
        ttk.Button(add_row, text="+ Add point", command=self._add_point_row).grid(row=0, column=0)

        details = ttk.Frame(self)
        details.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(details, text="Name:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(details, textvariable=self._name_var, width=24).grid(row=0, column=1, sticky="w")
        ttk.Label(details, text="Notes (optional):").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0)
        )
        ttk.Entry(details, textvariable=self._notes_var, width=48).grid(
            row=1, column=1, sticky="ew", pady=(4, 0)
        )

        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, sticky="w", pady=(0, 8))
        ttk.Button(actions, text="Fit", command=self._fit).grid(row=0, column=0, padx=(0, 8))
        self._accept_button = ttk.Button(actions, text="Accept", command=self._accept, state="disabled")
        self._accept_button.grid(row=0, column=1, padx=(0, 8))
        self._decline_button = ttk.Button(actions, text="Decline", command=self._decline, state="disabled")
        self._decline_button.grid(row=0, column=2)

        self._status = ttk.Label(self, wraplength=560)
        self._status.grid(row=7, column=0, sticky="w", pady=(0, 8))

        self._chart = tk.Canvas(
            self, background="white", height=220, highlightthickness=1, highlightbackground=HAIRLINE,
        )
        self._chart.grid(row=8, column=0, sticky="ew", pady=(0, 8))
        self._chart.bind("<Configure>", lambda _event: self._refresh_chart(reload=False))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._chart.bind(sequence, self._redirect_wheel)

        ttk.Label(self, text="Saved calibrations (newest first)", font=SECTION_FONT).grid(
            row=9, column=0, sticky="w", pady=(4, 4)
        )
        columns = ("name", "created", "operator", "fit", "notes")
        self._saved = ttk.Treeview(self, columns=columns, show="headings", height=5, selectmode="browse")
        for column, heading, width in (
            ("name", "Name", 110), ("created", "Saved", 120), ("operator", "Operator", 80),
            ("fit", "Fit", 190), ("notes", "Notes", 220),
        ):
            self._saved.heading(column, text=heading)
            self._saved.column(column, width=width, stretch=column == "notes")
        self._saved.grid(row=10, column=0, sticky="ew", pady=(0, 4))
        self._saved.bind("<<TreeviewSelect>>", lambda _event: self._show_selected())
        self._saved_detail = ttk.Label(self, wraplength=560, foreground=MUTED_TEXT)
        self._saved_detail.grid(row=11, column=0, sticky="w", pady=(0, 8))

    def _redirect_wheel(self, event: tk.Event) -> str:
        self._scroll_from_event(event)
        return "break"

    def _device_changed(self) -> None:
        self._clear_candidate()
        self._refresh_chart()

    def _add_point_row(self) -> None:
        row = ttk.Frame(self._rows_container)
        row.grid(row=len(self._point_rows), column=0, sticky="w", pady=1)
        x_entry = ttk.Entry(row, width=16)
        x_entry.grid(row=0, column=0)
        y_entry = ttk.Entry(row, width=16)
        y_entry.grid(row=0, column=1)
        remove = ttk.Button(row, text="×", width=2, command=lambda: self._remove_point_row(row))
        remove.grid(row=0, column=2, padx=(4, 0))
        self._point_rows.append((row, x_entry, y_entry))

    def _remove_point_row(self, row: ttk.Frame) -> None:
        if len(self._point_rows) <= MINIMUM_POINT_ROWS:
            self._status.configure(text=f"At least {MINIMUM_POINT_ROWS} points are needed to fit a line.")
            return
        self._point_rows = [entry for entry in self._point_rows if entry[0] is not row]
        row.destroy()
        for index, (remaining_row, _x, _y) in enumerate(self._point_rows):
            remaining_row.grid(row=index, column=0, sticky="w", pady=1)

    def _entered_points(self) -> list[CalibrationPoint]:
        points = []
        for _row, x_entry, y_entry in self._point_rows:
            x_text, y_text = x_entry.get().strip(), y_entry.get().strip()
            if not x_text and not y_text:
                continue
            points.append(CalibrationPoint(float(x_text), float(y_text)))
        return points

    def _fit(self) -> None:
        device_id = self._device_var.get()
        if not device_id:
            self._status.configure(text=f"No {self._service.kind.label} device is configured.")
            return
        try:
            points = self._entered_points()
            candidate = self._service.fit_candidate(
                device_id, self._name_var.get(), self._operator_var.get(), points,
                self._notes_var.get(),
            )
        except (TypeError, ValueError) as error:
            messagebox.showerror("Could not fit calibration", str(error), parent=self._window)
            return
        self._candidate = candidate
        self._accept_button.configure(state="normal")
        self._decline_button.configure(state="normal")
        text = self._text
        self._status.configure(text=(
            f"Fit: slope={candidate.fit.slope:.4g} {text.y_unit} per {text.x_unit}, "
            f"intercept={candidate.fit.intercept:.4g} {text.y_unit}, "
            f"R²={candidate.fit.r_squared:.4f}. Review the chart, then accept or decline."
        ))
        self._refresh_chart(reload=False)  # History is unchanged; only the candidate overlay is new.

    def _accept(self) -> None:
        if self._candidate is None:
            return
        # The name and notes may have been edited after Fit; save what is shown.
        candidate = replace(
            self._candidate, name=self._name_var.get().strip(), notes=self._notes_var.get(),
        )
        result = self._service.accept(candidate)
        self._status.configure(text=result.summary)
        if not result.succeeded:
            messagebox.showerror(self._text.title, result.summary, parent=self._window)
            return
        self._clear_candidate()
        self._name_var.set(today_calibration_name())
        self._notes_var.set("")
        self._refresh_chart()

    def _decline(self) -> None:
        self._clear_candidate()
        self._status.configure(text="Calibration declined and discarded.")
        self._refresh_chart(reload=False)  # Nothing was written; history is unchanged.

    def _clear_candidate(self) -> None:
        self._candidate = None
        self._accept_button.configure(state="disabled")
        self._decline_button.configure(state="disabled")
        for _row, x_entry, y_entry in self._point_rows:
            x_entry.delete(0, "end")
            y_entry.delete(0, "end")

    def _show_selected(self) -> None:
        chosen = self._saved.selection()
        names = {calibration.name: calibration for calibration in self._history_cache}
        self._selected = names.get(chosen[0]) if chosen else None
        selected = self._selected
        if selected is None:
            self._saved_detail.configure(text="")
        else:
            text = self._text
            points = ", ".join(f"({point.x:g}, {point.y:g})" for point in selected.points)
            self._saved_detail.configure(text=(
                f"{selected.name}: y = {selected.fit.slope:.4g}·x + {selected.fit.intercept:.4g} "
                f"({text.y_unit} from {text.x_unit}), R²={selected.fit.r_squared:.4f}. "
                f"Points: {points}." + (f" Notes: {selected.notes}" if selected.notes else "")
            ))
        self._refresh_chart(reload=False)

    def _refresh_saved_list(self) -> None:
        self._saved.delete(*self._saved.get_children())
        for calibration in reversed(self._history_cache):
            fit = calibration.fit
            self._saved.insert("", "end", iid=calibration.name, values=(
                calibration.name,
                calibration.created_utc.astimezone().strftime("%Y-%m-%d %H:%M"),
                calibration.operator,
                f"slope {fit.slope:.4g}, intercept {fit.intercept:.4g}, R² {fit.r_squared:.4f}",
                calibration.notes,
            ))
        self._selected = None
        self._saved_detail.configure(text="")

    def _refresh_chart(self, *, reload: bool = True) -> None:
        device_id = self._device_var.get()
        # A pure resize/redraw (<Configure>, which fires repeatedly while a
        # window is being dragged) reuses the cached history instead of
        # re-reading and re-parsing that device's file from disk every time.
        if reload or device_id != self._history_cache_device:
            try:
                self._history_cache = self._service.history(device_id) if device_id else ()
            except Exception as error:
                # A corrupted or hand-edited history file must not prevent
                # opening the Settings window or reaching other instruments'
                # panels in it.
                self._history_cache = ()
                self._status.configure(
                    text=f"Could not read calibration history for {device_id!r}: {error}"
                )
            self._history_cache_device = device_id
            self._refresh_saved_list()
        width = max(self._chart.winfo_width(), 100)
        height = max(self._chart.winfo_height(), 100)
        _draw_calibration_chart(
            self._chart, width, height, text=self._text, history=self._history_cache,
            candidate=self._candidate, selected=self._selected,
        )
