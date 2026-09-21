import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.ui.device_setup.types import AddGuardianRequest


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class GuardianDialogs(SetupDialog):
    def _open_add_guardian(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add OHAUS Guardian 5000")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        fields = (
            ("Device ID", "hotplate"),
            ("Hardware label", "Guardian 5000"),
            ("Purpose (optional)", ""),
            ("Maximum temperature (degC, optional)", ""),
            ("Maximum speed (rpm, optional)", ""),
            ("Timeout (seconds)", "2.0"),
            ("Measurement interval (seconds)", "2.0"),
        )
        entries: dict[str, ttk.Entry] = {}
        for row_index, (label, default) in enumerate(fields):
            ttk.Label(form, text=label).grid(
                row=row_index,
                column=0,
                sticky="w",
                padx=(0, 10),
                pady=4,
            )
            entry = ttk.Entry(form, width=32)
            entry.insert(0, default)
            entry.grid(row=row_index, column=1, sticky="ew", pady=4)
            entries[label] = entry

        port_row = len(fields)
        ttk.Label(form, text="Serial port").grid(
            row=port_row,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=4,
        )
        known_ports = tuple(
            port.device for port in self._view_model.serial_ports()
        )
        port_selector = ttk.Combobox(
            form,
            values=known_ports,
            width=29,
        )
        if known_ports:
            port_selector.set(known_ports[0])
        port_selector.grid(row=port_row, column=1, sticky="ew", pady=4)

        note = ttk.Label(
            form,
            text=(
                "Saving is allowed only after one successful read-only "
                "identity and status query. No heating or stirring "
                "command will be sent."
            ),
            wraplength=430,
        )
        note.grid(
            row=port_row + 1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 8),
        )

        buttons = ttk.Frame(form)
        buttons.grid(
            row=port_row + 2,
            column=0,
            columnspan=2,
            sticky="e",
        )

        def check_and_save() -> None:
            try:
                timeout_seconds = float(
                    entries["Timeout (seconds)"].get().strip()
                )
                poll_interval_seconds = float(
                    entries["Measurement interval (seconds)"].get().strip()
                )
                maximum_temperature_text = entries[
                    "Maximum temperature (degC, optional)"
                ].get().strip()
                maximum_speed_text = entries[
                    "Maximum speed (rpm, optional)"
                ].get().strip()
                maximum_temperature = (
                    float(maximum_temperature_text)
                    if maximum_temperature_text
                    else None
                )
                maximum_speed = (
                    float(maximum_speed_text) if maximum_speed_text else None
                )
            except ValueError:
                messagebox.showerror(
                    "Invalid numeric value",
                    "Timeout, measurement interval, and optional limits "
                    "must be numbers.",
                    parent=dialog,
                )
                return

            confirmed = messagebox.askyesno(
                "Run read-only check",
                "Open the selected COM port and query the Guardian's "
                "identity and status once?\n\nNo heating or stirring "
                "command will be sent.",
                parent=dialog,
            )
            if not confirmed:
                return

            result = self._view_model.add_guardian_and_check(
                AddGuardianRequest(
                    device_id=entries["Device ID"].get(),
                    hardware_label=entries["Hardware label"].get(),
                    purpose_label=entries["Purpose (optional)"].get(),
                    port=port_selector.get(),
                    timeout_seconds=timeout_seconds,
                    maximum_temperature=maximum_temperature,
                    maximum_speed=maximum_speed,
                    poll_interval_seconds=poll_interval_seconds,
                )
            )
            self._record_result(result)
            self.refresh()
            if result.succeeded:
                messagebox.showinfo(
                    "Guardian added",
                    result.summary,
                    parent=dialog,
                )
                dialog.destroy()
            else:
                messagebox.showerror(
                    "Guardian not added",
                    result.summary + "\n\n" + result.technical_details,
                    parent=dialog,
                )

        ttk.Button(
            buttons,
            text="Cancel",
            command=dialog.destroy,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            buttons,
            text="Read-only check and save",
            command=check_and_save,
        ).grid(row=0, column=1)
        for field_entry in entries.values():
            field_entry.bind("<Return>", lambda _event: check_and_save())
            field_entry.bind("<KP_Enter>", lambda _event: check_and_save())
        port_selector.bind("<Return>", lambda _event: check_and_save())
        port_selector.bind("<KP_Enter>", lambda _event: check_and_save())
        entries["Device ID"].focus_set()
        entries["Device ID"].selection_range(0, "end")
