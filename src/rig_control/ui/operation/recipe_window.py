"""Recipe builder: a list of Set, Wait and Loop … Next rows.

The left side shows the recipe as it will run, top to bottom.  Each loop
opens with a Loop row and closes with a Next row; everything between them
is indented, tinted and runs once per loop value.

The right side summarises the selected step.  Double-clicking a value there
changes it in place; double-clicking the step opens ``StepEditorDialog`` for
everything else.  Hardware work stays in ``RecipeRunner``.
"""
from collections.abc import Callable
from dataclasses import replace
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rig_control.recipes import (
    LoopStep, Recipe, RepeatStep, SetStep, WaitPurpose, WaitStep, estimate_recipe, load_recipe,
    preview_recipe, recipe_problems, save_recipe, step_times,
)
from rig_control.recipes.execution import RecipeRunner
from rig_control.recipes import Assignment, Ramp, RampKind
from rig_control.recipes.plan import PlannedRamp, plan, values_before
from rig_control.recipes.outline import (
    LoopEnd, OutlineError, OutlineRow, delete, depths, duplicate, from_outline, insert, is_loop, move,
    partner, to_outline,
)
from rig_control.rig_profile import RigProfile
from rig_control.ui.common.theme import ERROR_TEXT, INFO_TEXT, MUTED_TEXT, SECTION_FONT, SUCCESS_TEXT
from rig_control.ui.operation.recipe_settings import (
    SETTINGS, default_value, group_by_device, group_mode, short_label, format_clock, format_duration, format_value, parse_duration,
    ValueDisplay, default_end_state, describe_ramp, end_state_summary, end_state_warnings, parse_pass_names, spec_for, unit_for,
)
from rig_control.ui.operation.recipe_end_dialog import EndStateDialog
from rig_control.ui.operation.recipe_step_dialog import StepEditorDialog

#: Background for each loop nesting level, so a loop's extent is visible.
_DEPTH_TINTS = ("#E3EEFA", "#E5F4E8", "#FCF1DE", "#F1E7FA")
_PANEL_WRAP = 280
#: A quick-panel field: (name, shown value, unit, kind, change).  ``kind`` is
#: "" (read-only), "text" (``change(text) -> step``), "toggle"
#: (``change() -> step``, for two-state values) or "heading" (a device title).
_Field = tuple[str, str, str, str, Callable[..., OutlineRow] | None]
#: Fields of a whole Set step, shown above (not indented under) its devices.
_STEP_FIELDS = {"Name", "Then hold", "Hold is"}


