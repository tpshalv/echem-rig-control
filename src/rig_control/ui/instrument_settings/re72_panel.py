"""RE72's instrument-specific settings editor, hosted by the Settings window."""

import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from rig_control.instrument_settings.re72 import Re72SettingsService
from rig_control.instrument_settings.common import InstrumentSettingResult
from rig_control.ui.common.theme import SECTION_FONT
from rig_control.ui.common.widgets import HoverToolTip
from rig_control.ui.instrument_settings.base import InstrumentSettingsPanel


class Re72SettingsPanel(InstrumentSettingsPanel):
    def __init__(
        self, parent: tk.Misc, service_provider: Callable[[], Re72SettingsService],
        scroll_from_event: Callable[[tk.Event], None],
    ) -> None:
        super().__init__(parent, padding=(4, 0))
        self._window = self.winfo_toplevel()
        self._service_provider = service_provider
        self._scroll_from_event = scroll_from_event
        self._re72_entries: dict[str, ttk.Entry | ttk.Combobox] = {}
        self._re72_rows: dict[str, object] = {}
        self._re72_original_values: dict[str, str] = {}
        self._re72_device_var = tk.StringVar(master=self)
        self._tooltips: list[HoverToolTip] = []
        self._autotune_monitoring = False
        self._autotune_seen_active = False
        self._autotune_poll_count = 0
        self._autotune_after_id: str | None = None
        self._build_re72_category(self)
        self._status = ttk.Label(self, wraplength=560)
        self._status.grid(row=7, column=0, sticky="w", pady=(8, 0))
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.refresh()

    @property
    def _service(self) -> Re72SettingsService:
        return self._service_provider()

    def refresh(self) -> None:
        device_ids = self._service.device_ids()
        self._re72_devices.configure(values=device_ids)
        if self._re72_device_var.get() not in device_ids:
            self._re72_device_var.set(device_ids[0] if device_ids else "")

    def apply_changes(self) -> bool:
        return self._apply_re72()

    def _show_result(self, result: InstrumentSettingResult) -> None:
        self._status.configure(text=result.summary)
        if not result.succeeded:
            messagebox.showerror("Instrument settings", result.summary, parent=self._window)

    def _schedule_autotune(self, delay: int, callback: Callable[[], None]) -> None:
        self._autotune_after_id = self.after(delay, callback)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self and self._autotune_after_id is not None:
            self.after_cancel(self._autotune_after_id)
            self._autotune_after_id = None

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
        self._populate_re72_rows(self._service.empty_setting_rows())
        self.after_idle(self._resize_re72_tab)

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
        self._scroll_from_event(event)
        return "break"

    def _resize_re72_tab(self, _event: tk.Event | None = None) -> None:
        """Size the notebook for its visible page, not its longest page."""
        selected = self._re72_tabs.select()
        if not selected:
            return
        self._window.update_idletasks()
        page = self._window.nametowidget(selected)
        self._re72_tabs.configure(height=page.winfo_reqheight())

    def _refresh_re72(self, *, preserve_status: bool = False) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        try:
            rows = self._service.read_settings(device_id)
            self._populate_re72_rows(rows)
            runtime = self._service.read_runtime_state(device_id)
        except Exception as error:
            messagebox.showerror("RE72 settings", str(error), parent=self._window)
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
            parent=self._window,
        )
        if not confirmed:
            return
        result = self._service.start_autotune(device_id)
        self._show_result(result)
        if not result.succeeded:
            return
        self._autotune_monitoring = True
        self._autotune_seen_active = False
        self._autotune_poll_count = 0
        self._autotune_button.configure(state="disabled")
        self._schedule_autotune(1000, self._poll_autotune)

    def _save_re72_snapshot(self) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        stamp = datetime.now().strftime("%Y%m%d")
        stem = self._service.snapshot_default_stem(device_id)
        selected = filedialog.asksaveasfilename(
            parent=self._window,
            title="Save complete RE72 settings snapshot",
            initialfile=f"{stem}_{stamp}.re72.json",
            defaultextension=".json",
            filetypes=(("RE72 snapshot", "*.re72.json"), ("JSON files", "*.json")),
        )
        if not selected:
            return
        result = self._service.save_snapshot(device_id, selected)
        self._show_result(result)

    def _restore_re72_snapshot(self) -> None:
        device_id = self._re72_device_var.get()
        if not device_id:
            self._status.configure(text="No RE72 controller is configured.")
            return
        selected = filedialog.askopenfilename(
            parent=self._window,
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
            parent=self._window,
        )
        if not confirmed:
            return
        result = self._service.restore_snapshot(device_id, selected)
        self._show_result(result)
        if result.succeeded:
            self._refresh_re72(preserve_status=True)

    def _poll_autotune(self) -> None:
        if not self._autotune_monitoring or not self.winfo_exists():
            return
        try:
            state = self._service.read_runtime_state(
                self._re72_device_var.get()
            )
        except Exception as error:
            self._status.configure(text=f"Could not monitor autotune: {error}")
            self._schedule_autotune(3000, self._poll_autotune)
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
            self._schedule_autotune(2000, self._poll_autotune)

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
        result = self._service.write_settings(
            device_id,
            changes,
        )
        self._show_result(result)
        if result.succeeded:
            self._refresh_re72(preserve_status=True)
        return result.succeeded
