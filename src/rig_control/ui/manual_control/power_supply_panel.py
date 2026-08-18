import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import ttk

from rig_control.devices.power_supply import PowerSupplyOperatingMode
from rig_control.ui.common.theme import (
    BODY_BOLD_FONT,
    MUTED_TEXT,
    NEUTRAL_BACKGROUND,
    SECTION_FONT,
    SUCCESS_BACKGROUND,
    SUCCESS_TEXT,
    WARNING_BACKGROUND,
    WARNING_TEXT,
)
from rig_control.ui.common.widgets import create_device_status_indicator
from rig_control.ui.manual_control.model import ManualControlViewModel


class PowerSupplyPanel(ttk.Frame):
    """Manual controls for the configured power supplies."""

    def __init__(
        self,
        parent: tk.Misc,
        view_model: ManualControlViewModel,
        *,
        on_mode_change: Callable[..., None],
        on_set_voltage: Callable[[str, ttk.Entry], None],
        on_set_current: Callable[[str, ttk.Entry], None],
        on_enable_output: Callable[[str], None],
        on_set_output: Callable[[str, bool], None],
    ) -> None:
        super().__init__(parent, padding=10)
        self._view_model = view_model
        self._on_mode_change = on_mode_change
        self._on_set_voltage = on_set_voltage
        self._on_set_current = on_set_current
        self._on_enable_output = on_enable_output
        self._on_set_output = on_set_output
        self.first_entry: ttk.Entry | None = None
        self.columnconfigure(0, weight=1)

    def refresh(self) -> None:
        for child in self.winfo_children():
            child.destroy()

        self.first_entry = None
        rows = self._view_model.power_supply_rows()

        if not rows:
            ttk.Label(
                self,
                text="No power supplies are configured.",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, row in enumerate(rows):
            frame = ttk.LabelFrame(
                self,
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

            create_device_status_indicator(
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
                font=BODY_BOLD_FONT,
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
                self._on_mode_change(
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
                foreground=MUTED_TEXT,
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
                font=BODY_BOLD_FONT,
            ).grid(row=0, column=0, sticky="w")

            ttk.Label(
                values,
                text="Set",
                font=BODY_BOLD_FONT,
            ).grid(
                row=0,
                column=1,
                sticky="w",
                padx=(18, 0),
            )

            ttk.Label(
                values,
                text="Actual",
                font=BODY_BOLD_FONT,
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
                    font=SECTION_FONT,
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
                font=SECTION_FONT,
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
                foreground=MUTED_TEXT,
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
                    self.first_entry is None
                    and row.is_available
                ):
                    self.first_entry = entry

                if control_kind == "voltage":
                    action = (
                        lambda device_id=row.device_id,
                        selected_entry=entry:
                        self._on_set_voltage(
                            device_id,
                            selected_entry,
                        )
                    )
                    button_text = "Set voltage"
                else:
                    action = (
                        lambda device_id=row.device_id,
                        selected_entry=entry:
                        self._on_set_current(
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
                self._on_enable_output(device_id),
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
                self._on_set_output(
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

    def _create_output_indicator(
        self,
        parent: tk.Misc,
        *,
        is_available: bool,
        output_enabled: bool,
    ) -> None:
        if not is_available:
            text = "●  OUTPUT STATE UNKNOWN"
            foreground = WARNING_TEXT
            background = WARNING_BACKGROUND
        elif output_enabled:
            text = "●  OUTPUT ENABLED"
            foreground = SUCCESS_TEXT
            background = SUCCESS_BACKGROUND
        else:
            text = "●  Output disabled"
            foreground = MUTED_TEXT
            background = NEUTRAL_BACKGROUND

        indicator = tk.Label(
            parent,
            text=text,
            foreground=foreground,
            background=background,
            font=SECTION_FONT,
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