class RecipeBuilderWindow:
    """Edits an in-memory list of outline rows; hardware work remains in ``RecipeRunner``."""

    def __init__(self, root: tk.Toplevel, runner: RecipeRunner, profile: RigProfile, *,
                 default_output_directory: str = "", current_unit: str = "mA") -> None:
        self._root, self._runner, self._profile = root, runner, profile
        self._rows: list[OutlineRow] = []
        self._selected: int | None = None
        #: With a Set step selected: one device's line, or None for the whole step.
        self._selected_device: str | None = None
        self._inline: ttk.Entry | None = None
        self._roles = tuple(r for r in profile.enabled_roles if r.capability in SETTINGS)
        #: Every enabled device, for the end state (even ones a step cannot set).
        self._all_roles = tuple(profile.enabled_roles)
        #: How the rig is left on finish or Stop; new recipes leave MFC and BPR running.
        self._end_state = default_end_state(self._all_roles)
        #: True while the fixed End state row is selected.
        self._end_selected = False
        #: Currents show in mA (or A, per the app setting); recipes keep amperes.
        self._units = ValueDisplay(current_unit)
        root.title("Recipe builder")
        root.geometry("1120x720"); root.minsize(860, 560)
        root.columnconfigure(0, weight=1); root.rowconfigure(2, weight=1)
        self._build_header()
        self._build_toolbar()
        self._build_body()
        self._build_footer(default_output_directory)
        self._refresh(); self._show_quick()
        self._tick()

    # --- layout ---

    def _build_header(self) -> None:
        top = ttk.Frame(self._root, padding=(12, 12, 12, 4)); top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Recipe name").grid(row=0, column=0, sticky="w")
        self._name = ttk.Entry(top); self._name.grid(row=0, column=1, sticky="ew", padx=6)
        self._name.insert(0, "Untitled recipe")
        ttk.Button(top, text="New", command=self._new).grid(row=0, column=2, padx=3)
        ttk.Button(top, text="Load…", command=self._load).grid(row=0, column=3, padx=3)
        ttk.Button(top, text="Save…", command=self._save).grid(row=0, column=4, padx=3)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self._root, padding=(12, 4)); bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(bar, text="Add after selected:").pack(side="left")
        for text, command in (("+ Set", self._add_set), ("+ Wait (time)", self._add_wait),
                              ("+ Loop over values", self._add_loop), ("+ Repeat", self._add_repeat),
                              ("+ Ramp", self._add_ramp)):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        for text, command in (("Edit…", self._edit_selected), ("▲ Up", lambda: self._move(-1)),
                              ("▼ Down", lambda: self._move(1)), ("Duplicate", self._duplicate),
                              ("Delete", self._delete)):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=2)

    def _build_body(self) -> None:
        body = ttk.Frame(self._root, padding=(12, 0)); body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1); body.columnconfigure(1, minsize=310); body.rowconfigure(0, weight=1)

        tabs = ttk.Notebook(body); tabs.grid(row=0, column=0, sticky="nsew")
        steps_tab = ttk.Frame(tabs, padding=6); timeline_tab = ttk.Frame(tabs, padding=6)
        tabs.add(steps_tab, text="Steps"); tabs.add(timeline_tab, text="Timeline")
        tabs.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_timeline())
        self._tabs, self._timeline_tab = tabs, timeline_tab

        steps_tab.columnconfigure(0, weight=1); steps_tab.rowconfigure(0, weight=1)
        ttk.Style(self._root).configure("Recipe.Treeview", rowheight=24, font=("Segoe UI", 10))
        # TIME sits before the wide STEP column, so scrolling sideways to read a
        # long line never hides it.
        self._tree = ttk.Treeview(steps_tab, columns=("n", "name", "time", "step"), show="headings",
                                  selectmode="browse", style="Recipe.Treeview")
        for col, label, width in (("n", "#", 36), ("name", "NAME", 130), ("time", "TIME", 110), ("step", "STEP", 560)):
            self._tree.heading(col, text=label, anchor="w"); self._tree.column(col, width=width, stretch=False, anchor="w")
        scroll = ttk.Scrollbar(steps_tab, orient="vertical", command=self._tree.yview)
        xscroll = ttk.Scrollbar(steps_tab, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        self._tree.grid(row=0, column=0, sticky="nsew"); scroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self._tree.bind("<Configure>", lambda _e: self._fit_step_column(), add="+")
        self._measure = tkfont.Font(root=self._root, family="Segoe UI", size=10, weight="bold")
        self._step_text_width = 0
        for depth, tint in enumerate(_DEPTH_TINTS, start=1):
            self._tree.tag_configure(f"depth{depth}", background=tint)
        self._tree.tag_configure("loop", foreground=INFO_TEXT, font=("Segoe UI", 10, "bold"))
        self._tree.tag_configure("next", foreground=INFO_TEXT)
        self._tree.tag_configure("settle", foreground=MUTED_TEXT)
        self._tree.tag_configure("end", font=("Segoe UI", 10, "bold"), background="#ECEFF3")
        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Return>", lambda _e: self._edit_selected())
        self._tree.bind("<Control-Up>", lambda _e: (self._move(-1), "break")[1])
        self._tree.bind("<Control-Down>", lambda _e: (self._move(1), "break")[1])
        self._tree.bind("<Delete>", lambda _e: self._delete())
        self._summary = ttk.Label(steps_tab, wraplength=640, justify="left")
        self._summary.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        timeline_tab.columnconfigure(0, weight=1); timeline_tab.rowconfigure(0, weight=1)
        self._timeline = ttk.Treeview(timeline_tab, columns=("at", "where", "step"), show="headings")
        for col, label, width in (("at", "START", 80), ("where", "LOOP POSITION", 300), ("step", "STEP", 360)):
            self._timeline.heading(col, text=label, anchor="w"); self._timeline.column(col, width=width, anchor="w")
        tscroll = ttk.Scrollbar(timeline_tab, orient="vertical", command=self._timeline.yview)
        self._timeline.configure(yscrollcommand=tscroll.set)
        self._timeline.grid(row=0, column=0, sticky="nsew"); tscroll.grid(row=0, column=1, sticky="ns")
        self._timeline_note = ttk.Label(timeline_tab, foreground=MUTED_TEXT)
        self._timeline_note.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._quick = ttk.LabelFrame(body, text="Selected step", padding=10)
        self._quick.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self._quick.columnconfigure(1, weight=1)

    def _build_footer(self, output: str) -> None:
        bottom = ttk.Frame(self._root, padding=12); bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="Output folder").grid(row=0, column=0, sticky="w")
        self._output = ttk.Entry(bottom); self._output.grid(row=0, column=1, sticky="ew", padx=5); self._output.insert(0, output)
        ttk.Button(bottom, text="Browse…", command=self._browse).grid(row=0, column=2)
        ttk.Button(bottom, text="Validate", command=self._validate).grid(row=0, column=3, padx=(10, 3))
        self._start = ttk.Button(bottom, text="Start", command=self._start_recipe); self._start.grid(row=0, column=4, padx=3)
        ttk.Button(bottom, text="Stop → end state", command=self._runner.stop).grid(row=0, column=5, padx=3)
        self._status = ttk.Label(bottom, text="No recipe running", font=SECTION_FONT)
        self._status.grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

    # --- lookups ---

    def _friendly(self, device_id: str) -> str:
        try:
            return self._profile.get_role(device_id).friendly_name
        except KeyError:
            return device_id

    def _target_text(self, device_id: str, setting: str) -> str:
        return f"{self._friendly(device_id)} · {spec_for(setting).label}"

    # --- row text ---

    def _device_line(self, device_id: str, items, key: tuple[int, ...] | None = None) -> str:
        """``Keithley 2280S (CC):  Current setpoint 0.1 A  ·  Voltage limit 5 V  ·  Output On``"""
        mode = group_mode(items)
        parts = []
        for a in items:
            spec = spec_for(a.setting)
            unit = f" {self._units.suffix(spec)}" if self._units.suffix(spec) else ""
            ramp = f" ({describe_ramp(a.ramp, spec, self._units, self._ramp_start(key, a.device_id, a.setting))})" \
                if a.ramp else ""
            parts.append(f"{short_label(spec)} {self._units.show(a.value, spec)}{unit}{ramp}")
        mode_text = {"constant_current": " (CC)", "constant_voltage": " (CV)"}.get(mode.value if mode else "", "")
        return f"{self._friendly(device_id)}{mode_text}:  " + "  ·  ".join(parts)

    def _describe(self, index: int) -> tuple[list[tuple[str | None, str]], str]:
        """Tree lines for one outline row as ``(device_id or None, text)``, plus its time.

        A Set step with several devices gets a header line (the whole step)
        and one line per device; otherwise every row is a single line.
        """
        row, key = self._rows[index], self._keys[index]
        if isinstance(row, SetStep):
            groups = group_by_device(row.assignments)
            hold = row.hold
            then = f"   →  then {hold.purpose.value} {format_duration(hold.duration_seconds)}" if hold else ""
            seconds = self._times.total(key) if self._times and key else 0.0
            time = format_duration(seconds) if seconds else ""
            if len(groups) == 1:
                return [(None, "Set   " + self._device_line(*groups[0], key) + then)], time
            header = "Set   " + ", ".join(self._friendly(d) for d, _ in groups) + then
            return [(None, header)] + [(d, "        " + self._device_line(d, items, key)) for d, items in groups], time
        if isinstance(row, WaitStep):
            icon = "◷ Settle " if row.purpose is WaitPurpose.SETTLE else "● Measure"
            return [(None, f"{icon}  {format_duration(row.duration_seconds)}")], format_duration(row.duration_seconds)
        if isinstance(row, LoopStep):
            spec = spec_for(row.setting)
            unit = f" {self._units.suffix(spec)}" if self._units.suffix(spec) else ""
            shown = ", ".join(row.pass_names[:8] if row.pass_names else
                              (self._units.show(v, spec) + unit for v in row.values[:8]))
            shown += ", …" if len(row.values) > 8 else ""
            ramp = (f"   ·  {describe_ramp(row.ramp, spec, self._units, self._ramp_start(key, row.device_id, row.setting))}"
                    if row.ramp else "")
            return [(None, f"┌ Loop  {self._target_text(row.device_id, row.setting)}:  {shown}   ({row.iterations}×){ramp}")], ""
        if isinstance(row, RepeatStep):
            return [(None, f"┌ Repeat  {row.count}×")], ""
        loop = self._rows[partner(self._rows, index)]
        name = loop.name or (spec_for(loop.setting).label if isinstance(loop, LoopStep) else "repeat")
        return [(None, f"└ Next  {name}")], ""

    def _loop_title(self, index: int) -> str:
        row = self._rows[index]
        text = self._describe(index)[0][0][1].removeprefix("┌ ")
        return f"{row.name}: {text}" if row.name else text

    # --- refresh ---

    def _recipe(self, rows: list[OutlineRow] | None = None) -> Recipe:
        return Recipe(self._name.get().strip() or "Untitled recipe", from_outline(self._rows if rows is None else rows),
                      end_state=self._end_state)

    def _outline_keys(self) -> list[tuple[int, ...] | None]:
        """Each outline row's index path in the recipe tree (None for Next rows)."""
        keys: list[tuple[int, ...] | None] = []
        def walk(steps, prefix: tuple[int, ...]) -> None:
            for i, step in enumerate(steps):
                keys.append((*prefix, i))
                if isinstance(step, (LoopStep, RepeatStep)):
                    walk(step.steps, (*prefix, i)); keys.append(None)
        walk(from_outline(self._rows), ())
        return keys

    def _analyse(self) -> None:
        """Times and ramp starts from the one plan the runner will follow."""
        steps = from_outline(self._rows)
        self._keys = self._outline_keys()
        self._ramp_starts: dict[tuple, float] = {}
        self._plan_problem = ""
        try:
            for item in plan(steps):
                if isinstance(item, PlannedRamp):
                    where = (tuple(i for i, _ in item.trace), item.target.device_id, item.target.setting)
                    self._ramp_starts.setdefault(where, item.start)
            self._times = step_times(steps)
        except ValueError as error:
            self._times, self._plan_problem = None, str(error)

    def _ramp_start(self, key, device_id: str, setting: str) -> float | None:
        return self._ramp_starts.get((key, device_id, setting)) if key else None

    def _refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._analyse()
        levels = depths(self._rows)
        self._step_text_width = 0
        for index, row in enumerate(self._rows):
            depth = levels[index]
            lines, time = self._describe(index)
            tags: list[str] = []
            band = depth + 1 if is_loop(row) or isinstance(row, LoopEnd) else depth
            if band:
                tags.append(f"depth{(band - 1) % len(_DEPTH_TINTS) + 1}")
            if is_loop(row):
                tags.append("loop")
                key = self._keys[index]
                time = (f"{row.iterations}× = {format_duration(self._times.total(key))}"
                        if self._times and key else "")
            elif isinstance(row, LoopEnd):
                tags.append("next")
            elif isinstance(row, WaitStep) and row.purpose is WaitPurpose.SETTLE:
                tags.append("settle")
            guide = "│     " * depth
            name = "" if isinstance(row, LoopEnd) else row.name
            for line_number, (device_id, text) in enumerate(lines):
                # The first line is the step; a Set step's device lines are "index:device".
                iid = str(index) if device_id is None else f"{index}:{device_id}"
                values = (index + 1, name, time, guide + text) if line_number == 0 else ("", "", "", guide + text)
                self._tree.insert("", "end", iid=iid, values=values, tags=tags)
                self._step_text_width = max(self._step_text_width, self._measure.measure(guide + text))
        end_text = "■ End state (finish or Stop):  " + ";  ".join(
            end_state_summary(self._end_state, self._all_roles, self._units))
        self._tree.insert("", "end", iid="end", values=("", "End state", "", end_text), tags=("end",))
        self._step_text_width = max(self._step_text_width, self._measure.measure(end_text))
        self._fit_step_column()
        if self._end_selected:
            self._tree.selection_set("end")
        elif self._selected is not None and self._selected < len(self._rows):
            iid = self._selected_iid()
            self._tree.selection_set(iid); self._tree.see(iid)
        self._refresh_summary()
        if self._tabs.select() == str(self._timeline_tab):
            self._refresh_timeline()

    def _fit_step_column(self) -> None:
        """Make STEP as wide as its longest line; the scrollbar then reaches the end."""
        others = sum(int(self._tree.column(c, "width")) for c in ("n", "name", "time"))
        available = self._tree.winfo_width() - others - 4
        self._tree.column("step", width=max(self._step_text_width + 24, available, 200))

    def _refresh_summary(self) -> None:
        if not self._rows:
            self._summary.configure(text="Empty recipe. Add a step to begin.", foreground=MUTED_TEXT); return
        recipe = self._recipe()
        problems = list(recipe_problems(recipe))
        try:
            estimate = estimate_recipe(recipe)
            text = (f"{estimate.measure_waits} measurements · {estimate.settle_waits} settles · "
                    f"about {format_duration(estimate.duration_seconds)} in total. "
                    "Then the end state applies (see its row).")
        except ValueError:
            text = "Total time can't be worked out until that is fixed."
        for i, r in enumerate(self._rows):
            if not is_loop(r):
                continue
            if isinstance(self._rows[i + 1], LoopEnd):
                problems.append(f"Row {i + 1}: this loop has no steps inside it")
            elif not any((isinstance(x, WaitStep) and x.duration_seconds > 0)
                         or (isinstance(x, SetStep) and x.hold is not None)
                         for x in self._rows[i + 1:partner(self._rows, i)]):
                problems.append(f"Row {i + 1}: nothing inside this loop takes time; "
                                "add a Wait to set how long each pass lasts")
        if problems:
            self._summary.configure(text="⚠ " + problems[0] + "\n" + text, foreground=ERROR_TEXT)
        else:
            self._summary.configure(text="✓ " + text, foreground=SUCCESS_TEXT)

    def _refresh_timeline(self) -> None:
        self._timeline.delete(*self._timeline.get_children())
        limit = 500
        try:
            rows = preview_recipe(self._recipe(), limit=limit, text=self._units.text) if self._rows else ()
        except ValueError as error:
            self._timeline_note.configure(text=f"No timeline: {error}"); return
        for row in rows:
            self._timeline.insert("", "end", values=(format_clock(row.planned_start_seconds),
                                                     " › ".join(row.position) or "—", row.label))
        self._timeline_note.configure(text=f"Showing the first {limit} rows." if len(rows) == limit else
                                      "Planned start times exclude communication overhead.")

    # --- selection ---

    def _selected_iid(self) -> str:
        iid = f"{self._selected}:{self._selected_device}"
        if self._selected_device is None or not self._tree.exists(iid):
            self._selected_device = None
            return str(self._selected)
        return iid

    def _select_line(self, iid: str) -> bool:
        """Select a tree line; returns True when the selection changed."""
        if iid == "end":
            changed = not self._end_selected
            self._end_selected, self._selected, self._selected_device = True, None, None
            return changed
        if self._end_selected:
            self._end_selected = False
            self._selected = None  # Force the change below to count.
        index, _, device = iid.partition(":")
        selected = (int(index), device or None)
        if selected == (self._selected, self._selected_device):
            return False
        self._selected, self._selected_device = selected
        return True

    def _on_select(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        if self._select_line(selection[0]):
            self._show_quick()

    def _on_double_click(self, event: tk.Event) -> str | None:
        line = self._tree.identify_row(event.y)
        if not line:
            return None
        self._select_line(line); self._tree.selection_set(line); self._show_quick()
        self._edit_selected()
        return "break"

    # --- quick panel: compact summary, values editable in place ---

    def _show_quick(self) -> None:
        for child in self._quick.winfo_children():
            child.destroy()
        self._inline = None
        q = self._quick
        if self._end_selected:
            self._show_end_state(); return
        row = self._rows[self._selected] if self._selected is not None and self._selected < len(self._rows) else None
        if row is None:
            self._quick.configure(text="Selected step")
            ttk.Label(q, wraplength=_PANEL_WRAP, justify="left", foreground=MUTED_TEXT, text=(
                "Select a step to see it here, or add one with the buttons above.\n\n"
                "Set writes settings. Wait holds for a time (Settle or Measure). "
                "Loop runs the steps down to its Next row once per value; the "
                "innermost loop finishes all its values first.")).grid(row=0, column=0, columnspan=3, sticky="w")
            return
        title = {SetStep: "Set", WaitStep: "Wait", LoopStep: "Loop", RepeatStep: "Repeat", LoopEnd: "Next"}[type(row)]
        if self._selected_device is not None:
            title += f" · {self._friendly(self._selected_device)} only"
        self._quick.configure(text=f"Row {self._selected + 1}: {title}")
        fields = self._fields(row)
        for r, (name, value, unit, kind, edit) in enumerate(fields):
            if kind == "heading":
                ttk.Label(q, text=name, font=SECTION_FONT).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))
                continue
            indent = 12 if isinstance(row, SetStep) and name not in _STEP_FIELDS else 0
            ttk.Label(q, text=name, foreground=MUTED_TEXT, wraplength=150, justify="left").grid(
                row=r, column=0, sticky="nw", padx=(indent, 8), pady=2)
            shown = ttk.Label(q, text=value, wraplength=150, justify="left",
                              foreground=INFO_TEXT if edit else "", font=("Segoe UI", 10, "bold" if edit else "normal"))
            shown.grid(row=r, column=1, sticky="w", pady=2)
            ttk.Label(q, text=unit, foreground=MUTED_TEXT).grid(row=r, column=2, sticky="w", padx=(4, 0), pady=2)
            if kind == "toggle":
                shown.configure(cursor="hand2")
                shown.bind("<Double-1>", lambda _e, f=edit: self._apply_quick(f, None))
            elif kind == "text":
                shown.configure(cursor="hand2")
                shown.bind("<Double-1>", lambda _e, w=shown, v=value, f=edit: self._edit_inline(w, v, f))
        r = len(fields)
        self._quick_error = ttk.Label(q, foreground=ERROR_TEXT, wraplength=_PANEL_WRAP, justify="left")
        self._quick_error.grid(row=r, column=0, columnspan=3, sticky="w")
        timing = self._timing_text()
        if timing:
            ttk.Label(q, text=timing, foreground=INFO_TEXT, wraplength=_PANEL_WRAP, justify="left").grid(
                row=r + 1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Separator(q).grid(row=r + 2, column=0, columnspan=3, sticky="ew", pady=8)
        hint = ("Double-click a blue value to change it here. Double-click the step in the list, "
                "or Edit all…, for everything else." + (
                    " Select the step's first line to see all its devices." if self._selected_device else
                    " Select one device's line to see only that device." if isinstance(row, SetStep)
                    and len(group_by_device(row.assignments)) > 1 else "")) if not isinstance(row, LoopEnd) else \
               "Double-click this row, or Edit all…, to edit its loop. Move it up or down to change what the loop covers."
        ttk.Label(q, text=hint, foreground=MUTED_TEXT, wraplength=_PANEL_WRAP, justify="left").grid(
            row=r + 3, column=0, columnspan=3, sticky="w")
        ttk.Button(q, text="Edit all…", command=self._edit_selected).grid(row=r + 4, column=0, sticky="w", pady=(8, 0))

    def _show_end_state(self) -> None:
        q = self._quick
        self._quick.configure(text="End state")
        ttk.Label(q, wraplength=_PANEL_WRAP, justify="left", text=(
            "Applied when the run finishes, when you press Stop (remaining steps are skipped) "
            "or if the recipe fails. Control then returns to manual.")).grid(row=0, column=0, columnspan=3, sticky="w")
        lines = end_state_summary(self._end_state, self._all_roles, self._units)
        ttk.Label(q, text="\n".join(lines), font=("Segoe UI", 10, "bold"), foreground=INFO_TEXT, wraplength=_PANEL_WRAP,
                  justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        warnings = end_state_warnings(self._end_state, self._all_roles)
        if warnings:
            ttk.Label(q, text="\n".join("⚠ " + w for w in warnings), foreground=ERROR_TEXT, wraplength=_PANEL_WRAP,
                      justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Separator(q).grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(q, text="Always the last row: it can't be moved or deleted. The global safe state "
                          "for emergencies still turns everything off.", foreground=MUTED_TEXT,
                  wraplength=_PANEL_WRAP, justify="left").grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Button(q, text="Edit end state…", command=self._edit_end_state).grid(row=5, column=0, sticky="w", pady=(8, 0))

    def _edit_end_state(self) -> None:
        def done(end) -> None:
            if end is not None:
                self._end_state = end
                self._refresh()
            self._show_quick(); self._tree.focus_set()
        EndStateDialog(self._root, self._end_state, roles=self._all_roles, units=self._units, on_done=done)

    def _fields(self, row: OutlineRow) -> list[_Field]:
        if isinstance(row, LoopEnd):
            loop_index = partner(self._rows, self._selected)  # type: ignore[arg-type]
            return [("Closes", self._loop_title(loop_index), "", "", None)]
        fields: list[_Field] = [("Name", row.name or "—", "", "text", lambda text: replace(row, name=text))]
        if isinstance(row, SetStep):
            def hold(text: str) -> SetStep:
                folded = text.strip().casefold()
                seconds = 0.0 if folded in {"", "none", "0", "—"} else parse_duration(text)
                purpose = row.hold.purpose if row.hold else WaitPurpose.SETTLE
                return replace(row, hold=WaitStep(seconds, purpose) if seconds else None)
            fields.append(("Then hold", format_duration(row.hold.duration_seconds) if row.hold else "none",
                           "", "text", hold))
            if row.hold:
                other = WaitPurpose.SETTLE if row.hold.purpose is WaitPurpose.MEASURE else WaitPurpose.MEASURE
                fields.append(("Hold is", row.hold.purpose.value.capitalize(), "", "toggle",
                               lambda: replace(row, hold=replace(row.hold, purpose=other))))
            for device_id, items in group_by_device(row.assignments):
                if self._selected_device not in (None, device_id):
                    continue
                mode = group_mode(items)
                mode_text = {"constant_current": " (CC)", "constant_voltage": " (CV)"}.get(mode.value if mode else "", "")
                fields.append((self._friendly(device_id) + mode_text, "", "", "heading", None))
                for a in items:
                    i = row.assignments.index(a)
                    spec = spec_for(a.setting)
                    def change(value: object, i=i) -> SetStep:
                        changed = list(row.assignments); changed[i] = replace(changed[i], value=value)
                        return replace(row, assignments=tuple(changed))
                    start = self._ramp_start(self._keys[self._selected], a.device_id, a.setting)
                    ramp_field = [("   ↳ ramp", describe_ramp(a.ramp, spec, self._units, start).removeprefix("ramp "),
                                   "", "", None)] if a.ramp else []
                    if spec.kind == "bool":
                        fields.append((short_label(spec), format_value(a.value), "",
                                       "toggle", lambda change=change, a=a: change(not a.value)))
                    else:
                        fields.append((short_label(spec), self._units.show(a.value, spec), self._units.unit(spec),
                                       "text", lambda text, change=change, spec=spec: change(self._units.read(text, spec))))
                    fields += ramp_field
        elif isinstance(row, WaitStep):
            other = WaitPurpose.SETTLE if row.purpose is WaitPurpose.MEASURE else WaitPurpose.MEASURE
            fields += [
                ("Purpose", row.purpose.value.capitalize(), "", "toggle", lambda: replace(row, purpose=other)),
                ("Duration", format_duration(row.duration_seconds), "", "text",
                 lambda text: replace(row, duration_seconds=parse_duration(text))),
            ]
        elif isinstance(row, LoopStep):
            spec = spec_for(row.setting)
            def values(text: str) -> LoopStep:
                new = self._units.read_values(text, spec)
                # Pass names follow their values; drop them if the count changes.
                names = row.pass_names if len(new) == len(row.values) else ()
                return replace(row, values=new, pass_names=names)
            fields += [
                ("Each pass sets", self._target_text(row.device_id, row.setting), "", "", None),
                ("Values", self._units.show_values(row.values, spec), self._units.unit(spec), "text", values),
                ("Pass names", ", ".join(row.pass_names) or "—", "", "text",
                 lambda text: replace(row, pass_names=parse_pass_names(text))),
                ("Passes", str(row.iterations), "", "", None),
                ("Ramp", describe_ramp(row.ramp, spec, self._units,
                                       self._ramp_start(self._keys[self._selected], row.device_id, row.setting)
                                       ).removeprefix("ramp ") + ("; then from each value to the next" if row.ramp else "")
                 if row.ramp else "none (jumps to each value)", "", "", None),
            ]
        else:
            def count(text: str) -> RepeatStep:
                try:
                    return replace(row, count=int(text.strip()))
                except ValueError:
                    raise ValueError("Enter a whole number of at least 1") from None
            fields.append(("Times", str(row.count), "", "text", count))
        return fields

    def _edit_inline(self, label: ttk.Label, text: str, edit: Callable[..., OutlineRow]) -> None:
        """Swap the value for an entry box: Enter keeps the change, Esc drops it."""
        if self._inline is not None:
            return
        info = label.grid_info()
        entry = ttk.Entry(label.master, width=16)
        entry.grid(row=info["row"], column=info["column"], sticky="ew", pady=2)
        label.grid_remove()
        entry.insert(0, "" if text == "—" else text); entry.select_range(0, "end"); entry.focus_set()
        self._inline = entry

        def finish(save: bool, leaving: bool = False) -> None:
            if self._inline is not entry:
                return
            if save and not self._apply_quick(edit, entry.get()) and not leaving:
                return  # Keep the box open so the value can be corrected.
            if self._inline is entry:
                message = self._quick_error.cget("text") if leaving else ""
                self._show_quick()
                if message:
                    self._quick_error.configure(text=f"Not changed: {message}")

        entry.bind("<Return>", lambda _e: (finish(True), "break")[1])
        entry.bind("<KP_Enter>", lambda _e: (finish(True), "break")[1])
        entry.bind("<Escape>", lambda _e: (finish(False), "break")[1])
        entry.bind("<FocusOut>", lambda _e: finish(True, leaving=True))

    def _apply_quick(self, edit: Callable[..., OutlineRow], text: str | None) -> bool:
        try:
            new = edit() if text is None else edit(text)
        except (ValueError, TypeError) as error:
            self._quick_error.configure(text=str(error)); return False
        self._replace_selected(new)
        return True

    def _replace_selected(self, new: OutlineRow) -> None:
        if self._selected is None:
            return
        if new != self._rows[self._selected]:
            self._rows[self._selected] = new
            self._refresh()
        self._show_quick()

    def _timing_text(self) -> str:
        row = self._rows[self._selected] if self._selected is not None else None
        key = self._keys[self._selected] if row is not None else None
        if row is None or key is None:
            return ""
        if self._times is None:
            return f"⚠ {self._plan_problem}" if is_loop(row) or isinstance(row, SetStep) else ""
        if isinstance(row, SetStep):
            seconds = self._times.total(key)
            if seconds:
                return f"Takes {format_duration(seconds)} (ramps and hold) before the next step."
            return "Takes no time: the next step starts at once. The settings stay until a later step changes them."
        if not is_loop(row):
            return ""
        passes = self._times.passes.get(key, [])
        total = self._times.total(key)
        if not total:
            return "⚠ Nothing inside this loop takes time. Add a Wait inside it to set how long each pass lasts."
        shortest, longest = min(passes), max(passes)
        each = (f"Each pass lasts {format_duration(shortest)}" if abs(longest - shortest) < 0.5
                else f"Passes take {format_duration(shortest)}–{format_duration(longest)} (ramps differ)")
        return f"{each}, including ramps and holds; {len(passes)} pass{'es' if len(passes) != 1 else ''} = {format_duration(total)}."

    # --- pop-out editor ---

    def _edit_selected(self) -> None:
        if self._end_selected:
            self._edit_end_state(); return
        if self._selected is None:
            return
        index = self._selected
        if isinstance(self._rows[index], LoopEnd):
            index = partner(self._rows, index)
            self._selected, self._selected_device = index, None
            self._refresh(); self._show_quick()
        self._open_editor(index, focus_device=self._selected_device)

    def _open_editor(self, index: int, *, is_new: bool = False,
                     on_cancel: Callable[[], None] | None = None, focus_device: str | None = None) -> None:
        original = self._rows[index]

        def check(draft: OutlineRow) -> tuple[str, ...]:
            """Problems this edit would add, judged in the step's place in the recipe."""
            rows = list(self._rows); rows[index] = draft
            try:
                before = set(recipe_problems(self._recipe()))
                return tuple(p for p in recipe_problems(self._recipe(rows)) if p not in before)
            except (ValueError, TypeError):
                return ()

        def done(new: OutlineRow | None) -> None:
            if new is None:
                if on_cancel is not None:
                    on_cancel()
            elif new != original:
                self._rows[index] = new
                self._refresh()
            self._show_quick(); self._tree.focus_set()

        StepEditorDialog(self._root, original, roles=self._roles, check=check, on_done=done, is_new=is_new,
                         focus_device=focus_device, units=self._units,
                         values_before=values_before(from_outline(self._rows), self._outline_keys()[index]))

    # --- structure edits ---

    def _default_target(self) -> tuple[str, object]:
        if not self._roles:
            raise ValueError("This rig profile has no devices a recipe can control")
        role = self._roles[0]
        return role.device_id, SETTINGS[role.capability][0]

    def _add(self, row: OutlineRow) -> None:
        """Insert a step after the selection and open its editor; Cancel removes it again."""
        snapshot, selected = list(self._rows), self._selected
        self._rows, index = insert(self._rows, self._selected, row)  # type: ignore[arg-type]
        if is_loop(row):
            # A new loop starts with a wait inside, so how long each pass
            # lasts is visible and editable straight away.
            self._rows, _ = insert(self._rows, index, WaitStep(60.0, WaitPurpose.MEASURE))
        self._selected, self._selected_device = index, None
        self._refresh(); self._show_quick()

        def undo() -> None:
            self._rows, self._selected = snapshot, selected
            self._refresh()

        self._open_editor(index, is_new=True, on_cancel=undo)

    def _add_set(self) -> None:
        try:
            device_id, spec = self._default_target()
        except ValueError as error:
            messagebox.showerror("Add step", str(error), parent=self._root); return
        self._add(SetStep.one(device_id, spec.name, default_value(spec), unit_for(spec)))  # type: ignore[attr-defined]

    def _add_wait(self) -> None:
        self._add(WaitStep(60.0, WaitPurpose.MEASURE))

    def _add_loop(self) -> None:
        try:
            device_id, spec = self._default_target()
        except ValueError as error:
            messagebox.showerror("Add loop", str(error), parent=self._root); return
        values = (0.0,) if spec.kind == "number" else (True, False)  # type: ignore[attr-defined]
        self._add(LoopStep(device_id, spec.name, values, unit_for(spec)))  # type: ignore[attr-defined]

    def _add_ramp(self) -> None:
        """A Set step that ramps one setting between two fixed endpoints, for full control."""
        numeric = [(r.device_id, spec) for r in self._roles for spec in SETTINGS[r.capability] if spec.kind == "number"]
        if not numeric:
            messagebox.showerror("Add ramp", "This rig profile has no setting that can be ramped", parent=self._root)
            return
        device_id, spec = numeric[0]
        ramp = Ramp(RampKind.STEP_COUNT, 30.0, start=0.0, count=5)
        self._add(SetStep((Assignment(device_id, spec.name, 0.0, unit_for(spec), ramp),), "Ramp"))

    def _add_repeat(self) -> None:
        self._add(RepeatStep(2))

    def _move(self, direction: int) -> None:
        if self._end_selected:
            self._summary.configure(text="The end state is always the last row; edit it instead.", foreground=ERROR_TEXT)
            return
        if self._selected is None:
            return
        try:
            self._rows, self._selected = move(self._rows, self._selected, direction)
        except OutlineError as error:
            self._summary.configure(text=f"Can't move: {error}", foreground=ERROR_TEXT); return
        self._refresh(); self._show_quick()

    def _duplicate(self) -> None:
        if self._end_selected:
            self._summary.configure(text="The end state is always the last row; edit it instead.", foreground=ERROR_TEXT)
            return
        if self._selected is None:
            return
        self._rows, self._selected = duplicate(self._rows, self._selected)
        self._selected_device = None
        self._refresh(); self._show_quick()

    def _delete(self) -> None:
        if self._end_selected:
            self._summary.configure(text="The end state is always the last row; edit it instead.", foreground=ERROR_TEXT)
            return
        if self._selected is None:
            return
        index, row = self._selected, self._rows[self._selected]
        if isinstance(row, SetStep) and self._selected_device is not None:
            # A device line is selected: remove just that device from the step.
            kept = tuple(a for a in row.assignments if a.device_id != self._selected_device)
            self._selected_device = None
            if kept:
                self._rows[index] = replace(row, assignments=kept)
                self._refresh(); self._show_quick(); return
        self._rows = delete(self._rows, index)
        self._selected = min(index, len(self._rows) - 1) if self._rows else None
        self._selected_device = None
        self._refresh(); self._show_quick()

    # --- files and running ---

    @staticmethod
    def _set_entry(entry: ttk.Entry, text: str) -> None:
        entry.delete(0, "end"); entry.insert(0, text)

    def _load_rows(self, recipe: Recipe) -> None:
        self._rows = to_outline(recipe.steps); self._selected = self._selected_device = None
        self._end_state, self._end_selected = recipe.end_state, False
        self._set_entry(self._name, recipe.name)
        self._refresh(); self._show_quick()

    def _new(self) -> None:
        if self._rows and not messagebox.askyesno("New recipe", "Discard the current recipe?", parent=self._root):
            return
        self._load_rows(Recipe("Untitled recipe", end_state=default_end_state(self._all_roles)))

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(parent=self._root, defaultextension=".json", filetypes=(("Recipe JSON", "*.json"),))
        if path:
            try: save_recipe(self._recipe(), path)
            except Exception as error: messagebox.showerror("Save recipe", str(error), parent=self._root)

    def _load(self) -> None:
        path = filedialog.askopenfilename(parent=self._root, filetypes=(("Recipe JSON", "*.json"),))
        if path:
            try: self._load_rows(load_recipe(path))
            except Exception as error: messagebox.showerror("Load recipe", str(error), parent=self._root)

    def _browse(self) -> None:
        path = filedialog.askdirectory(parent=self._root)
        if path: self._set_entry(self._output, path)

    def _validate(self) -> None:
        try:
            self._runner.validate(self._recipe())
        except Exception as error:
            messagebox.showerror("Validate recipe", str(error), parent=self._root)
        else:
            messagebox.showinfo("Validate recipe", "The recipe is valid for the connected devices.", parent=self._root)

    def _start_recipe(self) -> None:
        if not self._output.get().strip():
            messagebox.showerror("Start recipe", "Choose an output folder first.", parent=self._root); return
        summary = "\n".join("   • " + line for line in end_state_summary(self._end_state, self._all_roles, self._units))
        warnings = end_state_warnings(self._end_state, self._all_roles)
        message = ("When the run finishes, or if you press Stop:\n\n" + summary
                   + ("\n\n" + "\n".join("⚠ " + w for w in warnings) if warnings else "")
                   + "\n\nStart the recipe?")
        if not messagebox.askokcancel("Start recipe", message, icon="warning" if warnings else "question",
                                      parent=self._root):
            return
        try:
            self._runner.start(self._recipe(), recording_root=Path(self._output.get()), operator="operator")
        except Exception as error:
            messagebox.showerror("Start recipe", str(error), parent=self._root)

    def _tick(self) -> None:
        if not self._root.winfo_exists():
            return
        status = self._runner.status
        if status.state.value == "running":
            where = " › ".join(status.position)
            text = (f"Running: {where + ' › ' if where else ''}{status.active_step or ''}   "
                    f"({format_duration(round(status.elapsed_seconds))} elapsed, "
                    f"~{format_duration(round(status.estimated_remaining_seconds))} left)")
        elif status.state.value == "idle":
            text = "No recipe running"
        else:
            text = f"Recipe {status.state.value}" + (f": {status.failure}" if status.failure else "")
        self._status.configure(text=text)
        self._start.configure(state="disabled" if self._runner.is_running else "normal")
        self._root.after(250, self._tick)
