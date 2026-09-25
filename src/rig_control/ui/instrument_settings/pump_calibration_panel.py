"""Pump speed-to-flow calibration editor, hosted by the Settings window.

Enter a handful of measured (RPM, ml/min) points, review the fitted line
and R^2 against the chart before deciding whether to accept it. Accepting
appends it to that pump's calibration history and makes it active;
declining discards it, leaving no trace. The chart also draws the pump's
previous calibrations faintly, so a drifting fit is visible at a glance
rather than something that has to be read out of a table of numbers.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from rig_control.devices.pump_calibration import CalibrationPoint
from rig_control.instrument_settings.pump_calibration import (
    CalibrationCandidate,
    PumpCalibrationSettingsService,
)
from rig_control.ui.common.theme import HAIRLINE, INFO_TEXT, MUTED_TEXT, SECTION_FONT
from rig_control.ui.instrument_settings.base import InstrumentSettingsPanel


MINIMUM_POINT_ROWS = 2


def _draw_calibration_chart(
    canvas: tk.Canvas,
    width: int,
    height: int,
    *,
    history: tuple,
    candidate: CalibrationCandidate | None,
) -> None:
    canvas.delete("all")
    left, top, right, bottom = 56, 14, max(width - 16, 60), max(height - 34, 30)
    if right <= left or bottom <= top:
        return

    rpm_values = [point.speed_rpm for calibration in history for point in calibration.points]
    flow_values = [point.flow_ml_per_min for calibration in history for point in calibration.points]
    if candidate is not None:
        rpm_values += [point.speed_rpm for point in candidate.points]
        flow_values += [point.flow_ml_per_min for point in candidate.points]

    if not rpm_values:
        canvas.create_text(
            (left + right) / 2, (top + bottom) / 2,
            text="No calibration points yet", fill=MUTED_TEXT,
        )
        return

    rpm_span = max(max(rpm_values) - min(rpm_values), 1e-6)
    flow_span = max(max(flow_values) - min(flow_values), 1e-6)
    min_rpm = min(rpm_values) - rpm_span * 0.08
    max_rpm = max(rpm_values) + rpm_span * 0.08
    min_flow = min(0.0, min(flow_values) - flow_span * 0.08)
    max_flow = max(flow_values) + flow_span * 0.08

    def x(rpm: float) -> float:
        return left + (rpm - min_rpm) / (max_rpm - min_rpm) * (right - left)

    def y(flow: float) -> float:
        return bottom - (flow - min_flow) / (max_flow - min_flow) * (bottom - top)

    canvas.create_line(left, top, left, bottom, fill=MUTED_TEXT)
    canvas.create_line(left, bottom, right, bottom, fill=MUTED_TEXT)
    canvas.create_text(left - 6, top, anchor="ne", fill=MUTED_TEXT, text=f"{max_flow:.3g}")
    canvas.create_text(left - 6, bottom, anchor="se", fill=MUTED_TEXT, text=f"{min_flow:.3g} ml/min")
    canvas.create_text(left, bottom + 16, anchor="n", fill=MUTED_TEXT, text=f"{min_rpm:.3g}")
    canvas.create_text(right, bottom + 16, anchor="n", fill=MUTED_TEXT, text=f"{max_rpm:.3g} rpm")

    previous, active = (history[:-1], history[-1]) if history else ((), None)
    for calibration in previous:
        fit = calibration.fit
        canvas.create_line(
            x(min_rpm), y(fit.flow_for_speed(min_rpm)), x(max_rpm), y(fit.flow_for_speed(max_rpm)),
            fill=HAIRLINE, width=1, dash=(2, 2),
        )
    if active is not None:
        fit = active.fit
        canvas.create_line(
            x(min_rpm), y(fit.flow_for_speed(min_rpm)), x(max_rpm), y(fit.flow_for_speed(max_rpm)),
            fill=MUTED_TEXT, width=2,
        )
    if candidate is not None:
        fit = candidate.fit
        canvas.create_line(
            x(min_rpm), y(fit.flow_for_speed(min_rpm)), x(max_rpm), y(fit.flow_for_speed(max_rpm)),
            fill=INFO_TEXT, width=2,
        )
        for point in candidate.points:
            point_x, point_y = x(point.speed_rpm), y(point.flow_ml_per_min)
            canvas.create_oval(
                point_x - 3, point_y - 3, point_x + 3, point_y + 3,
                fill=INFO_TEXT, outline=INFO_TEXT,
            )

    canvas.create_text(
        right, top, anchor="ne", fill=MUTED_TEXT,
        text=(
            f"{len(history)} saved calibration(s)"
            + (" — reviewing a new fit" if candidate is not None else "")
        ),
    )


class PumpCalibrationPanel(InstrumentSettingsPanel):
    def __init__(
        self, parent: tk.Misc, service_provider: Callable[[], PumpCalibrationSettingsService],
        scroll_from_event: Callable[[tk.Event], None],
    ) -> None:
        super().__init__(parent, padding=(4, 0))
        self._window = self.winfo_toplevel()
        self._service_provider = service_provider
        self._scroll_from_event = scroll_from_event
        self._device_var = tk.StringVar(master=self)
        self._operator_var = tk.StringVar(master=self)
        self._notes_var = tk.StringVar(master=self)
        self._point_rows: list[tuple[ttk.Frame, ttk.Entry, ttk.Entry]] = []
        self._candidate: CalibrationCandidate | None = None
        self._history_cache: tuple = ()
        self._history_cache_device: str | None = None
        self._build()
        self.refresh()

    @property
    def _service(self) -> PumpCalibrationSettingsService:
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
        ttk.Label(self, text="Pump calibration", font=SECTION_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        chooser = ttk.Frame(self)
        chooser.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(chooser, text="Pump:").grid(row=0, column=0, padx=(0, 6))
        self._device_chooser = ttk.Combobox(
            chooser, textvariable=self._device_var, state="readonly", width=24,
        )
        self._device_chooser.grid(row=0, column=1, padx=(0, 12))
        self._device_chooser.bind(
            "<<ComboboxSelected>>", lambda _event: (self._clear_candidate(), self._refresh_chart()),
        )
        ttk.Label(chooser, text="Operator:").grid(row=0, column=2, padx=(0, 6))
        ttk.Entry(chooser, textvariable=self._operator_var, width=18).grid(row=0, column=3)

        ttk.Label(
            self,
            text=(
                "Run the pump at a few speeds, measure the flow at each, and enter the pairs "
                "below. Fit shows the line before anything is saved; accept it to make it "
                "active, or decline to discard it."
            ),
            wraplength=560,
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))

        self._points_frame = ttk.Frame(self)
        self._points_frame.grid(row=3, column=0, sticky="w")
        header = ttk.Frame(self._points_frame)
        header.grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Speed (rpm)", width=14).grid(row=0, column=0)
        ttk.Label(header, text="Flow (ml/min)", width=14).grid(row=0, column=1)
        self._rows_container = ttk.Frame(self._points_frame)
        self._rows_container.grid(row=1, column=0, sticky="w")
        for _ in range(MINIMUM_POINT_ROWS + 1):
            self._add_point_row()

        add_row = ttk.Frame(self)
        add_row.grid(row=4, column=0, sticky="w", pady=(4, 8))
        ttk.Button(add_row, text="+ Add point", command=self._add_point_row).grid(row=0, column=0)

        notes_row = ttk.Frame(self)
        notes_row.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(notes_row, text="Notes (optional):").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(notes_row, textvariable=self._notes_var, width=48).grid(row=0, column=1, sticky="ew")

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

    def _redirect_wheel(self, event: tk.Event) -> str:
        self._scroll_from_event(event)
        return "break"

    def _add_point_row(self) -> None:
        row = ttk.Frame(self._rows_container)
        row.grid(row=len(self._point_rows), column=0, sticky="w", pady=1)
        speed_entry = ttk.Entry(row, width=14)
        speed_entry.grid(row=0, column=0)
        flow_entry = ttk.Entry(row, width=14)
        flow_entry.grid(row=0, column=1)
        remove = ttk.Button(row, text="×", width=2, command=lambda: self._remove_point_row(row))
        remove.grid(row=0, column=2, padx=(4, 0))
        self._point_rows.append((row, speed_entry, flow_entry))

    def _remove_point_row(self, row: ttk.Frame) -> None:
        if len(self._point_rows) <= MINIMUM_POINT_ROWS:
            self._status.configure(text=f"At least {MINIMUM_POINT_ROWS} points are needed to fit a line.")
            return
        self._point_rows = [entry for entry in self._point_rows if entry[0] is not row]
        row.destroy()
        for index, (remaining_row, _speed, _flow) in enumerate(self._point_rows):
            remaining_row.grid(row=index, column=0, sticky="w", pady=1)

    def _entered_points(self) -> list[CalibrationPoint]:
        points = []
        for _row, speed_entry, flow_entry in self._point_rows:
            speed_text, flow_text = speed_entry.get().strip(), flow_entry.get().strip()
            if not speed_text and not flow_text:
                continue
            points.append(CalibrationPoint(float(speed_text), float(flow_text)))
        return points

    def _fit(self) -> None:
        device_id = self._device_var.get()
        if not device_id:
            self._status.configure(text="No pump is configured.")
            return
        try:
            points = self._entered_points()
            candidate = self._service.fit_candidate(
                device_id, self._operator_var.get(), points, self._notes_var.get(),
            )
        except (TypeError, ValueError) as error:
            messagebox.showerror("Could not fit calibration", str(error), parent=self._window)
            return
        self._candidate = candidate
        self._accept_button.configure(state="normal")
        self._decline_button.configure(state="normal")
        self._status.configure(text=(
            f"Fit: slope={candidate.fit.slope:.4g} ml/min per rpm, "
            f"intercept={candidate.fit.intercept:.4g} ml/min, "
            f"R²={candidate.fit.r_squared:.4f}. Review the chart, then accept or decline."
        ))
        self._refresh_chart(reload=False)  # History is unchanged; only the candidate overlay is new.

    def _accept(self) -> None:
        if self._candidate is None:
            return
        result = self._service.accept(self._candidate)
        self._status.configure(text=result.summary)
        if not result.succeeded:
            messagebox.showerror("Pump calibration", result.summary, parent=self._window)
            return
        self._clear_candidate()
        self._refresh_chart()

    def _decline(self) -> None:
        self._clear_candidate()
        self._status.configure(text="Calibration declined and discarded.")
        self._refresh_chart(reload=False)  # Nothing was written; history is unchanged.

    def _clear_candidate(self) -> None:
        self._candidate = None
        self._accept_button.configure(state="disabled")
        self._decline_button.configure(state="disabled")
        for row, speed_entry, flow_entry in self._point_rows:
            speed_entry.delete(0, "end")
            flow_entry.delete(0, "end")

    def _refresh_chart(self, *, reload: bool = True) -> None:
        device_id = self._device_var.get()
        # A pure resize/redraw (<Configure>, which fires repeatedly while a
        # window is being dragged) reuses the cached history instead of
        # re-reading and re-parsing that pump's file from disk every time.
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
        width = max(self._chart.winfo_width(), 100)
        height = max(self._chart.winfo_height(), 100)
        _draw_calibration_chart(
            self._chart, width, height, history=self._history_cache, candidate=self._candidate,
        )
