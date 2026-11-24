"""The v2 Operation window: the same screen, laid out compactly.

Only the layout and the channel display are new.  Everything the screen
*does* (connecting, monitoring, recording, notes, confirmations before a
supply or heater turns on, closing safely) is inherited unchanged from
``OperationWindow``, so the two cannot drift apart in behaviour.

Each system tab is a grid: quantities down, devices across, so the same
quantity on different devices can be compared side by side.  A setting is
shown under the reading it controls and changes on a click; readings are
plain text and open a trend on a double-click.  Quality and the time of each
reading are on hover; anything needing attention turns red, and so does its
tab title.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.data.electrolysers import NO_ELECTROLYSER_FOLDER
from rig_control.ui.common.theme_v2 import (
    ACCENT, BOLD_FONT, CAPTION_FONT, DANGER_SOFT, DANGER_TEXT, ERROR, GOOD_SOFT, GOOD_TEXT, MUTED,
    NOTICE_SOFT, NOTICE_TEXT, SURFACE,
)
from rig_control.ui.operation.compact_layout import (
    OVERVIEW_LINES, needs_attention, system_layout,
)
from rig_control.ui.operation.compact_panels import QuantityGrid, ScrollArea
from rig_control.ui.operation.model import OperationChannelRow
from rig_control.ui.operation.trend_window import TrendWindow
from rig_control.ui.operation.window import OperationWindow

_OVERVIEW = "Overview"


def _onto_card(parent: tk.Misc) -> None:
    """Restyle inherited plain frames and labels so they sit on a white card."""
    for child in parent.winfo_children():
        if isinstance(child, ttk.Label):
            child.configure(style="Card.TLabel")
        elif isinstance(child, ttk.Frame) and not isinstance(child, ttk.LabelFrame):
            child.configure(style="Plain.Card.TFrame")
            _onto_card(child)


class _Chosen:
    """Stands in for an editor widget: ``_commit_cell_edit`` only reads ``get()``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def get(self) -> str:
        return self._text


