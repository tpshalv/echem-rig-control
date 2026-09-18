import tkinter as tk
from pathlib import Path
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from rig_control.ui.common.theme import SECTION_FONT, TITLE_FONT
from rig_control.ui.common.widgets import HoverToolTip, VerticalScrolledFrame
from rig_control.ui.home.model import HomeActionResult, HomeViewModel


class SettingsWindow:
    """Editor for application-wide preferences and their settings file."""

    _HIGH_CURRENT_MODE_KEY = "power_supply_high_current_mode"
    _CEILING_KEY = "power_supply_wiring_current_ceiling_amps"

    # Ordered (category label, setting keys) - one section per category,
    # picked from the sidebar. Add a new category here, or add a key to an
    # existing one, to grow the settings screen without needing to touch
    # its layout code.
    _CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("General", ("publish_interval_seconds",)),
        (
            "File save locations",
            (
                "default_output_directory",
                "export_bin_seconds",
                "live_export_interval_seconds",
                "technical_log_path",
            ),
        ),
        ("RE72 controllers", ()),
        ("Graphical", ("trend_history_readings",)),
        (
            "Power supply safety",
            (
                _HIGH_CURRENT_MODE_KEY,
                _CEILING_KEY,
                "power_supply_default_current_amps",
                "power_supply_default_voltage_volts",
            ),
        ),
    )

    def __init__(
        self,
        root: tk.Toplevel,
        view_model: HomeViewModel,
        *,
        on_change: callable | None = None,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._on_change = on_change
        self._entries: dict[str, ttk.Entry] = {}
        self._category_frames: dict[str, ttk.Frame] = {}
        self._high_current_var = tk.BooleanVar(value=False)
        self._re72_entries: dict[str, ttk.Entry | ttk.Combobox] = {}
        self._re72_rows: dict[str, object] = {}
        self._re72_original_values: dict[str, str] = {}
        self._re72_device_var = tk.StringVar()
        self._tooltips: list[HoverToolTip] = []
        self._autotune_monitoring = False
        self._autotune_seen_active = False
        self._autotune_poll_count = 0
        root.title("Application Settings")
        root.geometry("820x560")
        root.minsize(700, 420)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self._create_widgets()
        self.refresh()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=16)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        ttk.Label(main, text="Application settings", font=TITLE_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        file_frame = ttk.LabelFrame(main, text="Settings file", padding=10)
        file_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        file_frame.columnconfigure(0, weight=1)
        self._path_label = ttk.Label(file_frame, wraplength=570)
        self._path_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(file_frame, text="Choose…", command=self._choose_file).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(file_frame, text="Save as…", command=self._save_as).grid(
            row=0, column=2
        )

        body = ttk.Frame(main)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Treeview(
            body,
            columns=(),
            show="tree",
            selectmode="browse",
            height=len(self._CATEGORIES),
        )
        sidebar.column("#0", width=170, stretch=False)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        for category, _keys in self._CATEGORIES:
            sidebar.insert("", "end", iid=category, text=category)
        sidebar.bind("<<TreeviewSelect>>", self._on_category_selected)
        self._sidebar = sidebar

        detail_scroll = VerticalScrolledFrame(body)
        self._detail_scroll = detail_scroll
        detail_scroll.grid(row=0, column=1, sticky="nsew")
        detail_scroll.content.columnconfigure(0, weight=1)

        rows = self._view_model.setting_rows()
        rows_by_key = {row.key: row for row in rows}
        for category, keys in self._CATEGORIES:
            frame = ttk.Frame(detail_scroll.content, padding=(4, 0))
            frame.grid(row=0, column=0, sticky="new")
            frame.columnconfigure(1, weight=1)
            self._category_frames[category] = frame
            if category == "RE72 controllers":
                self._build_re72_category(frame)
            else:
                self._build_category(frame, [rows_by_key[key] for key in keys])

        sidebar.selection_set(self._CATEGORIES[0][0])
        # selection_set() does not itself fire <<TreeviewSelect>>, so the
        # initial show/hide has to be forced explicitly.
        self._on_category_selected(None)

        buttons = ttk.Frame(main)
        buttons.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        ttk.Button(buttons, text="Apply", command=self._apply).grid(row=0, column=0)
        ttk.Button(buttons, text="Save", command=self._save).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(buttons, text="Close", command=self._root.destroy).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._status = ttk.Label(main, font=SECTION_FONT)
        self._status.grid(row=4, column=0, sticky="w", pady=(10, 0))

    def _build_re72_category(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Lumel RE72 controller", font=SECTION_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        chooser = ttk.Frame(frame)
        chooser.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(chooser, text="Controller:").grid(row=0, column=0, padx=(0, 6))
        self._re72_devices = ttk.Combobox(
            chooser, textvariable=self._re72_device_var, state="readonly", width=24
        )
        self._re72_devices.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(chooser, text="Refresh from controller", command=self._refresh_re72).grid(
            row=0, column=2
        )
        self._autotune_button = ttk.Button(
            chooser,
            text="Start autotune...",
            command=self._start_autotune,
        )
        self._autotune_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Button(
            chooser,
            text="Save full snapshot...",
            command=self._save_re72_snapshot,
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Button(
            chooser,
            text="Load/restore snapshot...",
            command=self._restore_re72_snapshot,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(
            frame,
            text=("Values come from the physical controller. Edit fields and use Apply; "
                  "hover over a label or control for a detailed explanation."),
            wraplength=560,
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))

        self._re72_runtime = ttk.Label(
            frame,
            text="Control state: refresh to read",
            font=SECTION_FONT,
        )
        self._re72_runtime.grid(row=3, column=0, sticky="w", pady=(0, 8))

        tabs = ttk.Notebook(frame)
        self._re72_tabs = tabs
        tabs.grid(row=4, column=0, sticky="nsew")
        # ttk.Notebook also changes tabs on wheel input. Redirect those events
        # to the containing page so an incidental hover cannot switch between
        # Basic and Advanced.
        tabs.bind("<MouseWheel>", self._redirect_re72_wheel)
        tabs.bind("<Button-4>", self._redirect_re72_wheel)
        tabs.bind("<Button-5>", self._redirect_re72_wheel)
        tabs.bind("<<NotebookTabChanged>>", self._resize_re72_tab)
        self._re72_basic = ttk.Frame(tabs, padding=8)
        self._re72_advanced = ttk.Frame(tabs, padding=8)
        tabs.add(self._re72_basic, text="Basic")
        tabs.add(self._re72_advanced, text="Advanced")
        for page in (self._re72_basic, self._re72_advanced):
            page.columnconfigure(1, weight=0)
            page.columnconfigure(2, weight=1)

        # Build every control immediately. A hardware refresh fills the values;
        # failure to connect must not leave an apparently empty settings page.
        self._populate_re72_rows(self._view_model.empty_re72_setting_rows())
        self._root.after_idle(self._resize_re72_tab)

        ttk.Label(
            frame,
            text="Edit one or more fields, then apply and verify the changed values.",
        ).grid(row=5, column=0, sticky="w", pady=(8, 4))
        ttk.Button(frame, text="Apply RE72 changes", command=self._apply_re72).grid(
            row=6, column=0, sticky="w"
        )
        self._re72_selected_key: str | None = None

    def _populate_re72_rows(self, rows: tuple) -> None:
        if not self._re72_entries:
            counters = {"Basic": 0, "Advanced": 0}
            pages = {"Basic": self._re72_basic, "Advanced": self._re72_advanced}
            for row in rows:
                definition = row.definition
                parent = pages[definition.group]
                index = counters[definition.group]
                counters[definition.group] += 1
                label = ttk.Label(parent, text=f"{definition.label}:")
                label.grid(row=index, column=0, sticky="w", pady=1)
                if definition.choices:
                    entry = ttk.Combobox(
                        parent,
                        values=tuple(label for _value, label in definition.choices),
                        state="readonly",
                        width=25,
                    )
                    # A wheel event over a closed ttk Combobox normally cycles
                    # its selection. That is hazardous in this scrolling form:
                    # merely passing over a PID option could change it.
                    entry.bind("<MouseWheel>", self._redirect_re72_wheel)
                    entry.bind("<Button-4>", self._redirect_re72_wheel)
                    entry.bind("<Button-5>", self._redirect_re72_wheel)
                else:
                    entry = ttk.Entry(parent, width=16)
                entry.grid(row=index, column=1, sticky="w", padx=8, pady=1)
                if not definition.writable:
                    entry.configure(state="disabled")
                else:
                    entry.bind(
                        "<FocusIn>",
                        lambda _event, key=definition.key: setattr(
                            self, "_re72_selected_key", key
                        ),
                    )
                    if definition.choices:
                        entry.bind(
                            "<<ComboboxSelected>>",
                            lambda _event, key=definition.key: setattr(
                                self, "_re72_selected_key", key
                            ),
                        )
                if definition.description:
                    self._tooltips.extend(
                        (
                            HoverToolTip(label, definition.description),
                            HoverToolTip(entry, definition.description),
                        )
                    )
                self._re72_entries[definition.key] = entry
                self._re72_rows[definition.key] = row
        for row in rows:
            entry = self._re72_entries[row.definition.key]
            state = str(entry.cget("state"))
            if state in {"readonly", "disabled"}:
                entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, row.value)
            if not row.definition.writable:
                entry.configure(state="disabled")
            elif row.definition.choices:
                entry.configure(state="readonly")
            self._re72_rows[row.definition.key] = row
            self._re72_original_values[row.definition.key] = row.value

    def _redirect_re72_wheel(self, event: tk.Event) -> str:
        self._detail_scroll.scroll_from_event(event)
        return "break"

    def _resize_re72_tab(self, _event: tk.Event | None = None) -> None:
        """Size the notebook for its visible page, not its longest page."""
        selected = self._re72_tabs.select()
        if not selected:
            return
        self._root.update_idletasks()
        page = self._root.nametowidget(selected)
        self._re72_tabs.configure(height=page.winfo_reqheight())

    def _refresh_re72(self, *, preserve_status: bool = False) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        try:
            rows = self._view_model.read_re72_settings(device_id)
            self._populate_re72_rows(rows)
            runtime = self._view_model.read_re72_runtime_state(device_id)
        except Exception as error:
            messagebox.showerror("RE72 settings", str(error), parent=self._root)
            self._status.configure(text=f"Could not read {device_id}: {error}")
            return
        self._re72_runtime.configure(
            text=(
                f"Control state: {runtime['mode']}; active PID set "
                f"{runtime['active_pid_set']}; active setpoint "
                f"{runtime['active_setpoint']:g}; autotune "
                f"{'running' if runtime['autotune_active'] else 'not running'}"
            )
        )
        if not preserve_status:
            self._status.configure(text=f"Read {len(rows)} settings from {device_id}.")

    def _start_autotune(self) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        if not self._apply_re72():
            return
        confirmed = messagebox.askyesno(
            "Start RE72 autotune",
            (
                "Autotune deliberately cycles the heater and can cause temperature "
                "overshoot. Confirm the process is attended, the setpoint and "
                "autotune limits are safe, and OUT2 is connected to the intended SSR.\n\n"
                "Start autotune now?"
            ),
            icon="warning",
            parent=self._root,
        )
        if not confirmed:
            return
        result = self._view_model.start_re72_autotune(device_id)
        self._show_result(result)
        if not result.succeeded:
            return
        self._autotune_monitoring = True
        self._autotune_seen_active = False
        self._autotune_poll_count = 0
        self._autotune_button.configure(state="disabled")
        self._root.after(1000, self._poll_autotune)

    def _save_re72_snapshot(self) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        stamp = datetime.now().strftime("%Y%m%d")
        stem = self._view_model.re72_snapshot_default_stem(device_id)
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            title="Save complete RE72 settings snapshot",
            initialfile=f"{stem}_{stamp}.re72.json",
            defaultextension=".json",
            filetypes=(("RE72 snapshot", "*.re72.json"), ("JSON files", "*.json")),
        )
        if not selected:
            return
        result = self._view_model.save_re72_snapshot(device_id, selected)
        self._show_result(result)

    def _restore_re72_snapshot(self) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        selected = filedialog.askopenfilename(
            parent=self._root,
            title="Load complete RE72 settings snapshot",
            filetypes=(("RE72 snapshot", "*.re72.json"), ("JSON files", "*.json")),
        )
        if not selected:
            return
        confirmed = messagebox.askyesno(
            "Restore RE72 settings",
            (
                "This will replace the physical controller's documented writable "
                "configuration with values from the snapshot. Live status, hardware "
                "identity and RS-485 communication settings will not be written.\n\n"
                "Make sure heater operation is safe before continuing. Restore now?"
            ),
            icon="warning",
            parent=self._root,
        )
        if not confirmed:
            return
        result = self._view_model.restore_re72_snapshot(device_id, selected)
        self._show_result(result)
        if result.succeeded:
            self._refresh_re72(preserve_status=True)

    def _poll_autotune(self) -> None:
        if not self._autotune_monitoring or not self._root.winfo_exists():
            return
        try:
            state = self._view_model.read_re72_runtime_state(
                self._re72_device_var.get()
            )
        except Exception as error:
            self._status.configure(text=f"Could not monitor autotune: {error}")
            self._root.after(3000, self._poll_autotune)
            return
        active = bool(state["autotune_active"])
        self._autotune_poll_count += 1
        self._autotune_seen_active = self._autotune_seen_active or active
        self._re72_runtime.configure(
            text=(
                f"Control state: {state['mode']}; active PID set "
                f"{state['active_pid_set']}; active setpoint "
                f"{state['active_setpoint']:g}; autotune "
                f"{'running' if active else 'not running'}"
            )
        )
        if state["autotune_failed"]:
            self._finish_autotune("Autotune failed; check the controller error display.")
        elif self._autotune_seen_active and not active:
            self._finish_autotune("Autotune finished; PID values refreshed.")
        elif self._autotune_poll_count >= 5 and not self._autotune_seen_active:
            self._finish_autotune(
                "The controller did not report that autotune started."
            )
        else:
            self._root.after(2000, self._poll_autotune)

    def _finish_autotune(self, message: str) -> None:
        self._autotune_monitoring = False
        self._autotune_button.configure(state="normal")
        self._refresh_re72(preserve_status=True)
        self._status.configure(text=message)

    def _apply_re72(self) -> bool:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return False
        changes = {
            key: entry.get()
            for key, entry in self._re72_entries.items()
            if self._re72_rows[key].definition.writable
            and entry.get() != self._re72_original_values.get(key, "")
        }
        if not changes:
            self._status.configure(text="No RE72 settings have changed.")
            return True
        result = self._view_model.write_re72_settings(
            device_id,
            changes,
        )
        self._show_result(result)
        if result.succeeded:
            self._refresh_re72(preserve_status=True)
        return result.succeeded

    def _build_category(self, frame: ttk.Frame, rows: list) -> None:
        index = 0
        for row in rows:
            if row.key == self._HIGH_CURRENT_MODE_KEY:
                ttk.Checkbutton(
                    frame,
                    text=row.label,
                    variable=self._high_current_var,
                    command=self._on_high_current_mode_toggled,
                ).grid(row=index, column=0, columnspan=2, sticky="w", pady=4)
                index += 1
                ttk.Label(frame, text=row.description, wraplength=480).grid(
                    row=index, column=0, columnspan=2, sticky="w", pady=(0, 4)
                )
                index += 1
            elif row.key == self._CEILING_KEY:
                self._ceiling_frame = ttk.Frame(frame)
                self._ceiling_frame.columnconfigure(1, weight=1)
                self._add_setting_row(self._ceiling_frame, 0, row)
                self._ceiling_row_index = index
                index += 1
            else:
                self._add_setting_row(frame, index, row)
                index += 1

    def _add_setting_row(self, parent: ttk.Frame, index: int, row) -> None:
        ttk.Label(parent, text=f"{row.label}:").grid(
            row=index, column=0, sticky="w", pady=4
        )
        entry = ttk.Entry(parent, width=48)
        entry.grid(row=index, column=1, sticky="ew", padx=8, pady=4)
        self._entries[row.key] = entry
        if row.key == "default_output_directory":
            ttk.Button(
                parent,
                text="Browse…",
                command=lambda field=entry: self._browse_directory(field),
            ).grid(row=index, column=2, pady=4)
        elif row.key == "technical_log_path":
            ttk.Button(
                parent,
                text="Browse…",
                command=lambda field=entry: self._browse_log_file(field),
            ).grid(row=index, column=2, pady=4)
        else:
            ttk.Label(parent, text=row.description, wraplength=250).grid(
                row=index, column=2, sticky="w", pady=4
            )

    def refresh(self) -> None:
        self._path_label.configure(
            text=f"{self._view_model.settings.friendly_name} — {self._view_model.settings_path}"
        )
        for row in self._view_model.setting_rows():
            entry = self._entries.get(row.key)
            if entry is None:
                continue
            entry.delete(0, "end")
            entry.insert(0, row.value)
        self._high_current_var.set(
            self._view_model.settings.power_supply_high_current_mode
        )
        self._update_ceiling_visibility()
        device_ids = self._view_model.re72_device_ids()
        self._re72_devices.configure(values=device_ids)
        if device_ids and self._re72_device_var.get() not in device_ids:
            self._re72_device_var.set(device_ids[0])

    def _on_category_selected(self, _event: tk.Event) -> None:
        selection = self._sidebar.selection()
        if not selection:
            return
        selected = selection[0]
        for category, frame in self._category_frames.items():
            if category == selected:
                frame.grid(row=0, column=0, sticky="new")
            else:
                frame.grid_remove()

    def _on_high_current_mode_toggled(self) -> None:
        if self._high_current_var.get():
            confirmed = messagebox.askyesno(
                title="Enable High current mode",
                message=(
                    "This allows the manual power-supply current ceiling to "
                    "be raised above the normal 45 A wiring rating.\n\n"
                    "Only enable this if the cables in use are actually "
                    "rated for it.\n\nEnable High current mode?"
                ),
                icon="warning",
                parent=self._root,
            )
            if not confirmed:
                self._high_current_var.set(False)
        self._update_ceiling_visibility()

    def _update_ceiling_visibility(self) -> None:
        if self._high_current_var.get():
            self._ceiling_frame.grid(
                row=self._ceiling_row_index, column=0, columnspan=2, sticky="ew"
            )
        else:
            self._ceiling_frame.grid_remove()

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
        )
        if selected:
            self._show_result(self._view_model.select_settings(selected))
            self.refresh()

    def _browse_directory(self, entry: ttk.Entry) -> None:
        selected = filedialog.askdirectory(parent=self._root)
        if selected:
            entry.delete(0, "end")
            entry.insert(0, selected)

    def _browse_log_file(self, entry: ttk.Entry) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            initialfile="rig-control.log",
            defaultextension=".log",
            filetypes=(("Log files", "*.log"), ("All files", "*.*")),
        )
        if selected:
            entry.delete(0, "end")
            entry.insert(0, selected)

    def _apply(self) -> bool:
        if self._sidebar.selection() == ("RE72 controllers",):
            return self._apply_re72()
        values = {key: entry.get() for key, entry in self._entries.items()}
        values[self._HIGH_CURRENT_MODE_KEY] = (
            "true" if self._high_current_var.get() else "false"
        )
        result = self._view_model.apply_setting_text(values)
        self._show_result(result)
        if result.succeeded and self._on_change is not None:
            self._on_change()
        self.refresh()
        return result.succeeded

    def _save(self) -> None:
        if self._sidebar.selection() == ("RE72 controllers",):
            self._apply_re72()
            return
        if self._apply():
            self._show_result(self._view_model.save_settings())
            self.refresh()

    def _save_as(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            defaultextension=".toml",
            filetypes=(("TOML files", "*.toml"),),
        )
        if selected and self._apply():
            self._show_result(self._view_model.save_settings(Path(selected)))
            self.refresh()

    def _show_result(self, result: HomeActionResult) -> None:
        self._status.configure(text=result.summary)
        if not result.succeeded:
            messagebox.showerror("Settings", result.summary, parent=self._root)
