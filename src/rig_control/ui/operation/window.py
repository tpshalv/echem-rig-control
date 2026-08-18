import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from rig_control.ui.common.theme import ERROR_TEXT, SECTION_FONT, TITLE_FONT
from rig_control.ui.operation.manual_control_panel import ManualControlPanel
from rig_control.ui.operation.model import OperationActionResult, OperationViewModel
from rig_control.ui.operation.trend_window import TrendWindow


class OperationWindow:
    """Live monitoring and experiment-recording window."""

    def __init__(
        self,
        root: tk.Tk,
        view_model: OperationViewModel,
        *,
        profile_name: str,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._after_id: str | None = None
        self._measurement_keys: dict[str, tuple[str, str]] = {}
        self._trend_windows: dict[tuple[str, str], TrendWindow] = {}
        self._root.title("Echem Rig Control — Operation")
        self._root.geometry("1050x760")
        self._root.minsize(850, 620)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

        self._create_widgets(profile_name)
        self._update_display()
        self._schedule_update()

    def _create_widgets(self, profile_name: str) -> None:
        main = ttk.Frame(self._root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        ttk.Label(main, text="Rig operation", font=TITLE_FONT).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(main, text=f"Profile: {profile_name}").grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )

        tabs = ttk.Notebook(main)
        tabs.grid(row=2, column=0, sticky="nsew")

        monitoring = ttk.Frame(tabs, padding=(0, 10, 0, 0))
        monitoring.columnconfigure(0, weight=1)
        monitoring.rowconfigure(1, weight=1)
        tabs.add(monitoring, text="Monitoring and recording")

        controls = ttk.Frame(monitoring)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(
            controls,
            text="Connect configured devices",
            command=self._connect_all,
        ).grid(row=0, column=0, padx=(0, 8))
        self._monitor_button = ttk.Button(
            controls,
            text="Start monitoring",
            command=self._toggle_monitoring,
        )
        self._monitor_button.grid(row=0, column=1)
        ttk.Label(controls, text="Trend history:").grid(
            row=0, column=2, padx=(20, 4)
        )
        self._history_limit = tk.StringVar(
            value=str(self._view_model.history_limit)
        )
        ttk.Spinbox(
            controls,
            from_=2,
            to=10000,
            width=7,
            textvariable=self._history_limit,
        ).grid(row=0, column=3)
        ttk.Label(controls, text="readings").grid(
            row=0, column=4, padx=(4, 4)
        )
        ttk.Button(
            controls,
            text="Apply",
            command=self._apply_history_limit,
        ).grid(row=0, column=5)

        body = ttk.PanedWindow(monitoring, orient="vertical")
        body.grid(row=1, column=0, sticky="nsew")

        live = ttk.LabelFrame(body, text="Live measurements", padding=8)
        live.columnconfigure(0, weight=1)
        live.rowconfigure(0, weight=1)
        self._measurements = ttk.Treeview(
            live,
            columns=("device", "channel", "value", "unit", "quality", "time"),
            show="headings",
            height=10,
        )
        for column, heading, width in (
            ("device", "Device", 170),
            ("channel", "Measurement", 170),
            ("value", "Value", 90),
            ("unit", "Unit", 90),
            ("quality", "Quality", 90),
            ("time", "Updated", 210),
        ):
            self._measurements.heading(column, text=heading)
            self._measurements.column(column, width=width, anchor="w")
        self._measurements.grid(row=0, column=0, sticky="nsew")
        measurement_scroll = ttk.Scrollbar(
            live, orient="vertical", command=self._measurements.yview
        )
        measurement_scroll.grid(row=0, column=1, sticky="ns")
        self._measurements.configure(yscrollcommand=measurement_scroll.set)
        self._measurements.bind("<Double-1>", self._open_selected_trend)

        lower = ttk.Frame(body)
        lower.columnconfigure(0, weight=1)
        lower.columnconfigure(1, weight=1)
        lower.rowconfigure(0, weight=1)
        body.add(live, weight=3)
        body.add(lower, weight=2)

        warnings_frame = ttk.LabelFrame(lower, text="Persistent warnings", padding=8)
        warnings_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        warnings_frame.columnconfigure(0, weight=1)
        warnings_frame.rowconfigure(0, weight=1)
        self._warnings = tk.Listbox(warnings_frame, foreground=ERROR_TEXT)
        self._warnings.grid(row=0, column=0, sticky="nsew")

        recording = ttk.LabelFrame(lower, text="Experiment recording", padding=8)
        recording.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        recording.columnconfigure(1, weight=1)
        fields = (
            ("Experiment ID", "experiment_id"),
            ("Operator", "operator"),
            ("Output folder", "output"),
            ("Sample interval (s)", "interval"),
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
        self._entries["interval"].insert(0, "1.0")
        ttk.Button(recording, text="Browse…", command=self._browse_output).grid(
            row=2, column=2, pady=2
        )

        actions = ttk.Frame(recording)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._record_button = ttk.Button(
            actions, text="Start recording", command=self._toggle_recording
        )
        self._record_button.grid(row=0, column=0)
        self._recording_status = ttk.Label(
            actions, text="Not recording", font=SECTION_FONT
        )
        self._recording_status.grid(row=0, column=1, padx=(12, 0))

        self._manual_panel = ManualControlPanel(
            tabs,
            self._view_model.manual_control,
        )
        tabs.add(self._manual_panel, text="Manual control")

    def _connect_all(self) -> None:
        results = self._view_model.connect_all()
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
            try:
                interval = float(self._entries["interval"].get().strip())
            except ValueError:
                messagebox.showerror(
                    "Invalid sample interval",
                    "Sample interval must be a number of seconds.",
                    parent=self._root,
                )
                return
            result = self._view_model.start_recording(
                experiment_id=self._entries["experiment_id"].get().strip(),
                operator=self._entries["operator"].get().strip(),
                output_directory=self._entries["output"].get().strip(),
                sample_interval_seconds=interval,
                notes=self._entries["notes"].get().strip(),
            )
        self._show_result(result)
        self._update_display()

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(parent=self._root)
        if selected:
            self._entries["output"].delete(0, "end")
            self._entries["output"].insert(0, selected)

    def _apply_history_limit(self) -> None:
        try:
            value = int(self._history_limit.get().strip())
            self._view_model.set_history_limit(value)
        except (TypeError, ValueError) as error:
            messagebox.showerror(
                "Invalid trend history",
                str(error),
                parent=self._root,
            )
            self._history_limit.set(str(self._view_model.history_limit))
            return
        self._history_limit.set(str(self._view_model.history_limit))

    def _open_selected_trend(self, event: tk.Event) -> None:
        item_id = self._measurements.identify_row(event.y)
        key = self._measurement_keys.get(item_id)
        if key is None:
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
                lambda selected_device=device_id, selected_channel=channel:
                self._view_model.measurement_history(
                    selected_device,
                    selected_channel,
                )
            ),
            on_close=lambda selected_key=key: self._trend_windows.pop(
                selected_key,
                None,
            ),
        )

    def _show_result(self, result: OperationActionResult) -> None:
        if not result.succeeded:
            messagebox.showerror("Operation failed", result.summary, parent=self._root)

    def _schedule_update(self) -> None:
        self._after_id = self._root.after(100, self._poll_ui_queue)

    def _poll_ui_queue(self) -> None:
        collected = self._view_model.collect_polling_results()
        self._update_display()
        if collected:
            self._manual_panel.refresh()
        self._schedule_update()

    def _update_display(self) -> None:
        self._measurements.delete(*self._measurements.get_children())
        self._measurement_keys.clear()
        for index, row in enumerate(self._view_model.measurement_rows()):
            item_id = f"measurement-{index}"
            self._measurement_keys[item_id] = (row.device_id, row.channel)
            self._measurements.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    row.device_id,
                    row.channel.replace("_", " ").title(),
                    format(row.value, ".6g"),
                    row.unit,
                    row.quality.upper(),
                    row.timestamp.astimezone().isoformat(timespec="seconds"),
                ),
            )
        self._warnings.delete(0, "end")
        for device_id, warning in self._view_model.warnings():
            self._warnings.insert("end", f"{device_id}: {warning}")

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
        for trend in tuple(self._trend_windows.values()):
            trend.refresh()

    def cancel_updates(self) -> None:
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None
        for trend in tuple(self._trend_windows.values()):
            trend.close()