class OperationWindowV2(OperationWindow):
    """Live monitoring and recording, with the compact v2 layout."""

    def __init__(self, root, view_model, **kwargs) -> None:
        self._grids: dict[str, QuantityGrid | dict[str, QuantityGrid]] = {}
        self._tab_frames: dict[str, ttk.Frame] = {}
        self._tab_labels: dict[str, str] = {}
        super().__init__(root, view_model, **kwargs)
        self._root.geometry("1320x800")
        self._root.minsize(1000, 640)

    # --- layout ---

    def _create_widgets(self, profile_name: str) -> None:
        # The old table code is not used: no trees means the inherited
        # display update skips them, and this class draws the grids instead.
        self._channel_trees = {}
        self._measurements = None
        self._root.configure(background=SURFACE)
        main = ttk.Frame(self._root, padding=(14, 10, 14, 12))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_header(main, profile_name)
        self._build_toolbar(main)

        body = ttk.Frame(main)
        body.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=350)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self._tabs = ttk.Notebook(left)
        self._tabs.grid(row=0, column=0, sticky="nsew")
        for system in (_OVERVIEW, *self._view_model.systems()):
            frame = ttk.Frame(self._tabs, style="Plain.Card.TFrame")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            scroll = ScrollArea(frame)
            scroll.grid(row=0, column=0, sticky="nsew")
            scroll.inner.columnconfigure(0, weight=1)
            self._tab_frames[system] = frame
            self._tab_labels[system] = system
            self._tabs.add(frame, text=f"  {system}  ")
            if system == _OVERVIEW:
                self._grids[system] = {}
                self._overview_area = scroll.inner
            else:
                grid = QuantityGrid(scroll.inner, on_edit=self._edit_row, on_trend=self._open_trend)
                grid.grid(row=0, column=0, sticky="new", padx=6, pady=4)
                self._grids[system] = grid

        self._build_warnings(left)
        self._build_recording(body)
        self._show_monitoring_state(False)

    def _build_header(self, main: ttk.Frame, profile_name: str) -> None:
        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        titles = ttk.Frame(header)
        titles.grid(row=0, column=0, sticky="w")
        ttk.Label(titles, text="Rig operation", style="Title.TLabel").pack(side="left")
        ttk.Label(titles, text=f"   {profile_name}", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        # tk.Label rather than ttk so the pill can carry its own colours.
        self._monitor_status = tk.Label(header, font=BOLD_FONT, padx=10, pady=2)
        self._monitor_status.grid(row=0, column=2, padx=(8, 10))
        ttk.Button(header, text="Safe state — all devices", style="Danger.TButton",
                   command=self._enter_global_safe_state).grid(row=0, column=3)

    def _build_toolbar(self, main: ttk.Frame) -> None:
        controls = ttk.Frame(main)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._connect_button = ttk.Button(controls, text="Connect devices", command=self._connect_all)
        self._connect_button.grid(row=0, column=0, padx=(0, 6))
        self._monitor_button = ttk.Button(controls, text="Start monitoring", style="Accent.TButton",
                                          command=self._toggle_monitoring)
        self._monitor_button.grid(row=0, column=1)
        ttk.Separator(controls, orient="vertical").grid(row=0, column=2, sticky="ns", padx=10)
        tools = ttk.Frame(controls)
        tools.grid(row=0, column=3)
        ttk.Button(tools, text="Live dashboard", command=self._open_live_dashboard).pack(side="left", padx=(0, 6))
        ttk.Button(tools, text="Recipe builder", command=self._open_recipe_builder).pack(side="left", padx=(0, 6))
        if self._view_model.leak_check is not None and self._view_model.leak_check.available:
            ttk.Button(tools, text="Leak check", command=self._open_leak_check).pack(side="left")
        controls.columnconfigure(4, weight=1)
        self._action_status = ttk.Label(controls, text="Ready", style="Muted.TLabel", anchor="e")
        self._action_status.grid(row=0, column=4, sticky="e", padx=(12, 0))
        # Inherited: the "manual changes during a recipe" row, shown only while one runs.
        self._build_override_controls(controls)
        self._override_choice.master.grid_configure(row=1, column=0, columnspan=5, sticky="w")
        self._override_choice.master.grid_remove()
        ttk.Label(controls, style="Caption.TLabel", text=(
            "Click an accent value to change it  ·  double-click a reading for its trend  ·  "
            "hover for quality and time"
        )).grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

    def _build_warnings(self, left: ttk.Frame) -> None:
        self._warnings_frame = tk.Frame(left, background=DANGER_SOFT, padx=8, pady=6)
        self._warnings_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._warnings_frame.columnconfigure(0, weight=1)
        top = tk.Frame(self._warnings_frame, background=DANGER_SOFT)
        top.grid(row=0, column=0, sticky="ew")
        tk.Label(top, text="Warnings", font=BOLD_FONT, background=DANGER_SOFT,
                 foreground=DANGER_TEXT).pack(side="left")
        ttk.Button(top, text="Copy", style="Small.TButton",
                   command=self._copy_warnings).pack(side="right")
        self._warnings = tk.Listbox(
            self._warnings_frame, foreground=DANGER_TEXT, background=DANGER_SOFT,
            exportselection=False, height=3, borderwidth=0, relief="flat",
            highlightthickness=0, activestyle="none",
        )
        self._warnings.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._warnings.bind("<Control-c>", self._copy_warnings)
        self._warnings.bind("<Double-1>", self._show_selected_warning)
        self._warnings_frame.grid_remove()

    def _build_recording(self, body: ttk.Frame) -> None:
        """The run, in the order it is filled in: who and what, where it saves, then record.

        Each box is as wide as what goes in it: initials and an area are a few
        characters, a description and a folder take the panel's width.
        """
        panel = ttk.Frame(body, padding=(14, 10, 14, 12), style="Card.TFrame")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        self._entries = {}

        def section(row: int, text: str, top: int = 10) -> None:
            ttk.Label(panel, text=text, style="Heading.Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=(top, 4))

        def field(parent, row: int, label: str) -> None:
            ttk.Label(parent, text=label, style="Muted.Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=2)

        section(0, "Experiment recording", top=0)
        run = ttk.Frame(panel, style="Plain.Card.TFrame")
        run.grid(row=1, column=0, sticky="ew")
        run.columnconfigure(1, weight=1)

        field(run, 0, "Operator")
        self._entries["operator"] = ttk.Entry(run, width=8)
        self._entries["operator"].grid(row=0, column=1, sticky="w", pady=2)

        field(run, 1, "Electrolyser")
        builds = ttk.Frame(run, style="Plain.Card.TFrame")
        builds.grid(row=1, column=1, sticky="ew", pady=2)
        builds.columnconfigure(0, weight=1)
        self._entries["electrolyser"] = ttk.Combobox(
            builds, state="readonly", width=18, postcommand=self._refresh_electrolysers)
        self._entries["electrolyser"].grid(row=0, column=0, sticky="ew")
        self._electrolyser_buttons = (
            ttk.Button(builds, text="New", style="Small.TButton", command=self._new_electrolyser),
            ttk.Button(builds, text="Edit", style="Small.TButton", command=self._edit_electrolyser),
        )
        for column, button in enumerate(self._electrolyser_buttons, start=1):
            button.grid(row=0, column=column, padx=(4, 0))

        field(run, 2, "Active area")
        area_row = ttk.Frame(run, style="Plain.Card.TFrame")
        area_row.grid(row=2, column=1, sticky="w", pady=2)
        self._entries["area"] = ttk.Entry(area_row, width=7, justify="right")
        self._entries["area"].pack(side="left")
        self._area_hint = ttk.Label(area_row, text="cm²", style="Muted.Card.TLabel")
        self._area_hint.pack(side="left", padx=(4, 0))

        field(run, 3, "Description")
        self._entries["description"] = ttk.Entry(run)
        self._entries["description"].grid(row=3, column=1, sticky="ew", pady=2)

        field(run, 4, "Start notes")
        self._entries["notes"] = ttk.Entry(run)
        self._entries["notes"].grid(row=4, column=1, sticky="ew", pady=2)

        section(2, "Saves to")
        saves = ttk.Frame(panel, style="Plain.Card.TFrame")
        saves.grid(row=3, column=0, sticky="ew")
        saves.columnconfigure(0, weight=1)
        self._id_preview = ttk.Label(saves, style="Card.TLabel", wraplength=300, justify="left")
        self._id_preview.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._entries["output"] = ttk.Entry(saves)
        self._entries["output"].grid(row=1, column=0, sticky="ew")
        ttk.Button(saves, text="Browse…", style="Small.TButton",
                   command=self._browse_output).grid(row=1, column=1, padx=(4, 0))

        for key in ("operator", "description"):
            self._entries[key].bind("<KeyRelease>", lambda _event: self._refresh_id_preview())
        self._entries["area"].bind("<KeyRelease>", lambda _event: self._area_typed())
        self._electrolyser_text = ""
        self._entries["electrolyser"].set(NO_ELECTROLYSER_FOLDER)
        self._entries["electrolyser"].bind("<<ComboboxSelected>>", lambda _event: self._electrolyser_chosen())
        self._entries["output"].bind("<FocusOut>", lambda _event: self._output_edited())
        area = self._view_model.cell_area.cm2
        if area is not None:
            self._entries["area"].insert(0, f"{area:g}")

        actions = ttk.Frame(panel, style="Plain.Card.TFrame")
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(1, weight=1)
        self._record_button = ttk.Button(actions, text="Start recording", style="Accent.TButton",
                                         command=self._toggle_recording)
        self._record_button.grid(row=0, column=0, sticky="w")
        self._recording_status = tk.Label(actions, font=BOLD_FONT, padx=8, pady=1)
        self._recording_status.grid(row=0, column=1, sticky="w", padx=(8, 0))

        section(5, "Notes during the run", top=14)
        notes = ttk.Frame(panel, style="Plain.Card.TFrame")
        notes.grid(row=6, column=0, sticky="ew")
        notes.columnconfigure(0, weight=1)
        self._create_note_box(notes, row=0)
        _onto_card(notes)
        card = ttk.Style(self._root).lookup("Plain.Card.TFrame", "background")
        self._recent_notes.configure(background=card, foreground=MUTED, font=CAPTION_FONT,
                                     height=8)
        self._recent_notes.tag_configure("time", foreground=ACCENT)

        ttk.Separator(panel).grid(row=7, column=0, sticky="ew", pady=(14, 8))
        more = ttk.Frame(panel, style="Plain.Card.TFrame")
        more.grid(row=8, column=0, sticky="ew")
        self._export_button = ttk.Button(more, text="Export Excel…", style="Small.TButton",
                                         command=self._export_excel)
        self._export_button.pack(side="left")
        ttk.Button(more, text="Save graph data…", style="Small.TButton",
                   command=self._save_graph_data).pack(side="left", padx=(6, 0))
        self._review_button = ttk.Button(more, text="Review a run…", style="Small.TButton",
                                         command=self._review_past_run)
        self._review_button.pack(side="left", padx=(6, 0))
        ttk.Label(panel, style="Caption.Card.TLabel", wraplength=300, justify="left", text=(
            "Every new reading is saved once. Measurement intervals are set per "
            "device in Device Setup."
        )).grid(row=9, column=0, sticky="w", pady=(8, 0))

    # --- display ---

    def _show_monitoring_state(self, monitoring: bool) -> None:
        if monitoring:
            self._monitor_status.configure(text="● Monitoring", foreground=GOOD_TEXT, background=GOOD_SOFT)
        else:
            self._monitor_status.configure(text="● Not monitoring — readings are not updating",
                                           foreground=NOTICE_TEXT, background=NOTICE_SOFT)

    def _finish_connect_all(self, results) -> None:
        """Say so on the status line; only problems need a box to click away."""
        self._connection_in_progress = False
        self._connect_button.configure(state="normal")
        failures = [result.summary for result in results if not result.succeeded]
        if failures:
            messagebox.showerror("Device connection problems", "\n".join(failures), parent=self._root)
            self._set_status(f"{len(failures)} device(s) did not connect; see the message for details.")
        else:
            self._set_status("All devices connected"
                             + (" and monitoring." if self._view_model.is_monitoring else "."))
        self._update_display()

    def _connect_all(self) -> None:
        if self._connection_in_progress:
            return
        super()._connect_all()
        if self._connection_in_progress:  # Not cancelled at the reconnect question.
            self._set_status("Connecting devices…")

    def _update_display(self, *, refresh_trends: bool = False) -> None:
        super()._update_display(refresh_trends=refresh_trends)
        # The next thing to press is the one in accent: connect first, then record.
        monitoring = self._view_model.is_monitoring
        for button, style in ((self._connect_button, "TButton" if monitoring else "Accent.TButton"),
                              (self._monitor_button, "TButton")):
            if str(button.cget("style")) != style:
                button.configure(style=style)
        self._monitor_button.configure(text="Pause monitoring" if monitoring else "Start monitoring")
        self._refresh_grids()
        self._refresh_recording_pill()
        if self._displayed_warnings:
            self._warnings.configure(height=min(len(self._displayed_warnings), 4))
            self._warnings_frame.grid()
        else:
            self._warnings_frame.grid_remove()

    def _refresh_recording_pill(self) -> None:
        recording = self._view_model.is_recording
        finalising = getattr(self, "_recording_finalising", False)
        if recording:
            text, foreground, background = "● Recording", ERROR, DANGER_SOFT
        elif finalising:
            text, foreground, background = "Writing exports…", NOTICE_TEXT, NOTICE_SOFT
        else:
            text, foreground, background = "Not recording", MUTED, SURFACE
        # The inherited update writes plain text here every tick; restyle it each time.
        self._recording_status.configure(text=text, foreground=foreground, background=background)
        style = "Danger.TButton" if recording else "Accent.TButton"
        if str(self._record_button.cget("style")) != style:
            self._record_button.configure(style=style)

    def _refresh_grids(self) -> None:
        rows = tuple(self._channel_rows.values())
        by_system: dict[str, list[OperationChannelRow]] = {}
        for row in rows:
            by_system.setdefault(row.system, []).append(row)
        for system, grid in self._grids.items():
            if system == _OVERVIEW:
                continue
            system_rows = by_system.get(system, [])
            grid.show(system_layout(system_rows))  # type: ignore[union-attr]
            self._mark_tab(system, needs_attention(system_rows))
        self._refresh_overview(by_system)
        self._mark_tab(_OVERVIEW, needs_attention(rows))

    def _refresh_overview(self, by_system: dict[str, list[OperationChannelRow]]) -> None:
        grids: dict[str, QuantityGrid] = self._grids[_OVERVIEW]  # type: ignore[assignment]
        for index, system in enumerate(s for s in self._view_model.systems()):
            layout = system_layout(by_system.get(system, ()), only=OVERVIEW_LINES)
            grid = grids.get(system)
            if grid is None:
                grid = QuantityGrid(self._overview_area, on_edit=self._edit_row,
                                    on_trend=self._open_trend, title=system)
                grid.grid(row=index, column=0, sticky="new", padx=6, pady=(0, 2))
                grids[system] = grid
            if layout.is_empty:
                grid.grid_remove()
            else:
                grid.grid()
                grid.show(layout)

    def _mark_tab(self, system: str, level: str) -> None:
        text = f"  {'⚠ ' if level == 'error' else ''}{system}  "
        frame = self._tab_frames.get(system)
        if frame is not None and self._tabs.tab(frame, "text") != text:
            self._tabs.tab(frame, text=text)

    def _refresh_override_controls(self) -> None:
        super()._refresh_override_controls()
        row = getattr(getattr(self, "_override_choice", None), "master", None)
        if row is None:
            return
        if self._override_running and not row.winfo_ismapped():
            row.grid()
        elif not self._override_running and row.winfo_ismapped():
            row.grid_remove()

    # --- acting on a value ---

    def _latest(self, row: OperationChannelRow) -> OperationChannelRow | None:
        """The row as it is now: limits and state can change between paint and click."""
        return self._channel_rows.get(row.key)

    def _edit_row(self, row: OperationChannelRow, anchor: tk.Widget) -> None:
        row = self._latest(row) or row
        if not row.writable:
            return
        self._cancel_cell_edit()
        if row.editor == "action":
            if row.channel == "resume_pressure_control" and not messagebox.askyesno(
                "Resume BPR regulation",
                "Release the outlet valve hold and resume regulation at the existing "
                "pressure setpoint?\n\nThis may discharge gas toward the GC.",
                parent=self._root,
            ):
                return
            self._run_operation_action(
                f"Updating {row.channel_name}",
                lambda: self._view_model.apply_channel_value(row.device_id, row.channel, True),
            )
            return
        if row.editor in {"boolean", "running"}:
            # One click flips it; turning something on is still confirmed.
            self._commit_cell_edit(row, _Chosen("Off" if row.value else "On"))  # type: ignore[arg-type]
            return
        # Made in the grid, not the small cell frame, so it is not clipped;
        # placed over the value it changes.
        host = anchor
        while host is not None and not isinstance(host, QuantityGrid):
            host = host.master
        if host is None:
            return
        width = max(anchor.winfo_width(), 120)
        height = max(anchor.winfo_height(), 22)
        if row.editor in {"mode", "direction"}:
            choices = (("constant_voltage", "constant_current") if row.editor == "mode"
                       else ("forward", "reverse"))
            editor = ttk.Combobox(host, values=choices, state="readonly")
            if row.value is not None:
                editor.set(str(row.value))
            editor.bind("<<ComboboxSelected>>",
                        lambda _event: self._commit_cell_edit(row, editor))
        else:
            editor = ttk.Entry(host, font=("Consolas", 10), justify="right")
            editor.insert(0, "" if row.value is None else format(float(row.value), ".6g"))
            editor.selection_range(0, "end")
            editor.bind("<Return>", lambda _event: self._commit_cell_edit(row, editor))
        editor.bind("<Escape>", lambda _event: self._cancel_cell_edit())
        editor.bind("<FocusOut>", lambda _event: self._root.after_idle(self._cancel_if_unfocused))
        editor.place(in_=anchor, relx=1.0, rely=0.0, anchor="ne", width=width, height=height)
        editor.lift()
        editor.focus_set()
        self._editor = editor
        self._editor_apply = None

    def _cancel_if_unfocused(self) -> None:
        editor = self._editor
        if editor is None:
            return
        try:
            focused = self._root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        # A combobox's drop-down list takes focus while open; that is still editing.
        if focused is editor or (focused is not None and str(focused).startswith(str(editor))):
            return
        if focused is not None and "popdown" in str(focused):
            return
        self._cancel_cell_edit()

    def _open_trend(self, key: tuple[str, str]) -> None:
        if not self._view_model.measurement_history(*key):
            self._set_status("No history yet for that reading.")
            return
        existing = self._trend_windows.get(key)
        if existing is not None and existing.exists:
            existing.focus()
            return
        device_id, channel = key
        self._trend_windows[key] = TrendWindow(
            self._root,
            device_id=device_id,
            channel=channel,
            history_provider=(
                lambda seconds, selected_device=device_id, selected_channel=channel:
                self._view_model.display_history_for_period(selected_device, selected_channel, seconds)
            ),
            on_close=lambda selected_key=key: self._trend_windows.pop(selected_key, None),
            history_limit=self._view_model.history_limit,
            annotations=self._annotation_hub,
        )

    # --- recipe builder with the plan graph ---

    def _open_recipe_builder(self) -> None:
        if self._view_model.recipe_runner is None or self._view_model.profile is None:
            messagebox.showerror(
                "Recipe builder",
                "Recipe building requires the active rig profile and control service.",
                parent=self._root,
            )
            return
        if self._recipe_window is not None and self._recipe_window.winfo_exists():
            self._recipe_window.lift()
            return
        from rig_control.ui.operation.recipe_window_v2 import RecipeBuilderWindowV2

        self._recipe_window = tk.Toplevel(self._root)
        RecipeBuilderWindowV2(
            self._recipe_window,
            self._view_model.recipe_runner,
            self._view_model.profile,
            current_unit=self._view_model.display_units.current,
            display_units=self._view_model.display_units,
            recording_details=self.recording_details,
            plan_recording=self.plan_recipe_recording,
            recording_preview=self.recording_preview,
        )

        def close() -> None:
            if self._recipe_window is not None:
                self._recipe_window.destroy()
                self._recipe_window = None
        self._recipe_window.protocol("WM_DELETE_WINDOW", close)
