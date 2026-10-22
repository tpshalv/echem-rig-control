import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime
from tkinter import filedialog, messagebox, ttk
from queue import Empty, Queue
from threading import Thread
from traceback import format_exc
from typing import Any

from rig_control.data.annotations import ANNOTATION_KIND_LABELS, ANNOTATION_KINDS
from rig_control.models import Event, EventSeverity, EventSink
from rig_control.ui.common.theme import (
    ERROR_TEXT, MUTED_TEXT, SECTION_FONT, SUCCESS_BACKGROUND, SUCCESS_TEXT, TITLE_FONT,
    WARNING_BACKGROUND, WARNING_TEXT,
)
from rig_control.ui.operation.annotation_dialog import RunReviewDialog
from rig_control.ui.operation.annotation_ui import AnnotationHub
from rig_control.ui.manual_control.types import DEFAULT_WIRING_CURRENT_CEILING_AMPS
from rig_control.ui.operation.model import (
    OperationActionResult,
    OperationChannelRow,
    OperationViewModel,
)
from rig_control.ui.operation.trend_window import TrendWindow
from rig_control.ui.operation.dashboard_window import (
    DashboardSignal,
    LiveDashboardWindow,
)
from rig_control.ui.operation.recipe_window import RecipeBuilderWindow


def format_operation_boolean(channel: str, value: bool) -> str:
    if channel == "watchdog_tripped":
        return "True" if value else "False"
    return "On" if value else "Off"


