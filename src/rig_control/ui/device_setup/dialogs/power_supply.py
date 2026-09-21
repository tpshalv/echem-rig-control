import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.ui.device_setup.types import (
    AddKeithleyRequest,
    SCPI_POWER_SUPPLY_DRIVER_LABELS,
    SCPI_POWER_SUPPLY_DRIVERS,
    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER,
)


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class PowerSupplyDialogs(SetupDialog):
    def _open_add_keithley(self, initial_driver: str = "keithley_2260b") -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add SCPI power supply")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)
        initial_metadata = SCPI_POWER_SUPPLY_DRIVERS[initial_driver]
        fields = (
            ("Power supply model", initial_metadata["display_name"]),
            ("Device ID", "main_power_supply"),
            ("Hardware label", "Main power supply"),
            ("Purpose (optional)", "Electrolysis supply"),
            ("Connection method (Ethernet or VISA)", "VISA"),
            ("VISA resource", "ASRL4::INSTR"),
            ("VISA baud rate", "9600"),
            ("IP address or host name", ""),
            ("SCPI port", str(initial_metadata["default_port"])),
            ("Timeout (seconds)", "5"),
            ("Maximum voltage (V)", f"{initial_metadata['default_voltage']:g}"),
            ("Maximum current (A)", f"{initial_metadata['default_current']:g}"),
            ("Maximum power (W)", f"{initial_metadata['default_power']:g}"),
            ("Measurement interval (seconds)", "0.1"),
        )
        entries: dict[str, ttk.Entry | ttk.Combobox] = {}
        labels: dict[str, ttk.Label] = {}
        for row_index, (label, default) in enumerate(fields):
            label_widget = ttk.Label(form, text=label)
            label_widget.grid(
                row=row_index,
                column=0,
                sticky="w",
                padx=(0, 10),
                pady=4,
            )
            if label == "Power supply model":
                entry = ttk.Combobox(
                    form,
                    width=29,
                    values=SCPI_POWER_SUPPLY_DRIVER_LABELS,
                    state="readonly",
                )
                entry.set(str(default))
            elif label == "Connection method (Ethernet or VISA)":
                entry = ttk.Combobox(
                    form,
                    width=29,
                    values=("VISA", "Ethernet"),
                    state="readonly",
                )
                entry.set(default)
            else:
                entry = ttk.Entry(form, width=32)
                entry.insert(0, default)
            entry.grid(row=row_index, column=1, sticky="ew", pady=4)
            labels[label] = label_widget
            entries[label] = entry

        visa_fields = ("VISA resource", "VISA baud rate")
        ethernet_fields = ("IP address or host name", "SCPI port")

        def update_model_defaults(*_args: object) -> None:
            driver = SCPI_POWER_SUPPLY_LABEL_TO_DRIVER[
                entries["Power supply model"].get()
            ]
            metadata = SCPI_POWER_SUPPLY_DRIVERS[driver]
            entries["SCPI port"].delete(0, "end")
            entries["SCPI port"].insert(0, str(metadata["default_port"]))
            entries["Maximum voltage (V)"].delete(0, "end")
            entries["Maximum voltage (V)"].insert(
                0,
                f"{metadata['default_voltage']:g}",
            )
            entries["Maximum current (A)"].delete(0, "end")
            entries["Maximum current (A)"].insert(
                0,
                f"{metadata['default_current']:g}",
            )
            entries["Maximum power (W)"].delete(0, "end")
            entries["Maximum power (W)"].insert(
                0,
                f"{metadata['default_power']:g}",
            )

        def update_connection_fields(*_args: object) -> None:
            method = entries[
                "Connection method (Ethernet or VISA)"
            ].get().strip().casefold()
            for field in visa_fields:
                action = "grid" if method == "visa" else "grid_remove"
                getattr(labels[field], action)()
                getattr(entries[field], action)()
            for field in ethernet_fields:
                action = "grid" if method == "ethernet" else "grid_remove"
                getattr(labels[field], action)()
                getattr(entries[field], action)()

        entries["Power supply model"].bind(
            "<<ComboboxSelected>>",
            update_model_defaults,
        )
        entries["Connection method (Ethernet or VISA)"].bind(
            "<<ComboboxSelected>>",
            update_connection_fields,
        )
        update_connection_fields()

        note_row = len(fields)
        ttk.Label(
            form,
            text=(
                "Saving is allowed only after the instrument responds to "
                "the read-only *IDN? identification query. Outputs and "
                "setpoints are not changed."
            ),
            wraplength=450,
        ).grid(
            row=note_row,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 8),
        )
        buttons = ttk.Frame(form)
        buttons.grid(
            row=note_row + 1,
            column=0,
            columnspan=2,
            sticky="e",
        )

        def check_and_save() -> None:
            try:
                request = AddKeithleyRequest(
                    device_id=entries["Device ID"].get(),
                    hardware_label=entries["Hardware label"].get(),
                    purpose_label=entries["Purpose (optional)"].get(),
                    host=entries["IP address or host name"].get(),
                    port=int(entries["SCPI port"].get().strip()),
                    timeout_seconds=float(
                        entries["Timeout (seconds)"].get().strip()
                    ),
                    maximum_voltage=float(
                        entries["Maximum voltage (V)"].get().strip()
                    ),
                    maximum_current=float(
                        entries["Maximum current (A)"].get().strip()
                    ),
                    maximum_power=float(
                        entries["Maximum power (W)"].get().strip()
                    ),
                    connection_method=entries[
                        "Connection method (Ethernet or VISA)"
                    ].get(),
                    resource_name=entries["VISA resource"].get(),
                    visa_baud_rate=int(entries["VISA baud rate"].get().strip()),
                    poll_interval_seconds=float(
                        entries["Measurement interval (seconds)"].get().strip()
                    ),
                    driver=SCPI_POWER_SUPPLY_LABEL_TO_DRIVER[
                        entries["Power supply model"].get()
                    ],
                )
            except ValueError:
                messagebox.showerror(
                    "Invalid numeric value",
                    "Port, timeout and electrical limits must be numbers.",
                    parent=dialog,
                )
                return

            confirmed = messagebox.askyesno(
                "Run read-only identification",
                "Connect to the entered target and send *IDN? "
                "once?\n\nNo output or setpoint command will be sent.",
                parent=dialog,
            )
            if not confirmed:
                return
            result = self._view_model.add_keithley_and_check(request)
            self._record_result(result)
            self.refresh()
            if result.succeeded:
                messagebox.showinfo(
                    "Power supply added",
                    result.summary,
                    parent=dialog,
                )
                dialog.destroy()
            else:
                messagebox.showerror(
                    "Power supply not added",
                    result.summary + "\n\n" + result.technical_details,
                    parent=dialog,
                )

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(
            buttons,
            text="Read-only identify and save",
            command=check_and_save,
        ).grid(row=0, column=1)
        for field_entry in entries.values():
            field_entry.bind("<Return>", lambda _event: check_and_save())
            field_entry.bind("<KP_Enter>", lambda _event: check_and_save())
        entries["Device ID"].focus_set()
        entries["Device ID"].selection_range(0, "end")
