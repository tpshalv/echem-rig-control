import tkinter as tk
from datetime import UTC, datetime
from tkinter import filedialog, messagebox, ttk
from queue import Empty, Queue
from threading import Thread
from traceback import format_exc

from rig_control.models import Event, EventSeverity, EventSink
from rig_control.ui.common.theme import ERROR_TEXT, SECTION_FONT, TITLE_FONT
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
        self._channel_trees: dict[str | None, ttk.Treeview] = {}
        self._tree_item_keys: dict[tuple[str | None, str], tuple[str, str]] = {}
        self._channel_rows: dict[tuple[str, str], OperationChannelRow] = {}
        self._editor: tk.Widget | None = None
        self._editor_apply: ttk.Button | None = None
        self._connection_results: Queue[tuple[OperationActionResult, ...]] = Queue()
        self._connection_in_progress = False
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
        controls.columnconfigure(2, weight=1)
        ttk.Label(
            controls,
            text=("■ SET: double-click VALUE, type, press Enter   •   "
                  "double-click DEVICE/CHANNEL for trend"),
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=2, padx=18)
        ttk.Button(controls, text="Enter safe state — all devices",
                   command=self._enter_global_safe_state).grid(row=0, column=3)
        ttk.Button(
            controls,
            text="Live dashboard",
            command=self._open_live_dashboard,
        ).grid(row=1, column=3, sticky="e", pady=(6, 0))
        self._action_status = ttk.Label(controls, text="Ready")
        self._action_status.grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
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
            ("Notes", "notes"),
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

        actions = ttk.Frame(recording)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._record_button = ttk.Button(
            actions, text="Start recording", command=self._toggle_recording
        )
        self._record_button.grid(row=0, column=0)
        self._recording_status = ttk.Label(
            actions, text="Not recording", font=SECTION_FONT
        )
        self._recording_status.grid(row=0, column=1, padx=(12, 0))
        ttk.Label(
            recording,
            text=("Every new reading is saved once. Measurement intervals "
                  "are configured per device in Device Setup."),
            wraplength=280,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

        for text, x, y, anchor in (("┌", 2, 2, "nw"), ("┐", 1, 2, "ne"),
                                    ("└", 2, 1, "sw"), ("┘", 1, 1, "se")):
            ttk.Label(main, text=text, style="Blueprint.TLabel").place(
                relx=x if x == 1 else 0, rely=y if y == 1 else 0,
                x=4 if x == 2 else -4, y=4 if y == 2 else -4, anchor=anchor)

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
                "All configured devices connected successfully.",
                parent=self._root,
            )
        self._update_display()

    def _toggle_monitoring(self) -> None:
        result = (
            self._view_model.stop_monitoring()
            if self._view_model.is_monitoring
            else self._view_model.start_monitoring()
        )
        self._show_result(result)
        self._update_display()

    def _toggle_recording(self) -> None:
        if self._view_model.is_recording:
            result = self._view_model.stop_recording()
        else:
            result = self._view_model.start_recording(
                experiment_id=self._entries["experiment_id"].get().strip(),
                operator=self._entries["operator"].get().strip(),
                output_directory=self._entries["output"].get().strip(),
                notes=self._entries["notes"].get().strip(),
            )
        self._show_result(result)
        self._update_display()

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
        )

    def _dashboard_signals(self) -> tuple[DashboardSignal, ...]:
        signals = []
        rows = {row.key: row for row in self._view_model.channel_rows()}
        for reading in self._view_model.measurement_rows():
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
                    "setpoint" in channel or "limit" in channel,
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
        )

    def _begin_cell_edit(self, event: tk.Event, system: str | None) -> None:
        tree = self._channel_trees[system]
        item_id = tree.identify_row(event.y)
        key = self._tree_item_keys.get((system, item_id))
        row = self._channel_rows.get(key) if key is not None else None
        if row is None or not row.writable:
            return
        if row.editor == "action":
            result = self._view_model.apply_channel_value(
                row.device_id, row.channel, True
            )
            self._show_result(result)
            self._update_display()
            return
        if tree.identify_column(event.x) != "#4":
            self._cancel_cell_edit()
            return
        box = tree.bbox(item_id, "value")
        if not box:
            return
        self._cancel_cell_edit()
        x, y, width, height = box
        if row.editor in {"boolean", "mode"}:
            choices = (
                ("On", "Off") if row.editor == "boolean"
                else ("constant_voltage", "constant_current")
            )
            editor = ttk.Combobox(tree, values=choices, state="readonly")
            if row.editor == "boolean":
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
            if row.editor == "boolean":
                value: float | bool | str = text == "On"
                if value and not messagebox.askyesno(
                    "Confirm output enable",
                    f"Enable output for {row.device_name!r}?\n\nConfirm wiring and limits are safe.",
                    parent=self._root,
                ):
                    self._cancel_cell_edit()
                    return "break"
            elif row.editor == "mode":
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
        result = self._view_model.apply_channel_value(row.device_id, row.channel, value)
        self._cancel_cell_edit()
        self._show_result(result)
        self._update_display()
        return "break"

    def _cancel_cell_edit(self) -> str:
        for widget in (self._editor, self._editor_apply):
            if widget is not None:
                widget.destroy()
        self._editor = None
        self._editor_apply = None
        return "break"

    def _enter_global_safe_state(self) -> None:
        result = self._view_model.manual_control.enter_global_safe_state()
        wrapped = OperationActionResult(result.succeeded, result.summary, result.technical_details)
        self._show_result(wrapped)
        self._update_display()

    def _show_result(self, result: OperationActionResult) -> None:
        if hasattr(self, "_action_status"):
            self._action_status.configure(text=result.summary)
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
        self._record_button.configure(
            text="Stop recording" if recording else "Start recording"
        )
        self._recording_status.configure(
            text="RECORDING" if recording else "Not recording"
        )
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
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None
        for trend in tuple(self._trend_windows.values()):
            trend.close()
        if self._dashboard_window is not None:
            self._dashboard_window.close()
