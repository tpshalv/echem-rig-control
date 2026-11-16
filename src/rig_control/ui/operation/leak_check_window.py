"""Leak check: raw flows, delta and percentage over the run, and the outlet-vs-inlet line."""
import os
from tkinter import messagebox, ttk
import tkinter as tk

from rig_control.flow.calibration import load_preferences, preferences_path, write_json
from rig_control.flow.leak_check import AVERAGING, LeakCheckPlan, parse_flows
from rig_control.ui.common.theme import BODY_FONT, ERROR_TEXT, MUTED_TEXT, WARNING_TEXT

INK = "#16253A"
GRID = "#E4E4E0"
AVERAGING_BAND = "#EFEFEA"
SET_COLOUR = "#52514E"
REFERENCE_COLOUR = "#9A9A94"
MFC_COLOUR = "#2A78D6"
MFM_COLOUR = "#EB6834"
DEFAULT_FLOWS = "25, 50, 75, 100"
# Below this the ratio is only meter noise divided by almost nothing.
MINIMUM_PERCENT_INLET_SCCM = 1.


def nice_ticks(low, high, count=5):
    """Round axis values covering low..high."""
    span = max(high-low, 1e-9)/max(count-1, 1)
    magnitude = 10**_floor_log10(span)
    step = next(m*magnitude for m in (1, 2, 2.5, 5, 10) if m*magnitude >= span)
    first = int(low//step)
    return [round((first+i)*step, 10)+0. for i in range(int((high-first*step)/step+1.999))]


def _floor_log10(value):
    power = 0
    while value >= 10:
        value, power = value/10, power+1
    while value < 1:
        value, power = value*10, power-1
    return power


def trace_segments(rows, value):
    """(minutes, value) runs, broken wherever a reading is missing."""
    segments, current = [], []
    for row in rows:
        y = value(row)
        if y is None:
            if current:
                segments.append(current)
                current = []
        else:
            current.append((row.elapsed_s/60, y))
    return segments+([current] if current else [])


def averaging_spans(rows):
    """(start, end) minutes of each condition's averaging period."""
    spans = {}
    for row in rows:
        if row.phase == AVERAGING:
            start, _ = spans.get(row.condition, (row.elapsed_s/60, None))
            spans[row.condition] = (start, row.elapsed_s/60)
    return list(spans.values())


def _thin(points, limit):
    stride = max(len(points)//max(limit, 1), 1)
    return points if stride == 1 else points[::stride]+[points[-1]]


def _text(canvas, x, y, text, anchor="w", fill=MUTED_TEXT):
    canvas.create_text(x, y, text=text, anchor=anchor, fill=fill, font=BODY_FONT)


def _empty(canvas, message):
    canvas.delete("all")
    canvas.create_text(max(canvas.winfo_width(), 200)/2, max(canvas.winfo_height(), 150)/2,
                       text=message, fill=MUTED_TEXT, font=BODY_FONT, width=max(canvas.winfo_width()-40, 160))


def delta_flow(row):
    """Outlet minus inlet for one raw reading, in sccm."""
    inlet, outlet = row.inlet_total, row.outlet_sccm
    return None if inlet is None or outlet is None else outlet-inlet


def outlet_percent(row):
    """Outlet as a percentage of inlet for one raw reading."""
    inlet, outlet = row.inlet_total, row.outlet_sccm
    return None if inlet is None or outlet is None or inlet < MINIMUM_PERCENT_INLET_SCCM else 100*outlet/inlet


# Set flow is last so it draws on top: a good MFC trace would otherwise hide it.
FLOW_SERIES = ((lambda r: r.outlet_sccm, MFM_COLOUR, "Outlet at MFM", None),
               (lambda r: r.inlet_total, MFC_COLOUR, "Inlet at MFC", None),
               (lambda r: r.set_total, SET_COLOUR, "Inlet set", (5, 4)))
DELTA_SERIES = ((delta_flow, MFC_COLOUR, "", None),)
PERCENT_SERIES = ((outlet_percent, MFC_COLOUR, "", None),)


def draw_time_panel(canvas, rows, minutes_planned, series, unit, *, reference=None, zero_floor=False):
    """One whole-check panel of raw readings, with the averaged periods shaded."""
    canvas.delete("all")
    runs = [(trace_segments(rows, value), colour, dash) for value, colour, _, dash in series]
    values = [point[1] for segments, _, _ in runs for segment in segments for point in segment]
    if not values:
        _empty(canvas, "Raw readings appear here during a check")
        return
    width, height = max(canvas.winfo_width(), 240), max(canvas.winfo_height(), 120)
    left, right, top, bottom = 56, width-14, 30, height-36
    if reference is not None:
        values = [*values, reference[0]]
    low, high = min(values), max(values)
    pad = max(high-low, 1.)*0.08
    low, high = (0. if zero_floor else low-pad), high+pad
    x_max = max(minutes_planned, rows[-1].elapsed_s/60, 0.1)
    x = lambda minutes: left+minutes/x_max*(right-left)
    y = lambda value: bottom-(value-low)/(high-low)*(bottom-top)
    for start, end in averaging_spans(rows):
        canvas.create_rectangle(x(start), top, x(end), bottom, fill=AVERAGING_BAND, outline="")
    for tick in nice_ticks(low, high):
        if low <= tick <= high:
            canvas.create_line(left, y(tick), right, y(tick), fill=GRID)
            _text(canvas, left-6, y(tick), f"{tick:g}", "e")
    for tick in nice_ticks(0, x_max, 7):
        if tick <= x_max:
            _text(canvas, x(tick), bottom+12, f"{tick:g}", "center")
    _text(canvas, (left+right)/2, bottom+27, "Elapsed time (min); shaded = averaged", "center")
    _text(canvas, 8, 12, unit)
    if reference is not None:
        canvas.create_line(left, y(reference[0]), right, y(reference[0]), fill=REFERENCE_COLOUR, dash=(5, 4))
        _text(canvas, right-4, y(reference[0])-8, reference[1], "e")
    for segments, colour, dash in runs:
        for segment in segments:
            coordinates = [v for minutes, value in _thin(segment, right-left) for v in (x(minutes), y(value))]
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=colour, width=2, dash=dash)
    if len(series) > 1:
        legend = left
        for _, colour, label, dash in reversed(series):
            canvas.create_line(legend, 12, legend+22, 12, fill=colour, width=2, dash=dash)
            _text(canvas, legend+27, 12, label, fill=INK)
            legend += 40+7*len(label)


def calibration_line_segment(fit, low, high):
    """Intersect the fitted line with the plot bounds without changing its slope.

    Clamping only the endpoint's outlet coordinate would draw a different line.
    Move its inlet coordinate to the intersection with the boundary instead.
    """
    slope, intercept = fit["slope"], fit["intercept_sccm"]
    if slope == 0:
        return ((low, intercept), (high, intercept)) if low <= intercept <= high else ()
    crossings = sorted(((low-intercept)/slope, (high-intercept)/slope))
    start, end = max(low, crossings[0]), min(high, crossings[1])
    if start > end:
        return ()
    return ((start, slope*start+intercept), (end, slope*end+intercept))


def draw_calibration(canvas, points, fit):
    if not points:
        _empty(canvas, "Each finished condition adds a point here")
        return
    canvas.delete("all")
    width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
    left, right, top, bottom = 52, width-12, 14, height-36
    values = [p["inlet_sccm"] for p in points]+[p["outlet_sccm"] for p in points]
    low, high = min(values), max(values)
    pad = max((high-low)*0.1, 1.)
    low, high = max(low-pad, 0.), high+pad
    x = lambda value: left+(value-low)/(high-low)*(right-left)
    y = lambda value: bottom-(value-low)/(high-low)*(bottom-top)
    for tick in nice_ticks(low, high):
        if low <= tick <= high:
            canvas.create_line(left, y(tick), right, y(tick), fill=GRID)
            _text(canvas, left-6, y(tick), f"{tick:g}", "e")
            _text(canvas, x(tick), bottom+12, f"{tick:g}", "center")
    _text(canvas, (left+right)/2, bottom+27, "Inlet at MFC (sccm)", "center")
    canvas.create_text(12, (top+bottom)/2, text="Outlet at MFM (sccm)", angle=90, fill=MUTED_TEXT, font=BODY_FONT)
    canvas.create_line(x(low), y(low), x(high), y(high), fill=REFERENCE_COLOUR, dash=(5, 4))
    _text(canvas, left+8, top+8, "Dashed: outlet = inlet")
    if fit:
        fitted = calibration_line_segment(fit, low, high)
        if fitted:
            canvas.create_line(*[v for q, out in fitted for v in (x(q), y(out))], fill=MFC_COLOUR, width=2)
    for point in points:
        px, py = x(point["inlet_sccm"]), y(point["outlet_sccm"])
        flagged = bool(point.get("flags"))
        canvas.create_oval(px-5, py-5, px+5, py+5, fill="white" if flagged else MFC_COLOUR, outline=MFC_COLOUR, width=2)


def fit_text(fit, count):
    if not fit:
        return f"{count} point{'' if count == 1 else 's'}; a line needs two different flows"
    sign = "+" if fit["intercept_sccm"] >= 0 else "-"
    quality = f", RMSE {fit['rmse_sccm']:.2f} sccm" if count > 2 else ""
    return f"Outlet = {fit['slope']:.4f} × inlet {sign} {abs(fit['intercept_sccm']):.2f} sccm\n{count} points{quality}"


class LeakCheckWindow:
    def __init__(self, parent, runner, indicator, preferences_file=None):
        self.runner, self.indicator = runner, indicator
        self._preferences_file = preferences_file or preferences_path(runner.profile.profile_id)
        preferences = load_preferences(self._preferences_file)
        self.window = tk.Toplevel(parent)
        self.window.title("Leak check")
        self.window.geometry("1180x880")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._after = None

        setup = ttk.Frame(self.window, padding=10)
        setup.pack(fill="x")
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text="Inlet MFCs").grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(setup, text="Flows (sccm), one value holds an MFC constant").grid(row=0, column=1, sticky="w")
        self.lines = {}
        saved_lines = preferences.get("lines", {})
        for row, key in enumerate(runner.inlets(), 1):
            saved = saved_lines.get(key, {})
            enabled = tk.BooleanVar(value=saved.get("enabled", len(runner.inlets()) == 1))
            flows = tk.StringVar(value=saved.get("flows", DEFAULT_FLOWS))
            ttk.Checkbutton(setup, text=key, variable=enabled, command=self._update_duration).grid(row=row, column=0, sticky="w", padx=(0, 12))
            ttk.Entry(setup, textvariable=flows).grid(row=row, column=1, sticky="ew", pady=1)
            flows.trace_add("write", lambda *_: self._update_duration())
            self.lines[key] = (enabled, flows)
        options = ttk.Frame(setup)
        options.grid(row=len(runner.inlets())+1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        outlets = runner.outlets()
        saved_outlet = preferences.get("outlet", "")
        self.outlet = tk.StringVar(value=saved_outlet if saved_outlet in outlets else (outlets[0] if outlets else ""))
        self.settle = tk.StringVar(value=str(preferences.get("settle_s", 60)))
        self.average = tk.StringVar(value=str(preferences.get("average_s", 60)))
        self.notes = tk.StringVar()
        ttk.Label(options, text="Outlet").pack(side="left")
        ttk.Combobox(options, textvariable=self.outlet, values=outlets, state="readonly", width=22).pack(side="left", padx=(4, 12))
        for label, variable in (("Settle (s)", self.settle), ("Average (s)", self.average)):
            ttk.Label(options, text=label).pack(side="left")
            ttk.Entry(options, textvariable=variable, width=6).pack(side="left", padx=(4, 12))
            variable.trace_add("write", lambda *_: self._update_duration())
        ttk.Label(options, text="Note").pack(side="left")
        ttk.Entry(options, textvariable=self.notes, width=30).pack(side="left", padx=(4, 12))
        self.duration = ttk.Label(options, foreground=MUTED_TEXT)
        self.duration.pack(side="left")

        actions = ttk.Frame(self.window, padding=(10, 0))
        actions.pack(fill="x")
        self.start = ttk.Button(actions, text="Start", command=self._start)
        self.start.pack(side="left")
        self.stop = ttk.Button(actions, text="Stop", command=runner.stop)
        self.stop.pack(side="left", padx=6)
        self.save = ttk.Button(actions, text="Save calibration", command=self._save)
        self.save.pack(side="left")
        ttk.Button(actions, text="Open folder", command=self._open_folder).pack(side="left", padx=6)
        self.supply = tk.StringVar(value=indicator.supply_id)
        self.supply_box = None
        if len(indicator.supplies()) > 1:
            self.supply_box = ttk.Combobox(actions, textvariable=self.supply, values=indicator.supplies(), state="readonly", width=20)
            self.supply_box.pack(side="right")
            self.supply_box.bind("<<ComboboxSelected>>", self._select_supply)
            ttk.Label(actions, text="Current for rough FE").pack(side="right", padx=4)

        self.status = ttk.Label(self.window, padding=(10, 6), wraplength=1040)
        self.status.pack(fill="x")
        self.notice = ttk.Label(self.window, padding=(10, 0), wraplength=1040, foreground=WARNING_TEXT)
        self.notice.pack(fill="x")

        charts = ttk.Frame(self.window, padding=10)
        charts.pack(fill="both", expand=True)
        charts.columnconfigure((0, 1), weight=1, uniform="panels")
        charts.rowconfigure((1, 3), weight=1, uniform="panels")
        ttk.Label(charts, text="Flows", anchor="sw").grid(row=0, column=0, sticky="nsew")
        self.calibration_title = ttk.Label(charts, text="Calibration", wraplength=560)
        self.calibration_title.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(charts, text="Delta flow: outlet − inlet").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(charts, text="Outlet as a percentage of inlet").grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        self.flows_canvas = self._panel(charts, 1, 0)
        self.calibration_canvas = self._panel(charts, 1, 1)
        self.delta_canvas = self._panel(charts, 3, 0)
        self.percent_canvas = self._panel(charts, 3, 1)
        self._update_duration()
        self._tick()

    @staticmethod
    def _panel(parent, row, column):
        canvas = tk.Canvas(parent, background="white", highlightthickness=0, width=200, height=120)
        canvas.grid(row=row, column=column, sticky="nsew", padx=(10 if column else 0, 0))
        return canvas

    def _plan(self):
        flows = {key: parse_flows(text.get()) for key, (enabled, text) in self.lines.items() if enabled.get()}
        return LeakCheckPlan.from_flow_lists(flows, self.outlet.get(), settle_s=float(self.settle.get()),
                                             average_s=float(self.average.get()), notes=self.notes.get().strip())

    def _update_duration(self):
        try:
            plan = self._plan()
            self.duration.configure(text=f"{len(plan.conditions)} conditions, about {plan.duration_s/60:.1f} min")
        except ValueError as error:
            self.duration.configure(text=str(error))

    def _save_preferences(self, **changes):
        try:
            preferences = load_preferences(self._preferences_file)
            write_json(self._preferences_file, {**preferences, **changes})
        except (OSError, ValueError):
            pass  # Remembered form values are a convenience only.

    def _start(self):
        runner = self.runner
        try:
            plan = self._plan()
            if runner.has_unsaved_points and not messagebox.askyesno(
                    "Leak check", "The last check has points that were not saved as a calibration. Start a new one anyway?",
                    parent=self.window):
                return
            runner.start(plan)
        except Exception as error:
            messagebox.showerror("Leak check", str(error), parent=self.window)
            return
        self._save_preferences(lines={key: {"enabled": enabled.get(), "flows": text.get()} for key, (enabled, text) in self.lines.items()},
                               outlet=self.outlet.get(), settle_s=plan.settle_s, average_s=plan.average_s)

    def _save(self):
        try:
            self.runner.save()
        except Exception as error:
            messagebox.showerror("Leak check", str(error), parent=self.window)

    def _open_folder(self):
        folder = self.runner.folder or self.runner.store.directory
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except (OSError, AttributeError) as error:
            messagebox.showinfo("Leak check", f"Folder: {folder}\n\n{error}", parent=self.window)

    def _select_supply(self, _event=None):
        try:
            self.indicator.select_supply(self.supply.get())
            self._save_preferences(supply_id=self.supply.get())
        except Exception as error:
            self.supply.set(self.indicator.supply_id)
            messagebox.showerror("Leak check", str(error), parent=self.window)

    def _tick(self):
        runner = self.runner
        try:
            with runner.lock:
                rows, points, fit = list(runner.rows), list(runner.points), runner.fit
                plan, status, warnings = runner.plan, runner.status, tuple(runner.warnings)
                running, can_save = runner.is_running, runner.can_save
                saved = runner.saved
            in_use = self.indicator.current_calibration()
            recording = self.indicator.is_recording()
            self.start.configure(state="disabled" if running else "normal")
            self.stop.configure(state="normal" if running else "disabled")
            self.save.configure(state="normal" if can_save else "disabled")
            if self.supply_box is not None:
                self.supply_box.configure(state="disabled" if recording else "readonly")
            failed = "COULD NOT" in status or "safety" in status
            self.status.configure(text=status, foreground=ERROR_TEXT if failed else "")
            notices = list(warnings)
            if recording:
                notices.append("Recording: readings go into the recording too. A calibration saved now applies from the next recording.")
            notices += [f"Unreadable calibration {error}" for error in runner.store.errors]
            self.notice.configure(text="\n".join(notices))
            planned = plan.duration_s/60 if running and plan else 0.
            draw_time_panel(self.flows_canvas, rows, planned, FLOW_SERIES, "sccm", zero_floor=True)
            draw_time_panel(self.delta_canvas, rows, planned, DELTA_SERIES, "sccm", reference=(0., "outlet = inlet"))
            draw_time_panel(self.percent_canvas, rows, planned, PERCENT_SERIES, "%", reference=(100., "outlet = inlet"))
            if points:
                title = "This check: "+fit_text(fit, len(points))
                if saved:
                    title += " (saved)"
                if any(p["flags"] for p in points):
                    title += "; hollow = noisy or sparse"
                draw_calibration(self.calibration_canvas, points, fit)
            elif in_use:
                title = f"In use, {in_use['id']}: "+fit_text(in_use, len(in_use.get("points", ())))
                draw_calibration(self.calibration_canvas, in_use.get("points", ()), in_use)
            else:
                title = "No saved calibration yet"
                draw_calibration(self.calibration_canvas, (), None)
            self.calibration_title.configure(text=title)
        except Exception as error:
            self.status.configure(text=f"Leak check display: {error}", foreground=ERROR_TEXT)
        self._after = self.window.after(1000, self._tick)

    def close(self):
        # A running check carries on; reopen this window to see it, or use Stop.
        if self._after:
            self.window.after_cancel(self._after)
        self.window.destroy()