class OperationWindow:
    """Live monitoring and experiment-recording window."""

    def __init__(
        self,
        root: tk.Tk,
        view_model: OperationViewModel,
        *,
        profile_name: str,
        default_output_directory: str = "",
        event_sink: EventSink | None = None,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._event_sink = event_sink
        self._refresh_failed = False
        self._after_id: str | None = None
        self._measurement_keys: dict[str, tuple[str, str]] = {}
        self._measurement_items: dict[tuple[str, str], str] = {}
        self._measurement_values: dict[tuple[str, str], tuple[str, ...]] = {}
        self._next_measurement_item = 0
        self._displayed_warnings: tuple[tuple[str, str], ...] = ()
        self._ui_tick_count = 0
        self._ui_tick_failure_count = 0
        self._last_successful_ui_tick: datetime | None = None
        self._measurement_rows_created = 0
        self._measurement_rows_updated = 0
        self._measurement_rows_deleted = 0
        self._warning_display_rebuilds = 0
        self._trend_windows: dict[tuple[str, str], TrendWindow] = {}
        self._dashboard_window: LiveDashboardWindow | None = None
        self._recipe_window: tk.Toplevel | None = None
        self._channel_trees: dict[str | None, ttk.Treeview] = {}
        self._tree_item_keys: dict[tuple[str | None, str], tuple[str, str]] = {}
        self._channel_rows: dict[tuple[str, str], OperationChannelRow] = {}
        self._editor: tk.Widget | None = None
        self._editor_apply: ttk.Button | None = None
        self._connection_results: Queue[tuple[OperationActionResult, ...]] = Queue()
        self._background_results: Queue[Callable[[], None]] = Queue()
        self._connection_in_progress = False
        self._action_in_progress = False
        self._action_description: str | None = None
        self._recording_finalising = False
        self._pending_close: Callable[[tuple[str, ...]], None] | None = None
        self._updates_cancelled = False
        self._note_started_at: datetime | None = None
        self._displayed_annotations: tuple = ()
        self._review_export_directory = None
        self._annotation_hub = AnnotationHub(
            root, view_model,
            on_changed=self._annotations_changed, on_status=self._set_status,
        )
        self._root.title("Echem Rig Control — Operation")
        self._root.geometry("1050x760")
        self._root.minsize(850, 620)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

        self._create_widgets(profile_name)
        if default_output_directory:
            self._entries["output"].insert(0, default_output_directory)
        self._update_display()
        self._schedule_update()

    def _create_widgets(self, profile_name: str) -> None:
        paper = "#EEF3F7"
        ink = "#16253A"
        style = ttk.Style(self._root)
        style.configure("Blueprint.TFrame", background=paper)
        style.configure("Blueprint.TLabel", background=paper, foreground=ink)
        style.configure("Blueprint.Treeview", background=paper, fieldbackground=paper,
                        foreground=ink, rowheight=25, font=("Segoe UI", 9))
        style.configure("Blueprint.Treeview.Heading", background=paper,
                        foreground=ink, font=("Segoe UI", 8, "bold"), relief="flat")
        style.configure("Blueprint.TNotebook", background=paper, borderwidth=0)
        style.configure("Blueprint.TNotebook.Tab", font=("Segoe UI", 8, "bold"),
                        padding=(14, 7), foreground=ink)
        self._root.configure(background=paper)
        main = ttk.Frame(self._root, padding=16, style="Blueprint.TFrame")
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        ttk.Label(main, text="Rig operation", font=TITLE_FONT,
                  style="Blueprint.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(main, text=f"Profile: {profile_name}",
                  style="Blueprint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        body = ttk.Frame(main, style="Blueprint.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=4)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(1, weight=1)

        controls = ttk.Frame(body, style="Blueprint.TFrame")
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self._connect_button = ttk.Button(
            controls,
            text="Connect configured devices",
            command=self._connect_all,
        )
        self._connect_button.grid(row=0, column=0, padx=(0, 8))
        self._monitor_button = ttk.Button(
            controls,
            text="Start monitoring",
            command=self._toggle_monitoring,
        )
        self._monitor_button.grid(row=0, column=1)
        # tk.Label rather than ttk so the badge can carry its own colours.
        self._monitor_status = tk.Label(
            controls, font=SECTION_FONT, padx=8, pady=2, relief="solid", borderwidth=1,
        )
        self._monitor_status.grid(row=0, column=2, padx=(8, 0))
        self._show_monitoring_state(False)
        controls.columnconfigure(3, weight=1)
        ttk.Label(
            controls,
            text=("■ SET: double-click VALUE, type, press Enter   •   "
                  "double-click DEVICE/CHANNEL for trend"),
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=3, padx=18)
        ttk.Button(controls, text="Enter safe state — all devices",
                   command=self._enter_global_safe_state).grid(row=0, column=4)
        ttk.Button(
            controls,
            text="Live dashboard",
            command=self._open_live_dashboard,
        ).grid(row=1, column=4, sticky="e", pady=(6, 0))
        ttk.Button(
            controls,
            text="Recipe builder",
            command=self._open_recipe_builder,
        ).grid(row=1, column=5, sticky="e", padx=(8, 0), pady=(6, 0))
        self._action_status = ttk.Label(controls, text="Ready")
        self._action_status.grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        left = ttk.Frame(body, style="Blueprint.TFrame")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        tabs = ttk.Notebook(left, style="Blueprint.TNotebook")
        tabs.grid(row=0, column=0, sticky="nsew")
        for system, label in [(None, "OVERVIEW"), *[(value, value.upper()) for value in self._view_model.systems()]]:
            frame = ttk.Frame(tabs, padding=(0, 8, 0, 0), style="Blueprint.TFrame")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            tree = self._create_channel_tree(frame, system)
            self._channel_trees[system] = tree
            tabs.add(frame, text=label)
        self._measurements = self._channel_trees[None]

        warnings_frame = ttk.LabelFrame(left, text="PERSISTENT WARNINGS", padding=8)
        warnings_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        warnings_frame.columnconfigure(0, weight=1)
        self._warnings = tk.Listbox(
            warnings_frame,
            foreground=ERROR_TEXT,
            background=paper,
            exportselection=False,
            height=3,
        )
        self._warnings.grid(row=0, column=0, sticky="nsew")
        warning_vertical = ttk.Scrollbar(
            warnings_frame,
            orient="vertical",
            command=self._warnings.yview,
        )
        warning_vertical.grid(row=0, column=1, sticky="ns")
        warning_horizontal = ttk.Scrollbar(
            warnings_frame,
            orient="horizontal",
            command=self._warnings.xview,
        )
        warning_horizontal.grid(row=1, column=0, sticky="ew")
        self._warnings.configure(
            yscrollcommand=warning_vertical.set,
            xscrollcommand=warning_horizontal.set,
        )
        self._warnings.bind("<Control-c>", self._copy_warnings)
        self._warnings.bind("<Double-1>", self._show_selected_warning)
        ttk.Button(
            warnings_frame,
            text="Copy warnings",
            command=self._copy_warnings,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        recording = ttk.LabelFrame(body, text="EXPERIMENT RECORDING", padding=12)
        recording.grid(row=1, column=1, sticky="nsew")
        recording.columnconfigure(1, weight=1)
        fields = (
            ("Experiment ID", "experiment_id"),
            ("Operator", "operator"),
            ("Output folder", "output"),
            ("Start notes", "notes"),
        )
        self._entries: dict[str, ttk.Entry] = {}
        for row, (label, key) in enumerate(fields):
            ttk.Label(recording, text=f"{label}:").grid(
                row=row, column=0, sticky="w", pady=2
            )
            entry = ttk.Entry(recording)
            entry.grid(row=row, column=1, sticky="ew", padx=(8, 4), pady=2)
            self._entries[key] = entry
        ttk.Button(recording, text="Browse…", command=self._browse_output).grid(
            row=2, column=2, pady=2
        )

        self._create_note_box(recording, row=4)

        actions = ttk.Frame(recording)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._record_button = ttk.Button(
            actions, text="Start recording", command=self._toggle_recording
        )
        self._record_button.grid(row=0, column=0)
        self._export_button = ttk.Button(
            actions, text="Export Excel", command=self._export_excel
        )
        self._export_button.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._review_button = ttk.Button(
            actions, text="Review run…", command=self._review_past_run
        )
        self._review_button.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        self._recording_status = ttk.Label(
            actions, text="Not recording", font=SECTION_FONT
        )
        self._recording_status.grid(row=0, column=1, padx=(12, 0))
        ttk.Label(
            recording,
            text=("Every new reading is saved once. Measurement intervals "
                  "are configured per device in Device Setup."),
            wraplength=280,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

        for text, x, y, anchor in (("┌", 2, 2, "nw"), ("┐", 1, 2, "ne"),
                                    ("└", 2, 1, "sw"), ("┘", 1, 1, "se")):
            ttk.Label(main, text=text, style="Blueprint.TLabel").place(
                relx=x if x == 1 else 0, rely=y if y == 1 else 0,
                x=4 if x == 2 else -4, y=4 if y == 2 else -4, anchor=anchor)

    def _create_note_box(self, parent: ttk.Frame, *, row: int) -> None:
        """One line for notes during a run; the time is taken at the first key."""
        box = ttk.Frame(parent)
        box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        box.columnconfigure(1, weight=1)
        self._note_kind_values = {ANNOTATION_KIND_LABELS[kind]: kind for kind in ANNOTATION_KINDS}
        self._note_kind = ttk.Combobox(
            box, values=tuple(self._note_kind_values), state="readonly", width=9,
        )
        self._note_kind.set(ANNOTATION_KIND_LABELS["lab_note"])
        self._note_kind.grid(row=0, column=0, padx=(0, 4))
        self._note_entry = ttk.Entry(box)
        self._note_entry.grid(row=0, column=1, sticky="ew")
        self._note_entry.bind("<KeyPress>", self._note_key_pressed)
        self._note_entry.bind("<KeyRelease>", self._note_key_released)
        self._note_entry.bind("<Return>", lambda _event: self._add_typed_note())
        self._note_entry.bind("<Escape>", lambda _event: self._clear_note())
        self._note_button = ttk.Button(box, text="Add note", command=self._add_typed_note)
        self._note_button.grid(row=0, column=2, padx=(4, 0))
        self._note_time = ttk.Label(box, foreground=MUTED_TEXT, width=9)
        self._note_time.grid(row=0, column=3, padx=(6, 0))
        self._recent_notes = ttk.Label(
            parent, foreground=MUTED_TEXT, justify="left", wraplength=320,
            text="Notes: Ctrl+N to type one, or double-click a graph.",
        )
        self._recent_notes.grid(row=row + 1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for sequence in ("<Control-n>", "<Control-N>"):
            self._root.bind(sequence, self._focus_note_box, add="+")

    def _focus_note_box(self, _event: tk.Event | None = None) -> str:
        if self._view_model.is_recording:
            self._note_entry.focus_set()
        else:
            self._set_status("Start recording to add notes; notes are saved with the run.")
        return "break"

    def _note_key_pressed(self, event: tk.Event) -> None:
        # Notes are usually written just after the event, so the time is
        # taken when typing starts rather than when Enter is pressed.
        if self._note_started_at is None and event.char and event.char.isprintable():
            self._note_started_at = datetime.now(UTC)
            self._note_time.configure(
                text=self._note_started_at.astimezone().strftime("%H:%M:%S")
            )

    def _note_key_released(self, _event: tk.Event) -> None:
        if not self._note_entry.get().strip():
            self._note_started_at = None
            self._note_time.configure(text="")

    def _clear_note(self) -> str:
        self._note_entry.delete(0, "end")
        self._note_started_at = None
        self._note_time.configure(text="")
        return "break"

    def _add_typed_note(self) -> str:
        text = self._note_entry.get().strip()
        if not text:
            return "break"
        result = self._view_model.add_annotation(
            self._note_kind_values[self._note_kind.get()],
            text,
            self._note_started_at or datetime.now(UTC),
        )
        self._set_status(result.summary)
        if not result.succeeded:
            messagebox.showerror("Note not saved", result.summary, parent=self._root)
            return "break"
        self._clear_note()
        self._note_kind.set(ANNOTATION_KIND_LABELS["lab_note"])
        self._annotations_changed()
        return "break"

    def _annotations_changed(self) -> None:
        self._show_recent_notes()
        for trend in tuple(self._trend_windows.values()):
            trend.refresh()
        if self._dashboard_window is not None:
            self._dashboard_window.refresh()

    def _show_recent_notes(self) -> None:
        notes = tuple(getattr(self._view_model, "annotations", ()))
        if notes == self._displayed_annotations:
            return
        self._displayed_annotations = notes
        if not notes:
            self._recent_notes.configure(
                text="Notes: Ctrl+N to type one, or double-click a graph."
            )
            return
        latest = sorted(notes, key=lambda note: note.created_at)[-3:]
        self._recent_notes.configure(text="\n".join(
            f"{note.timestamp.astimezone():%H:%M} · {note.kind_label} · "
            + (note.text if len(note.text) <= 48 else note.text[:47] + "…").replace("\n", " ")
            for note in reversed(latest)
        ))

    def _open_recipe_builder(self) -> None:
        """Open one non-modal editor; execution itself stays off the Tk thread."""
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
        self._recipe_window = tk.Toplevel(self._root)
        RecipeBuilderWindow(
            self._recipe_window,
            self._view_model.recipe_runner,
            self._view_model.profile,
            default_output_directory=self._entries["output"].get(),
            current_unit=self._view_model.display_units.current,
        )

        def close() -> None:
            if self._recipe_window is not None:
                self._recipe_window.destroy()
                self._recipe_window = None
        self._recipe_window.protocol("WM_DELETE_WINDOW", close)

    def _create_channel_tree(self, parent: ttk.Frame, system: str | None) -> ttk.Treeview:
        tree = ttk.Treeview(
            parent,
            columns=("device", "channel", "access", "value", "unit", "quality", "time"),
            show="headings", height=14, style="Blueprint.Treeview",
        )
        for column, heading, width, anchor in (
            ("device", "DEVICE", 165, "w"),
            ("channel", "CHANNEL", 180, "w"),
            ("access", "ACCESS", 70, "w"),
            ("value", "VALUE", 105, "e"),
            ("unit", "UNIT", 75, "w"),
            ("quality", "QUALITY", 75, "w"),
            ("time", "LAST UPDATED", 165, "w"),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor=anchor)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.bind(
            "<Double-1>",
            lambda event, selected=system: self._handle_channel_double_click(event, selected),
        )
        tree.tag_configure("stale", foreground=ERROR_TEXT)
        return tree

    def _connect_all(self) -> None:
        if self._connection_in_progress:
            return
        self._connection_in_progress = True
        self._connect_button.configure(state="disabled")
        Thread(
            target=lambda: self._connection_results.put(
                self._view_model.connect_all()
            ),
            name="connect-configured-devices",
            daemon=True,
        ).start()

    def _finish_connect_all(
        self,
        results: tuple[OperationActionResult, ...],
    ) -> None:
        self._connection_in_progress = False
        self._connect_button.configure(state="normal")
        failures = [result.summary for result in results if not result.succeeded]
        if failures:
            messagebox.showerror(
                "Device connection problems", "\n".join(failures), parent=self._root
            )
        else:
            messagebox.showinfo(
                "Devices connected",
                "All configured devices connected successfully."
                + ("\nMonitoring is running." if self._view_model.is_monitoring else ""),
                parent=self._root,
            )
        self._update_display()

    def _show_monitoring_state(self, monitoring: bool) -> None:
        if monitoring:
            self._monitor_status.configure(
                text="● MONITORING", foreground=SUCCESS_TEXT, background=SUCCESS_BACKGROUND,
            )
        else:
            self._monitor_status.configure(
                text="● NOT MONITORING — readings are not updating",
                foreground=WARNING_TEXT, background=WARNING_BACKGROUND,
            )

    def _toggle_monitoring(self) -> None:
        action = (
            self._view_model.stop_monitoring
            if self._view_model.is_monitoring
            else self._view_model.start_monitoring
        )
        self._run_operation_action("Updating monitoring", action)

    def _toggle_recording(self) -> None:
        if self._recording_finalising:
            self._set_status(
                "Still writing the last recording's CSV/Excel exports — please wait."
            )
            return
        if self._view_model.is_recording:
            self._stop_recording()
            return
        experiment_id = self._entries["experiment_id"].get().strip()
        operator = self._entries["operator"].get().strip()
        output_directory = self._entries["output"].get().strip()
        notes = self._entries["notes"].get().strip()
        self._run_operation_action(
            "Starting recording",
            lambda: self._view_model.start_recording(
                experiment_id=experiment_id,
                operator=operator,
                output_directory=output_directory,
                notes=notes,
            ),
        )

    def _stop_recording(self) -> None:
        """Stop without locking the window while long-run exports are written.

        The recorder detaches acquisition immediately; only the final CSV and
        Excel files are still being produced, so other controls stay usable.
        """
        self._recording_finalising = True
        self._set_status(
            "Recording stopped — writing final CSV/Excel exports "
            "(can take a few minutes for long runs)…"
        )
        self._update_display()

        def complete(value: object, error: Exception | None) -> None:
            self._recording_finalising = False
            self._show_outcome("Stopping recording", value, error)
            self._update_display()
            if getattr(self, "_review_export_directory", None) is not None:
                self._refresh_excel_after_review()
            else:
                self._resume_pending_close()

        self._run_in_background(self._view_model.stop_recording, complete)
        self._open_run_review()

    def _open_run_review(self, directory: object = None) -> None:
        """Offer the optional review; recording has already stopped by now."""
        try:
            context = self._view_model.run_review_context(directory)
        except Exception as error:
            if directory is not None:
                messagebox.showerror("Run review", str(error), parent=self._root)
            return

        def done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            overall_qc, end_notes = result
            outcome = self._view_model.save_run_review(
                context.directory, overall_qc, end_notes
            )
            self._show_result(outcome)
            if outcome.succeeded:
                self._review_export_directory = context.directory
                self._refresh_excel_after_review()

        RunReviewDialog(self._root, context, on_done=done)

    def _refresh_excel_after_review(self) -> None:
        """Rewrite only the workbook; the review does not change the CSV."""
        directory = self._review_export_directory
        if directory is None or self._recording_finalising:
            return  # Picked up when the final exports finish.
        self._review_export_directory = None
        self._recording_finalising = True
        self._set_status("Adding run review to Excel…")
        self._update_display()

        def complete(value: object, error: Exception | None) -> None:
            self._recording_finalising = False
            self._show_outcome("Adding run review to Excel", value, error)
            self._update_display()
            self._resume_pending_close()

        self._run_in_background(
            lambda: self._view_model.export_excel(directory), complete
        )

    def _review_past_run(self) -> None:
        if self._view_model.is_recording:
            self._set_status("Stop recording first; the review opens when you do.")
            return
        selected = filedialog.askdirectory(
            parent=self._root,
            title="Choose an experiment folder to review",
            initialdir=str(self._view_model.experiment_directory
                           or self._entries["output"].get() or "."),
        )
        if selected:
            self._open_run_review(selected)

    def _export_excel(self) -> None:
        if self._action_in_progress:
            return
        directory = None
        if not self._view_model.is_recording:
            selected = filedialog.askdirectory(
                parent=self._root,
                title="Choose an experiment folder to export",
                initialdir=str(self._view_model.experiment_directory
                               or self._entries["output"].get() or "."),
            )
            if not selected:
                return
            directory = selected
        self._run_operation_action(
            "Exporting Excel", lambda: self._view_model.export_excel(directory)
        )

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(parent=self._root)
        if selected:
            self._entries["output"].delete(0, "end")
            self._entries["output"].insert(0, selected)

    def _open_live_dashboard(self) -> None:
        if self._dashboard_window is not None and self._dashboard_window.exists:
            self._dashboard_window.focus()
            return
        self._dashboard_window = LiveDashboardWindow(
            self._root,
            signal_provider=self._dashboard_signals,
            history_provider=self._view_model.measurement_history_for_period,
            on_close=lambda: setattr(self, "_dashboard_window", None),
            annotations=self._annotation_hub,
        )

    def _dashboard_signals(self) -> tuple[DashboardSignal, ...]:
        signals = []
        rows = {row.key: row for row in self._view_model.channel_rows()}
        for reading in self._view_model.measurement_rows():
            if reading.channel in {"output_enabled", "regulation_mode"}:
                continue  # These states are shown in the table and exports.
            row = rows.get((reading.device_id, reading.channel))
            label = (
                f"{row.device_name} — {row.channel_name}"
                if row is not None
                else f"{reading.device_id} — {reading.channel}"
            )
            channel = reading.channel.casefold()
            unit = reading.unit.casefold()
            if "temp" in channel or unit in {"degc", "°c", "k"}:
                group = "Temperatures"
            elif "flow" in channel or unit in {"sccm", "slpm", "lpm"}:
                group = "Flows"
            elif "humid" in channel or "%rh" in unit:
                group = "Humidity"
            elif "pressure" in channel or unit in {"pa", "kpa", "bar", "psia"}:
                group = "Pressure"
            elif channel in {"voltage", "current", "power"} or unit in {
                "v",
                "a",
                "w",
            }:
                group = "Electrical"
            else:
                group = "All"
            signals.append(
                DashboardSignal(
                    reading.device_id,
                    reading.channel,
                    label,
                    reading.unit,
                    group,
                    quantity=row.channel_name if row is not None else "",
                    is_setpoint="setpoint" in channel or "limit" in channel,
                )
            )
        return tuple(sorted(signals, key=lambda signal: signal.label.casefold()))

    def _handle_channel_double_click(self, event: tk.Event, system: str | None) -> str:
        tree = self._channel_trees[system]
        column = tree.identify_column(event.x)
        item_id = tree.identify_row(event.y)
        key = self._tree_item_keys.get((system, item_id))
        row = self._channel_rows.get(key) if key is not None else None
        if row is not None and getattr(row, "editor", None) == "action":
            self._begin_cell_edit(event, system)
            return "break"
        if column == "#4" and row is not None and row.writable:
            self._begin_cell_edit(event, system)
            return "break"
        if column in {"#1", "#2"} or (
            column == "#4" and row is not None and not row.writable
        ):
            self._open_selected_trend(event, system)
        return "break"

    def _open_selected_trend(self, event: tk.Event, system: str | None = None) -> None:
        tree = self._channel_trees.get(system, self._measurements)
        item_id = tree.identify_row(event.y)
        key = self._tree_item_keys.get((system, item_id), self._measurement_keys.get(item_id))
        if key is None:
            return
        if not self._view_model.measurement_history(*key):
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
                self._view_model.measurement_history_for_period(
                    selected_device,
                    selected_channel,
                    seconds,
                )
            ),
            on_close=lambda selected_key=key: self._trend_windows.pop(
                selected_key,
                None,
            ),
            history_limit=self._view_model.history_limit,
            annotations=self._annotation_hub,
        )

    def _begin_cell_edit(self, event: tk.Event, system: str | None) -> None:
        tree = self._channel_trees[system]
        item_id = tree.identify_row(event.y)
        key = self._tree_item_keys.get((system, item_id))
        row = self._channel_rows.get(key) if key is not None else None
        if row is None or not row.writable:
            return
        if row.editor == "action":
            if row.channel == "resume_pressure_control" and not messagebox.askyesno(
                "Resume BPR regulation", "Release the outlet valve hold and resume regulation at the existing pressure setpoint?\n\nThis may discharge gas toward the GC.", parent=self._root):
                return
            self._run_operation_action(
                f"Updating {row.channel_name}",
                lambda: self._view_model.apply_channel_value(
                    row.device_id, row.channel, True
                ),
            )
            return
        if tree.identify_column(event.x) != "#4":
            self._cancel_cell_edit()
            return
        box = tree.bbox(item_id, "value")
        if not box:
            return
        self._cancel_cell_edit()
        x, y, width, height = box
        if row.editor in {"boolean", "mode", "direction", "running"}:
            choices = (
                ("On", "Off") if row.editor in {"boolean", "running"}
                else ("constant_voltage", "constant_current") if row.editor == "mode"
                else ("forward", "reverse")
            )
            editor = ttk.Combobox(tree, values=choices, state="readonly")
            if row.value is None:
                pass  # State not yet known - leave blank rather than guessing.
            elif row.editor in {"boolean", "running"}:
                editor.set("On" if row.value else "Off")
            else:
                editor.set(str(row.value))
            editor.place(x=x, y=y, width=width, height=height)
            editor.bind(
                "<<ComboboxSelected>>",
                lambda _event, selected=row, widget=editor:
                self._commit_cell_edit(selected, widget),
            )
            editor.bind("<Escape>", lambda _event: self._cancel_cell_edit())
            editor.focus_set()
            self._editor = editor
            self._editor_apply = None
            return
        else:
            editor = ttk.Entry(tree, font=("Consolas", 9))
            editor.insert(0, "" if row.value is None else format(float(row.value), ".6g"))
            editor.selection_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.bind("<Escape>", lambda _event: self._cancel_cell_edit())
        editor.bind("<Return>", lambda _event, selected=row, widget=editor:
                    self._commit_cell_edit(selected, widget))
        editor.focus_set()
        self._editor = editor
        self._editor_apply = None

    def _commit_cell_edit(self, row: OperationChannelRow, editor: tk.Widget) -> str:
        text = str(editor.get()).strip()  # type: ignore[attr-defined]
        try:
            if row.editor in {"boolean", "running"}:
                value: float | bool | str = text == "On"
                if value:
                    title, prompt = (
                        ("Confirm hotplate heating",
                         f"Start heating on {row.device_name!r}?\n\n"
                         "Confirm the vessel and target temperature are safe.")
                        if row.channel == "heating_enabled" else
                        ("Confirm hotplate stirring",
                         f"Start stirring on {row.device_name!r}?")
                        if row.channel == "stirring_enabled" else
                        ("Confirm output enable",
                         f"Enable output for {row.device_name!r}?\n\n"
                         "Confirm wiring and limits are safe.")
                        if row.editor == "boolean" else
                        ("Confirm pump start", f"Start pump {row.device_name!r}?")
                    )
                    if not messagebox.askyesno(title, prompt, parent=self._root):
                        self._cancel_cell_edit()
                        return "break"
            elif row.editor in {"mode", "direction"}:
                value = text
            else:
                value = float(text)
                if row.minimum is not None and value < row.minimum:
                    raise ValueError(f"Value must be at least {row.minimum:g} {row.unit}")
                if row.maximum is not None and value > row.maximum:
                    raise ValueError(f"Value must not exceed {row.maximum:g} {row.unit}")
        except ValueError as error:
            messagebox.showerror("Invalid value", str(error), parent=self._root)
            return "break"
        if (
            row.channel == "current_limit"
            and isinstance(value, float)
            and value > DEFAULT_WIRING_CURRENT_CEILING_AMPS
            and self._view_model.manual_control.high_current_mode
            and not messagebox.askyesno(
                "Exceeding normal wiring rating",
                f"{value:g} A exceeds the normal "
                f"{DEFAULT_WIRING_CURRENT_CEILING_AMPS:g} A wiring rating "
                "for this rig.\n\nOnly continue if the cables in use are "
                "actually rated for this current.\n\nContinue?",
                icon="warning",
                parent=self._root,
            )
        ):
            self._cancel_cell_edit()
            return "break"
        self._cancel_cell_edit()
        self._run_operation_action(
            f"Updating {row.channel_name}",
            lambda: self._view_model.apply_channel_value(
                row.device_id, row.channel, value
            ),
        )
        return "break"

    def _cancel_cell_edit(self) -> str:
        for widget in (self._editor, self._editor_apply):
            if widget is not None:
                widget.destroy()
        self._editor = None
        self._editor_apply = None
        return "break"

    def _enter_global_safe_state(self) -> None:
        self._run_operation_action(
            "Entering global safe state",
            lambda: self._as_operation_result(
                self._view_model.manual_control.enter_global_safe_state()
            ),
        )

    @staticmethod
    def _as_operation_result(result: Any) -> OperationActionResult:
        return OperationActionResult(
            result.succeeded,
            result.summary,
            result.technical_details,
        )

    def _run_operation_action(
        self,
        description: str,
        action: Callable[[], OperationActionResult],
    ) -> None:
        if self._action_in_progress:
            self._set_status(
                f"Still busy: {self._action_description or 'previous action'}… "
                "— please wait for it to finish."
            )
            return
        self._action_in_progress = True
        self._action_description = description
        self._set_status(f"{description}…")

        def complete(value: object, error: Exception | None) -> None:
            self._action_in_progress = False
            self._action_description = None
            self._show_outcome(description, value, error)
            self._update_display()
            self._resume_pending_close()

        self._run_in_background(action, complete)

    def _show_outcome(
        self, description: str, value: object, error: Exception | None
    ) -> None:
        if error is not None:
            self._show_result(
                OperationActionResult(
                    False,
                    f"{description} failed: {type(error).__name__}: {error}",
                    format_exc(),
                )
            )
        else:
            assert isinstance(value, OperationActionResult)
            self._show_result(value)

    def _set_status(self, text: str) -> None:
        if hasattr(self, "_action_status"):
            self._action_status.configure(text=text)

    def _resume_pending_close(self) -> None:
        on_closed = self._pending_close
        if on_closed is not None:
            self._pending_close = None
            self.request_close(on_closed)

    def _run_in_background(
        self,
        action: Callable[[], object],
        on_complete: Callable[[object, Exception | None], None],
    ) -> None:
        def worker() -> None:
            try:
                value: object = action()
            except Exception as error:
                self._background_results.put(
                    lambda caught=error: on_complete(None, caught)
                )
            else:
                self._background_results.put(
                    lambda: on_complete(value, None)
                )

        Thread(target=worker, name="operation-action", daemon=True).start()

    def request_close(self, on_closed: Callable[[tuple[str, ...]], None]) -> None:
        """Stop feature services off the Tk thread before closing this window."""
        force = False
        if self._action_in_progress or self._recording_finalising:
            busy = (
                "the final recording exports are written"
                if self._recording_finalising
                else f"{self._action_description or 'the current action'} finishes"
            )
            # A second close request while waiting offers a way out of
            # anything that has got stuck.
            if self._pending_close is None or not messagebox.askyesno(
                "Close Operation anyway?",
                f"Operation is still waiting until {busy}.\n\n"
                "Closing anyway does not wait for it. Raw readings are already "
                "saved in the experiment's journal files; if the final CSV/Excel "
                "files end up missing or out of date, re-create them with "
                "Export Excel and choose that experiment folder.\n\n"
                "Close Operation anyway?",
                icon="warning",
                parent=self._root,
            ):
                # Close automatically once the work finishes, rather than refusing.
                self._pending_close = on_closed
                self._set_status(
                    f"Operation will close when {busy}… "
                    "(close again to force)"
                )
                return
            self._pending_close = None
            force = True
        if self._view_model.is_recording and not messagebox.askyesno(
            "Stop recording and close Operation",
            "Recording will stop and the final exports will be written before "
            "Operation closes. Continue?",
            parent=self._root,
        ):
            return
        self._action_in_progress = True
        self._action_description = "Stopping Operation"
        self._set_status("Stopping Operation…")

        def complete(value: object, error: Exception | None) -> None:
            self._action_in_progress = False
            self._action_description = None
            failures = (
                (f"Could not close Operation: {type(error).__name__}: {error}",)
                if error is not None
                else tuple(value)  # type: ignore[arg-type]
            )
            if failures:
                messagebox.showerror(
                    "Operation shutdown problems",
                    "\n".join(failures),
                    parent=self._root,
                )
            self.cancel_updates()
            on_closed(failures)

        self._run_in_background(
            lambda: self._view_model.shutdown(wait_for_exports=not force), complete
        )

    def _show_result(self, result: OperationActionResult) -> None:
        self._set_status(result.summary)
        if not result.succeeded:
            messagebox.showerror("Operation failed", result.summary, parent=self._root)

    def _selected_warning(self) -> str | None:
        selection = self._warnings.curselection()
        if not selection:
            return None
        return str(self._warnings.get(selection[0]))

    def _copy_warnings(self, _: tk.Event | None = None) -> str:
        warning = self._selected_warning()
        if warning is None:
            warning = "\n".join(
                str(self._warnings.get(index))
                for index in range(self._warnings.size())
            )
        if warning:
            self._root.clipboard_clear()
            self._root.clipboard_append(warning)
            self._root.update()
        return "break"

    def _show_selected_warning(self, _: tk.Event | None = None) -> str:
        warning = self._selected_warning()
        if warning is not None:
            messagebox.showwarning(
                "Persistent warning",
                warning,
                parent=self._root,
            )
        return "break"

    def _schedule_update(self) -> None:
        if getattr(self, "_updates_cancelled", False):
            return
        self._after_id = self._root.after(100, self._poll_ui_queue)

    def _poll_ui_queue(self) -> None:
        self._ui_tick_count += 1
        try:
            try:
                connection_results = self._connection_results.get_nowait()
            except Empty:
                pass
            else:
                self._finish_connect_all(connection_results)
            background_results = getattr(self, "_background_results", None)
            if background_results is not None:
                while True:
                    try:
                        completion = background_results.get_nowait()
                    except Empty:
                        break
                    completion()
            collected = self._view_model.collect_polling_results()
            if collected:
                self._update_display(refresh_trends=True)
        except Exception as error:
            self._ui_tick_failure_count += 1
            if not self._refresh_failed:
                self._record_refresh_event(
                    Event(
                        source="operation_ui",
                        severity=EventSeverity.ERROR,
                        message=(
                            "Operation display refresh failed: "
                            f"{type(error).__name__}: {error}"
                        ),
                    ),
                    format_exc(),
                )
            self._refresh_failed = True
        else:
            self._last_successful_ui_tick = datetime.now(UTC)
            if self._refresh_failed:
                self._record_refresh_event(
                    Event(
                        source="operation_ui",
                        message="Operation display refresh recovered.",
                    )
                )
            self._refresh_failed = False
        finally:
            self._schedule_update()

    def _record_refresh_event(
        self,
        event: Event,
        technical_details: str | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event, technical_details)
        except Exception:
            pass

    def _update_display(self, *, refresh_trends: bool = False) -> None:
        channel_rows = (
            self._view_model.channel_rows()
            if hasattr(self._view_model, "channel_rows")
            else self._view_model.measurement_rows()
        )
        self._channel_rows = {
            (row.device_id, row.channel): row for row in channel_rows
        }
        if not hasattr(self, "_tree_item_keys"):
            self._tree_item_keys = {}
        trees = getattr(self, "_channel_trees", {None: self._measurements})
        for system, tree in trees.items():
            current_keys: set[tuple[str, str]] = set()
            rows = channel_rows if system is None else self._view_model.channel_rows(system)
            for row in rows:
                key = (row.device_id, row.channel)
                current_keys.add(key)
                writable = getattr(row, "writable", False)
                channel_name = getattr(row, "channel_name", row.channel.replace("_", " ").title())
                value = row.value
                if isinstance(value, bool):
                    value_text = format_operation_boolean(row.channel, value)
                elif isinstance(value, str):
                    value_text = value.replace("_", " ").title()
                elif value is None:
                    value_text = "—"
                else:
                    value_text = format(value, ".6g")
                timestamp = getattr(row, "timestamp", None)
                values = (
                    getattr(row, "device_name", row.device_id),
                    ("■ " if writable else "") + channel_name,
                    "ACTION" if getattr(row, "editor", "") == "action"
                    else ("SET" if writable else "READ"),
                    value_text,
                    row.unit,
                    row.quality.capitalize() if row.quality else "—",
                    timestamp.astimezone().isoformat(timespec="seconds") if timestamp else "—",
                )
                scoped_key = key if system is None else (f"{system}:{key[0]}", key[1])
                item_id = self._measurement_items.get(scoped_key)
                if item_id is None:
                    item_id = f"measurement-{self._next_measurement_item}"
                    self._next_measurement_item += 1
                    self._measurement_items[scoped_key] = item_id
                    self._measurement_keys[item_id] = key
                    self._tree_item_keys[(system, item_id)] = key
                    self._measurement_values[scoped_key] = values
                    try:
                        tree.insert("", "end", iid=item_id, values=values,
                                    tags=("stale",) if row.quality and row.quality != "good" else ())
                    except TypeError:  # Lightweight test doubles.
                        tree.insert("", "end", iid=item_id, values=values)
                    self._measurement_rows_created += 1
                elif self._measurement_values.get(scoped_key) != values:
                    try:
                        tree.item(item_id, values=values,
                                  tags=("stale",) if row.quality and row.quality != "good" else ())
                    except TypeError:  # Lightweight test doubles.
                        tree.item(item_id, values=values)
                    self._measurement_values[scoped_key] = values
                    self._measurement_rows_updated += 1

            prefix = "" if system is None else f"{system}:"
            existing = {
                key for key in self._measurement_items
                if (system is None and not str(key[0]).startswith(tuple(f"{s}:" for s in trees if s)))
                or (system is not None and str(key[0]).startswith(prefix))
            }
            desired = current_keys if system is None else {(f"{system}:{key[0]}", key[1]) for key in current_keys}
            for scoped_key in existing - desired:
                item_id = self._measurement_items.pop(scoped_key)
                tree.delete(item_id)
                self._measurement_keys.pop(item_id, None)
                self._tree_item_keys.pop((system, item_id), None)
                self._measurement_values.pop(scoped_key, None)
                self._measurement_rows_deleted += 1

        warnings = tuple(self._view_model.warnings())
        if warnings != self._displayed_warnings:
            self._warnings.delete(0, "end")
            for device_id, warning in warnings:
                self._warnings.insert("end", f"{device_id}: {warning}")
            self._displayed_warnings = warnings
            self._warning_display_rebuilds += 1

        monitoring = self._view_model.is_monitoring
        recording = self._view_model.is_recording
        self._monitor_button.configure(
            text="Stop monitoring" if monitoring else "Start monitoring"
        )
        self._show_monitoring_state(monitoring)
        finalising = getattr(self, "_recording_finalising", False)
        self._record_button.configure(
            text="Stop recording" if recording
            else "Writing exports…" if finalising
            else "Start recording",
            state="disabled" if finalising else "normal",
        )
        self._recording_status.configure(
            text="RECORDING" if recording
            else "Writing final exports…" if finalising
            else "Not recording"
        )
        if hasattr(self, "_note_entry"):
            note_state = "normal" if recording else "disabled"
            self._note_entry.configure(state=note_state)
            self._note_button.configure(state=note_state)
            self._note_kind.configure(state="readonly" if recording else "disabled")
            self._review_button.configure(state="disabled" if recording else "normal")
            self._show_recent_notes()
        if refresh_trends:
            for trend in tuple(self._trend_windows.values()):
                trend.refresh()
            if self._dashboard_window is not None:
                self._dashboard_window.refresh()

    def diagnostic_metrics(self) -> dict[str, object]:
        metrics = dict(self._view_model.diagnostic_metrics())
        dashboard = self._dashboard_window
        metrics.update(
            {
                "ui_tick_count": self._ui_tick_count,
                "ui_tick_failure_count": self._ui_tick_failure_count,
                "last_successful_ui_tick": (
                    self._last_successful_ui_tick.isoformat()
                    if self._last_successful_ui_tick is not None
                    else None
                ),
                "measurement_rows_created": self._measurement_rows_created,
                "measurement_rows_updated": self._measurement_rows_updated,
                "measurement_rows_deleted": self._measurement_rows_deleted,
                "warning_display_rebuilds": self._warning_display_rebuilds,
                "dashboard": (
                    dashboard.diagnostic_metrics()
                    if dashboard is not None and dashboard.exists
                    else {
                        "active_quadrants": 0,
                        "selected_traces": 0,
                        "visible_traces": 0,
                        "canvas_items": 0,
                    }
                ),
            }
        )
        return metrics

    def cancel_updates(self) -> None:
        self._updates_cancelled = True
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None
        for trend in tuple(self._trend_windows.values()):
            trend.close()
        if self._dashboard_window is not None:
            self._dashboard_window.close()
