import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import messagebox, ttk

from rig_control.device_factory import create_device_manager
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.control.service import RigControlService
from rig_control.ui.manual_control_model import (
    ManualActionResult,
    ManualControlViewModel,
)
from rig_control.devices.power_supply import (
    PowerSupplyOperatingMode,
)


class ManualControlWindow:
    """Manual commissioning controls backed by the control service."""

    STATUS_COLOURS = {
        "ready": "#16823B",
        "connected": "#16823B",
        "degraded": "#B26A00",
        "connecting": "#1769AA",
        "faulted": "#C62828",
        "disconnected": "#555555",
        "unknown": "#777777",
    }

    def __init__(
        self,
        root: tk.Tk,
        view_model: ManualControlViewModel,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._history: list[str] = []
        self._technical_details: list[str] = []
        self._first_entry: ttk.Entry | None = None
        self._seen_event_count = 0
        self._seen_failure_count = 0

        self._root.title(
            "Echem Rig Control - Simulated Manual Control"
        )
        self._root.geometry("1000x760")
        self._root.minsize(820, 600)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

        self._create_widgets()
        self.refresh()

        if self._first_entry is not None:
            self._first_entry.focus_set()
            self._first_entry.selection_range(0, "end")

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        main.rowconfigure(4, weight=1)

        simulation_label = tk.Label(
            main,
            text="SIMULATION MODE - NO PHYSICAL HARDWARE",
            foreground="#7A3E00",
            background="#FFF4CE",
            font=("Segoe UI", 11, "bold"),
            padx=10,
            pady=7,
        )
        simulation_label.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 10),
        )

        heading_row = ttk.Frame(main)
        heading_row.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(0, 8),
        )
        heading_row.columnconfigure(0, weight=1)

        ttk.Label(
            heading_row,
            text="Manual control",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self._mode_label = ttk.Label(
            heading_row,
            text="",
            font=("Segoe UI", 10, "bold"),
        )
        self._mode_label.grid(row=0, column=1, sticky="e")

        self._notebook = ttk.Notebook(main)
        self._notebook.grid(
            row=2,
            column=0,
            sticky="nsew",
        )

        self._mfc_tab = ttk.Frame(
            self._notebook,
            padding=10,
        )
        self._supply_tab = ttk.Frame(
            self._notebook,
            padding=10,
        )

        self._notebook.add(
            self._mfc_tab,
            text="Mass flow controllers",
        )
        self._notebook.add(
            self._supply_tab,
            text="Power supplies",
        )

        self._mfc_tab.columnconfigure(0, weight=1)
        self._supply_tab.columnconfigure(0, weight=1)

        action_row = ttk.Frame(main)
        action_row.grid(
            row=3,
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
            background="#B71C1C",
            activeforeground="white",
            activebackground="#8E0000",
            font=("Segoe UI", 10, "bold"),
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

        log_frame = ttk.Frame(main)
        log_frame.grid(
            row=4,
            column=0,
            sticky="nsew",
        )
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self._log = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            font=("Consolas", 9),
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
        self._rebuild_mfc_controls()
        self._rebuild_power_supply_controls()
        self._collect_model_events()

    def _rebuild_mfc_controls(self) -> None:
        for child in self._mfc_tab.winfo_children():
            child.destroy()

        rows = self._view_model.mfc_rows()

        if not rows:
            ttk.Label(
                self._mfc_tab,
                text="No mass flow controllers are configured.",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, row in enumerate(rows):
            frame = ttk.LabelFrame(
                self._mfc_tab,
                text=row.device_id,
                padding=10,
            )
            frame.grid(
                row=index,
                column=0,
                sticky="ew",
                pady=(0, 10),
            )
            frame.columnconfigure(1, weight=1)

            self._create_status_indicator(
                frame,
                status=row.status,
                row=0,
                column=0,
                columnspan=4,
            )

            headings = ttk.Frame(frame)
            headings.grid(
                row=1,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(10, 2),
            )
            headings.columnconfigure(1, weight=1)
            headings.columnconfigure(2, weight=1)

            ttk.Label(
                headings,
                text="Measurement",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=0, sticky="w")

            ttk.Label(
                headings,
                text="Set",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=1, sticky="w", padx=(18, 0))

            ttk.Label(
                headings,
                text="Actual",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=2, sticky="w", padx=(18, 0))

            values = ttk.Frame(frame)
            values.grid(
                row=2,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 10),
            )
            values.columnconfigure(1, weight=1)
            values.columnconfigure(2, weight=1)

            ttk.Label(
                values,
                text="Flow",
            ).grid(row=0, column=0, sticky="w")

            ttk.Label(
                values,
                text=(
                    f"{self._format_number(row.flow_setpoint)} "
                    f"{row.flow_unit}"
                ),
            ).grid(row=0, column=1, sticky="w", padx=(18, 0))

            ttk.Label(
                values,
                text=self._format_measurement(
                    row.measured_flow,
                    row.flow_unit,
                ),
                font=("Segoe UI", 10, "bold"),
            ).grid(row=0, column=2, sticky="w", padx=(18, 0))

            detail_text = self._measurement_detail(
                row.measurement_quality,
                row.measurement_time,
            )

            ttk.Label(
                frame,
                text=detail_text,
                foreground="#555555",
            ).grid(
                row=3,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(0, 10),
            )

            ttk.Separator(
                frame,
                orient="horizontal",
            ).grid(
                row=4,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 10),
            )

            ttk.Label(
                frame,
                text=f"New flow ({row.flow_unit}):",
            ).grid(row=5, column=0, sticky="w")

            entry = ttk.Entry(frame, width=14)
            entry.insert(0, str(row.flow_setpoint))
            entry.grid(
                row=5,
                column=1,
                sticky="w",
                padx=8,
            )

            if self._first_entry is None and row.is_available:
                self._first_entry = entry

            set_flow = (
                lambda device_id=row.device_id,
                selected_entry=entry: self._set_mfc_flow(
                    device_id,
                    selected_entry,
                )
            )

            self._bind_entry_submission(
                entry,
                set_flow,
            )

            set_button = ttk.Button(
                frame,
                text="Set flow",
                command=set_flow,
            )
            set_button.grid(
                row=5,
                column=2,
                padx=(0, 8),
            )

            if not row.is_available:
                entry.configure(state="disabled")
                set_button.configure(state="disabled")

    def _rebuild_power_supply_controls(self) -> None:
        for child in self._supply_tab.winfo_children():
            child.destroy()

        rows = self._view_model.power_supply_rows()

        if not rows:
            ttk.Label(
                self._supply_tab,
                text="No power supplies are configured.",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, row in enumerate(rows):
            frame = ttk.LabelFrame(
                self._supply_tab,
                text=row.device_id,
                padding=10,
            )
            frame.grid(
                row=index,
                column=0,
                sticky="ew",
                pady=(0, 10),
            )
            frame.columnconfigure(1, weight=1)

            status_row = ttk.Frame(frame)
            status_row.grid(
                row=0,
                column=0,
                columnspan=4,
                sticky="ew",
            )
            status_row.columnconfigure(0, weight=1)

            self._create_status_indicator(
                status_row,
                status=row.status,
                row=0,
                column=0,
            )

            self._create_output_indicator(
                status_row,
                is_available=row.is_available,
                output_enabled=row.output_enabled,
            )

            mode_frame = ttk.Frame(status_row)
            mode_frame.grid(
                row=1,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(10, 0),
            )

            ttk.Label(
                mode_frame,
                text="Intended control mode:",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=0, sticky="w")

            mode_selector = ttk.Combobox(
                mode_frame,
                values=(
                    "Constant current",
                    "Constant voltage",
                ),
                width=20,
                state="readonly",
            )
            mode_selector.set(
                self._operating_mode_label(
                    row.operating_mode
                )
            )
            mode_selector.grid(
                row=0,
                column=1,
                sticky="w",
                padx=(8, 0),
            )

            mode_selector.bind(
                "<<ComboboxSelected>>",
                lambda _event,
                device_id=row.device_id,
                selected_selector=mode_selector,
                previous_mode=row.operating_mode,
                output_enabled=row.output_enabled,
                maximum_voltage=row.maximum_voltage,
                maximum_current=row.maximum_current:
                self._request_supply_mode_change(
                    device_id=device_id,
                    selector=selected_selector,
                    previous_mode=previous_mode,
                    output_enabled=output_enabled,
                    maximum_voltage=maximum_voltage,
                    maximum_current=maximum_current,
                ),
            )

            ttk.Label(
                frame,
                text=(
                    f"Configured limits: "
                    f"{row.maximum_voltage} V, "
                    f"{row.maximum_current} A, "
                    f"{row.maximum_power} W"
                ),
                foreground="#555555",
            ).grid(
                row=1,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(8, 10),
            )

            constant_current = (
                row.operating_mode
                is PowerSupplyOperatingMode.CONSTANT_CURRENT
            )

            if constant_current:
                displayed_measurements = (
                    (
                        "Current",
                        (
                            f"{self._format_number(row.current_setting)} "
                            "A (setpoint)"
                        ),
                        self._format_measurement(
                            row.measured_current,
                            "A",
                        ),
                    ),
                    (
                        "Voltage",
                        (
                            f"{self._format_number(row.voltage_setpoint)} "
                            "V (limit)"
                        ),
                        self._format_measurement(
                            row.measured_voltage,
                            "V",
                        ),
                    ),
                )

                setting_controls = (
                    (
                        "current",
                        "Current setpoint (A):",
                        row.current_setting,
                    ),
                    (
                        "voltage",
                        "Voltage limit (V):",
                        row.voltage_setpoint,
                    ),
                )
            else:
                displayed_measurements = (
                    (
                        "Voltage",
                        (
                            f"{self._format_number(row.voltage_setpoint)} "
                            "V (setpoint)"
                        ),
                        self._format_measurement(
                            row.measured_voltage,
                            "V",
                        ),
                    ),
                    (
                        "Current",
                        (
                            f"{self._format_number(row.current_setting)} "
                            "A (limit)"
                        ),
                        self._format_measurement(
                            row.measured_current,
                            "A",
                        ),
                    ),
                )

                setting_controls = (
                    (
                        "voltage",
                        "Voltage setpoint (V):",
                        row.voltage_setpoint,
                    ),
                    (
                        "current",
                        "Current limit (A):",
                        row.current_setting,
                    ),
                )

            values = ttk.Frame(frame)
            values.grid(
                row=2,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 8),
            )
            values.columnconfigure(1, weight=1)
            values.columnconfigure(2, weight=1)

            ttk.Label(
                values,
                text="Measurement",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=0, sticky="w")

            ttk.Label(
                values,
                text="Set",
                font=("Segoe UI", 9, "bold"),
            ).grid(
                row=0,
                column=1,
                sticky="w",
                padx=(18, 0),
            )

            ttk.Label(
                values,
                text="Actual",
                font=("Segoe UI", 9, "bold"),
            ).grid(
                row=0,
                column=2,
                sticky="w",
                padx=(18, 0),
            )

            for value_row, (
                measurement_name,
                set_text,
                actual_text,
            ) in enumerate(
                displayed_measurements,
                start=1,
            ):
                ttk.Label(
                    values,
                    text=measurement_name,
                ).grid(
                    row=value_row,
                    column=0,
                    sticky="w",
                    pady=(5, 0),
                )

                ttk.Label(
                    values,
                    text=set_text,
                ).grid(
                    row=value_row,
                    column=1,
                    sticky="w",
                    padx=(18, 0),
                    pady=(5, 0),
                )

                ttk.Label(
                    values,
                    text=actual_text,
                    font=("Segoe UI", 10, "bold"),
                ).grid(
                    row=value_row,
                    column=2,
                    sticky="w",
                    padx=(18, 0),
                    pady=(5, 0),
                )

            ttk.Label(
                values,
                text="Power",
            ).grid(
                row=3,
                column=0,
                sticky="w",
                pady=(5, 0),
            )

            ttk.Label(
                values,
                text="—",
            ).grid(
                row=3,
                column=1,
                sticky="w",
                padx=(18, 0),
                pady=(5, 0),
            )

            ttk.Label(
                values,
                text=self._format_measured_power(
                    row.measured_voltage,
                    row.measured_current,
                ),
                font=("Segoe UI", 10, "bold"),
            ).grid(
                row=3,
                column=2,
                sticky="w",
                padx=(18, 0),
                pady=(5, 0),
            )

            voltage_detail = self._measurement_detail(
                row.voltage_quality,
                row.voltage_measurement_time,
            )
            current_detail = self._measurement_detail(
                row.current_quality,
                row.current_measurement_time,
            )

            ttk.Label(
                frame,
                text=(
                    f"Voltage: {voltage_detail}    "
                    f"Current: {current_detail}"
                ),
                foreground="#555555",
            ).grid(
                row=3,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(0, 10),
            )

            ttk.Separator(
                frame,
                orient="horizontal",
            ).grid(
                row=4,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 10),
            )

            normal_control_widgets: list[tk.Widget] = []

            for control_index, (
                control_kind,
                control_label,
                control_value,
            ) in enumerate(
                setting_controls,
                start=5,
            ):
                top_padding = (
                    (8, 0)
                    if control_index == 6
                    else (0, 0)
                )

                ttk.Label(
                    frame,
                    text=control_label,
                ).grid(
                    row=control_index,
                    column=0,
                    sticky="w",
                    pady=top_padding,
                )

                entry = ttk.Entry(
                    frame,
                    width=14,
                )
                entry.insert(
                    0,
                    str(control_value),
                )
                entry.grid(
                    row=control_index,
                    column=1,
                    sticky="w",
                    padx=8,
                    pady=top_padding,
                )

                if (
                    self._first_entry is None
                    and row.is_available
                ):
                    self._first_entry = entry

                if control_kind == "voltage":
                    action = (
                        lambda device_id=row.device_id,
                        selected_entry=entry:
                        self._set_supply_voltage(
                            device_id,
                            selected_entry,
                        )
                    )
                    button_text = "Set voltage"
                else:
                    action = (
                        lambda device_id=row.device_id,
                        selected_entry=entry:
                        self._set_supply_current(
                            device_id,
                            selected_entry,
                        )
                    )
                    button_text = "Set current"

                self._bind_entry_submission(
                    entry,
                    action,
                )

                button = ttk.Button(
                    frame,
                    text=button_text,
                    command=action,
                )
                button.grid(
                    row=control_index,
                    column=2,
                    sticky="w",
                    pady=top_padding,
                )

                normal_control_widgets.extend(
                    [
                        entry,
                        button,
                    ]
                )

            output_buttons = ttk.Frame(frame)
            output_buttons.grid(
                row=7,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(12, 0),
            )

            enable_button = ttk.Button(
                output_buttons,
                text="Enable output...",
                command=lambda device_id=row.device_id:
                self._enable_supply_output(device_id),
            )
            enable_button.grid(
                row=0,
                column=0,
                padx=(0, 8),
            )

            disable_button = ttk.Button(
                output_buttons,
                text="Disable output",
                command=lambda device_id=row.device_id:
                self._set_supply_output(
                    device_id,
                    False,
                ),
            )
            disable_button.grid(
                row=0,
                column=1,
                padx=(0, 8),
            )

            normal_control_widgets.extend(
                [
                    enable_button,
                    disable_button,
                ]
            )

            if not row.is_available:
                mode_selector.configure(state="disabled")

                for widget in normal_control_widgets:
                    widget.configure(state="disabled")

    def _create_status_indicator(
        self,
        parent: tk.Misc,
        *,
        status: str,
        row: int,
        column: int,
        columnspan: int = 1,
    ) -> None:
        colour = self.STATUS_COLOURS.get(
            status,
            self.STATUS_COLOURS["unknown"],
        )

        indicator = tk.Label(
            parent,
            text=f"●  {status.replace('_', ' ').title()}",
            foreground=colour,
            font=("Segoe UI", 10, "bold"),
        )
        indicator.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="w",
        )

    def _create_output_indicator(
        self,
        parent: tk.Misc,
        *,
        is_available: bool,
        output_enabled: bool,
    ) -> None:
        if not is_available:
            text = "●  OUTPUT STATE UNKNOWN"
            foreground = "#B26A00"
            background = "#FFF4CE"
        elif output_enabled:
            text = "●  OUTPUT ENABLED"
            foreground = "#0B6E2F"
            background = "#DFF6E5"
        else:
            text = "●  Output disabled"
            foreground = "#555555"
            background = "#EEEEEE"

        indicator = tk.Label(
            parent,
            text=text,
            foreground=foreground,
            background=background,
            font=("Segoe UI", 10, "bold"),
            padx=9,
            pady=4,
        )
        indicator.grid(
            row=0,
            column=1,
            sticky="e",
        )

    @staticmethod
    def _operating_mode_label(
        mode: PowerSupplyOperatingMode,
    ) -> str:
        if mode is PowerSupplyOperatingMode.CONSTANT_CURRENT:
            return "Constant current"

        return "Constant voltage"

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

    def _bind_entry_submission(
        self,
        entry: ttk.Entry,
        action: Callable[[], None],
    ) -> None:
        """Run the entry's Set action when either Enter key is pressed."""

        entry.bind(
            "<Return>",
            lambda _event: action(),
        )
        entry.bind(
            "<KP_Enter>",
            lambda _event: action(),
        )

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

    @staticmethod
    def _format_number(value: float) -> str:
        return format(value, ".6g")

    @classmethod
    def _format_measurement(
        cls,
        value: float | None,
        unit: str,
    ) -> str:
        if value is None:
            return "—"

        return f"{cls._format_number(value)} {unit}"

    @classmethod
    def _format_measured_power(
        cls,
        voltage: float | None,
        current: float | None,
    ) -> str:
        if voltage is None or current is None:
            return "—"

        return f"{cls._format_number(voltage * current)} W"

    @staticmethod
    def _measurement_detail(
        quality: str | None,
        timestamp: datetime | None,
    ) -> str:
        if timestamp is None:
            return "No current measurement available"

        local_timestamp = timestamp.astimezone().isoformat(
            timespec="seconds"
        )
        displayed_quality = (
            quality.upper()
            if quality is not None
            else "UNKNOWN"
        )

        return (
            f"Quality: {displayed_quality}; "
            f"updated {local_timestamp}"
        )


def main() -> None:
    root = tk.Tk()
    profile = load_rig_profile(
        "rig-profile.simulation.toml"
    )
    manager = create_device_manager(profile)

    # This program is explicitly simulation-only. Simulated devices are
    # therefore connected automatically for manual-control testing.
    for device_id in manager.device_ids:
        manager.connect(device_id)

    service = RigControlService(manager)
    view_model = ManualControlViewModel(
        manager,
        service,
    )

    view_model.initialize_manual_power_supply_defaults()

    ManualControlWindow(root, view_model)
    root.mainloop()


if __name__ == "__main__":
    main()