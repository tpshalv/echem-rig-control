import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from rig_control.ui.manual_control.model import (
    ManualControlViewModel,
)
from rig_control.ui.manual_control.mfc_panel import MfcPanel
from rig_control.ui.manual_control.power_supply_panel import (
    PowerSupplyPanel,
)
from rig_control.ui.manual_control.types import (
    ManualActionResult,
)
from rig_control.devices.power_supply import (
    PowerSupplyOperatingMode,
)

from rig_control.ui.common.theme import (
    DANGER_BUTTON_ACTIVE_BACKGROUND,
    DANGER_BUTTON_BACKGROUND,
    ERROR_TEXT,
    MONOSPACE_FONT,
    SECTION_FONT,
    TITLE_FONT,
)
from rig_control.ui.common.widgets import VerticalScrolledFrame

class ManualControlPanel(ttk.Frame):
    """Manual commissioning controls embedded in Operation."""

    def __init__(
        self,
        parent: tk.Misc,
        view_model: ManualControlViewModel,
    ) -> None:
        super().__init__(parent)
        self._root = self.winfo_toplevel()
        self._view_model = view_model
        self._history: list[str] = []
        self._technical_details: list[str] = []
        self._first_entry: ttk.Entry | None = None
        self._seen_event_count = 0
        self._seen_failure_count = 0

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._create_widgets()
        self.refresh()

        if self._first_entry is not None:
            self._first_entry.focus_set()
            self._first_entry.selection_range(0, "end")

    def _create_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        heading_row = ttk.Frame(main)
        heading_row.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 8),
        )
        heading_row.columnconfigure(0, weight=1)

        ttk.Label(
            heading_row,
            text="Manual control",
            font=TITLE_FONT
        ).grid(row=0, column=0, sticky="w")

        self._mode_label = ttk.Label(
            heading_row,
            text="",
            font=SECTION_FONT,
        )
        self._mode_label.grid(row=0, column=1, sticky="e")

        adjustable_area = ttk.PanedWindow(
            main,
            orient="vertical",
        )
        adjustable_area.grid(row=1, column=0, sticky="nsew")

        controls_area = ttk.Frame(adjustable_area)
        controls_area.columnconfigure(0, weight=1)
        controls_area.rowconfigure(0, weight=1)

        self._notebook = ttk.Notebook(controls_area)
        self._notebook.grid(row=0, column=0, sticky="nsew")

        self._mfc_scroll = VerticalScrolledFrame(self._notebook)
        self._mfc_panel = MfcPanel(
            self._mfc_scroll.content,
            self._view_model,
            self._set_mfc_flow,
        )
        self._mfc_panel.grid(row=0, column=0, sticky="nsew")

        self._supply_scroll = VerticalScrolledFrame(self._notebook)
        self._supply_panel = PowerSupplyPanel(
            self._supply_scroll.content,
            self._view_model,
            on_mode_change=self._request_supply_mode_change,
            on_set_voltage=self._set_supply_voltage,
            on_set_current=self._set_supply_current,
            on_enable_output=self._enable_supply_output,
            on_set_output=self._set_supply_output,
        )
        self._supply_panel.grid(row=0, column=0, sticky="nsew")

        self._controller_panel = ttk.Frame(self._notebook, padding=12)
        self._controller_panel.columnconfigure(0, weight=1)

        self._notebook.add(
            self._mfc_scroll,
            text="Mass flow controllers",
        )
        self._notebook.add(
            self._supply_scroll,
            text="Power supplies",
        )
        self._notebook.add(self._controller_panel, text="ESP32 controller")


        action_row = ttk.Frame(controls_area)
        action_row.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(10, 5),
        )

        ttk.Button(
            action_row,
            text="Refresh values",
            command=self.refresh,
        ).grid(row=0, column=0, padx=(0, 8))

        ttk.Button(
            action_row,
            text="Copy action log",
            command=self._copy_log,
        ).grid(row=0, column=1)

        action_row.columnconfigure(2, weight=1)

        global_safe_button = tk.Button(
            action_row,
            text="ENTER SAFE STATE — ALL DEVICES",
            command=self._enter_global_safe_state,
            foreground="white",
            background=DANGER_BUTTON_BACKGROUND,
            activeforeground="white",
            activebackground=DANGER_BUTTON_ACTIVE_BACKGROUND,
            font=SECTION_FONT,
            padx=12,
            pady=5,
            relief="raised",
            borderwidth=2,
        )
        global_safe_button.grid(
            row=0,
            column=3,
            sticky="e",
        )

        log_frame = ttk.Frame(adjustable_area)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        adjustable_area.add(controls_area, weight=3)
        adjustable_area.add(log_frame, weight=1)

        self._log = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            font=MONOSPACE_FONT,
            state="disabled",
        )
        self._log.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self._log.yview,
        )
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self._log.configure(
            yscrollcommand=scrollbar.set,
        )

    def refresh(self) -> None:
        self._mode_label.configure(
            text=f"Control mode: {self._view_model.control_mode}"
        )

        self._first_entry = None
        self._mfc_panel.refresh()
        self._first_entry = self._mfc_panel.first_entry
        self._supply_panel.refresh()
        self._refresh_controllers()
        if self._first_entry is None:
            self._first_entry = self._supply_panel.first_entry
        self._collect_model_events()

    def _refresh_controllers(self) -> None:
        for child in self._controller_panel.winfo_children():
            child.destroy()
        rows = self._view_model.controller_rows()
        if not rows:
            ttk.Label(self._controller_panel, text="No ESP32 controller is configured.").grid(row=0, column=0, sticky="w")
            return
        for index, row in enumerate(rows):
            frame = ttk.LabelFrame(self._controller_panel, text=row.device_id, padding=10)
            frame.grid(row=index, column=0, sticky="ew", pady=(0, 8))
            ttk.Label(frame, text=f"Connection: {row.status}").grid(row=0, column=0, sticky="w")
            ttk.Label(frame, text=f"Watchdog: {'TRIPPED' if row.watchdog_tripped else 'armed'}").grid(row=1, column=0, sticky="w")
            ttk.Label(frame, text=f"Safe state: {'active' if row.safe_state_active else 'inactive'}").grid(row=2, column=0, sticky="w")
            ttk.Label(frame, text=f"LED: {'ON' if row.led_enabled else 'OFF'}").grid(row=3, column=0, sticky="w")
            buttons = ttk.Frame(frame)
            buttons.grid(row=0, column=1, rowspan=4, padx=(20, 0))
            state = "normal" if row.is_available else "disabled"
            ttk.Button(buttons, text="Rearm watchdog", state=state, command=lambda device_id=row.device_id: self._rearm_controller(device_id)).grid(row=0, column=0, columnspan=2, pady=(0, 6))
            ttk.Button(buttons, text="LED ON", state=state, command=lambda device_id=row.device_id: self._set_controller_led(device_id, True)).grid(row=1, column=0, padx=(0, 6))
            ttk.Button(buttons, text="LED OFF", state=state, command=lambda device_id=row.device_id: self._set_controller_led(device_id, False)).grid(row=1, column=1)

    def _rearm_controller(self, device_id: str) -> None:
        self._record_result(self._view_model.rearm_controller(device_id))

    def _set_controller_led(self, device_id: str, enabled: bool) -> None:
        self._record_result(self._view_model.set_controller_output(device_id, "led", enabled))

    def _request_supply_mode_change(
        self,
        *,
        device_id: str,
        selector: ttk.Combobox,
        previous_mode: PowerSupplyOperatingMode,
        output_enabled: bool,
        maximum_voltage: float,
        maximum_current: float,
    ) -> None:
        selected_label = selector.get()

        if selected_label == "Constant current":
            requested_mode = (
                PowerSupplyOperatingMode.CONSTANT_CURRENT
            )
        else:
            requested_mode = (
                PowerSupplyOperatingMode.CONSTANT_VOLTAGE
            )

        if requested_mode is previous_mode:
            return

        if output_enabled:
            selector.set(
                self._operating_mode_label(previous_mode)
            )
            messagebox.showwarning(
                title="Disable output first",
                message=(
                    "The power-supply control mode cannot be "
                    "changed while its output is enabled.\n\n"
                    "Disable the output, then try again."
                ),
                parent=self._root,
            )
            return

        remembered_target = (
            self._view_model.power_supply_target(
                device_id,
                requested_mode,
            )
        )

        if requested_mode is (
            PowerSupplyOperatingMode.CONSTANT_CURRENT
        ):
            change_description = (
                f"Current setpoint: {remembered_target:g} A\n"
                f"Voltage limit: {maximum_voltage:g} V"
            )
        else:
            change_description = (
                f"Voltage setpoint: {remembered_target:g} V\n"
                f"Current limit: {maximum_current:g} A"
            )

        confirmed = messagebox.askyesno(
            title="Confirm power-supply mode change",
            message=(
                f"Switch {device_id!r} to "
                f"{selected_label.lower()} control?\n\n"
                f"{change_description}\n\n"
                "The output will remain disabled.\n\n"
                "Continue?"
            ),
            icon="warning",
            parent=self._root,
        )

        if not confirmed:
            selector.set(
                self._operating_mode_label(previous_mode)
            )
            return

        result = (
            self._view_model.set_power_supply_operating_mode(
                device_id,
                requested_mode,
            )
        )
        self._record_result(result)

    def _set_mfc_flow(
        self,
        device_id: str,
        entry: ttk.Entry,
    ) -> None:
        value = self._parse_number(
            entry,
            f"MFC {device_id!r} flow",
        )

        if value is None:
            return

        self._record_result(
            self._view_model.set_mfc_flow(
                device_id,
                value,
            )
        )

    def _set_supply_voltage(
        self,
        device_id: str,
        entry: ttk.Entry,
    ) -> None:
        value = self._parse_number(
            entry,
            f"Power supply {device_id!r} voltage",
        )

        if value is None:
            return

        self._record_result(
            self._view_model.set_power_supply_voltage(
                device_id,
                value,
            )
        )

    def _set_supply_current(
        self,
        device_id: str,
        entry: ttk.Entry,
    ) -> None:
        value = self._parse_number(
            entry,
            f"Power supply {device_id!r} current limit",
        )

        if value is None:
            return

        self._record_result(
            self._view_model.set_power_supply_current(
                device_id,
                value,
            )
        )

    def _enable_supply_output(
        self,
        device_id: str,
    ) -> None:
        confirmed = messagebox.askyesno(
            title="Confirm output enable",
            message=(
                f"Enable output for {device_id!r}?\n\n"
                "Confirm that voltage/current settings and "
                "physical connections are appropriate."
            ),
            parent=self._root,
        )

        if confirmed:
            self._set_supply_output(device_id, True)
        else:
            self._append_history(
                f"Output enable for {device_id!r} was cancelled."
            )

    def _set_supply_output(
        self,
        device_id: str,
        enabled: bool,
    ) -> None:
        self._record_result(
            self._view_model.set_power_supply_output(
                device_id,
                enabled,
            )
        )

    def _enter_global_safe_state(self) -> None:
        """Immediately request the safe state for the whole rig."""

        result = self._view_model.enter_global_safe_state()
        self._record_result(result)

        if result.succeeded:
            messagebox.showinfo(
                title="Global safe state",
                message=(
                    "All available safe-state operations succeeded."
                ),
                parent=self._root,
            )
        else:
            messagebox.showerror(
                title="Global safe-state problem",
                message=(
                    "One or more devices did not confirm safe state.\n\n"
                    "Other devices were still attempted. See the "
                    "action log for details."
                ),
                parent=self._root,
            )

    def _safe_state(self, device_id: str) -> None:
        self._record_result(
            self._view_model.enter_safe_state(device_id)
        )

    def _parse_number(
        self,
        entry: ttk.Entry,
        field_name: str,
    ) -> float | None:
        text = entry.get().strip()

        try:
            return float(text)
        except ValueError:
            self._append_history(
                f"{field_name} must be a valid number; "
                f"received {text!r}."
            )

            entry.focus_set()
            entry.selection_range(0, "end")
            entry.icursor("end")
            self._root.bell()

            return None

    def _record_result(
        self,
        result: ManualActionResult,
    ) -> None:
        self._append_history(result.summary)

        if result.technical_details:
            self._technical_details.append(
                result.technical_details
            )

        self.refresh()

    def _collect_model_events(self) -> None:
        failures = self._view_model.read_failures

        for failure in failures[self._seen_failure_count:]:
            self._technical_details.append(
                failure.technical_details
            )

        self._seen_failure_count = len(failures)

        events = self._view_model.events

        for event in events[self._seen_event_count:]:
            prefix = event.severity.value.upper()
            self._append_history(
                f"{prefix}: {event.message}"
            )

        self._seen_event_count = len(events)

    def _append_history(self, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        self._history.append(f"[{timestamp}] {message}")

        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.insert("1.0", "\n".join(self._history))
        self._log.see("end")
        self._log.configure(state="disabled")

    def _copy_log(self) -> None:
        sections = [
            "MANUAL CONTROL HISTORY\n"
            + (
                "\n".join(self._history)
                if self._history
                else "No actions recorded."
            )
        ]

        if self._technical_details:
            sections.append(
                "TECHNICAL ERROR DETAILS\n"
                + "\n\n".join(self._technical_details)
            )

        self._root.clipboard_clear()
        self._root.clipboard_append(
            "\n\n".join(sections)
        )
        self._root.update()
