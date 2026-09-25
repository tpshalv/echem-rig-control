import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.ui.device_setup.dialogs.base import SetupDialog
from rig_control.ui.device_setup.types import AddEzoHumRequest


class EzoHumDialogs(SetupDialog):
    def _open_add_ezo_hum(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add Atlas Scientific EZO-HUM")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        fields = (
            ("Device ID", "inlet_humidity"),
            ("Hardware label", "Inlet humidity"),
            ("Purpose (optional)", ""),
            ("Timeout (seconds)", "2.0"),
            ("Measurement interval (seconds)", "1.0"),
        )
        entries: dict[str, ttk.Entry] = {}
        for row, (label, default) in enumerate(fields):
            ttk.Label(form, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=4
            )
            entry = ttk.Entry(form, width=34)
            entry.insert(0, default)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            entries[label] = entry

        port_row = len(fields)
        ttk.Label(form, text="USB-UART serial port").grid(
            row=port_row, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ports = tuple(port.device for port in self._view_model.serial_ports())
        port_selector = ttk.Combobox(form, values=ports, width=31)
        if ports:
            port_selector.current(0)
        port_selector.grid(row=port_row, column=1, sticky="ew", pady=4)

        include_dew_point = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            form, text="Record dew point", variable=include_dew_point
        ).grid(row=port_row + 1, column=1, sticky="w", pady=4)
        note = ttk.Label(
            form,
            text=(
                "The check uses 9600 baud and temporarily changes the probe's "
                "UART output to on-demand humidity and temperature readings. "
                "Those settings reset when probe power is removed."
            ),
            wraplength=460,
        )
        note.grid(row=port_row + 2, column=0, columnspan=2, sticky="w", pady=(8, 10))

        buttons = ttk.Frame(form)
        buttons.grid(row=port_row + 3, column=0, columnspan=2, sticky="e")

        def check_and_save() -> None:
            try:
                request = AddEzoHumRequest(
                    device_id=entries["Device ID"].get(),
                    hardware_label=entries["Hardware label"].get(),
                    purpose_label=entries["Purpose (optional)"].get(),
                    port=port_selector.get(),
                    timeout_seconds=float(entries["Timeout (seconds)"].get()),
                    poll_interval_seconds=float(entries["Measurement interval (seconds)"].get()),
                    include_dew_point=include_dew_point.get(),
                )
            except ValueError:
                messagebox.showerror(
                    "Invalid numeric value",
                    "Timeout and measurement interval must be numbers.",
                    parent=dialog,
                )
                return
            result = self._view_model.add_ezo_hum_and_check(request)
            self._record_result(result)
            self.refresh()
            if result.succeeded:
                messagebox.showinfo("EZO-HUM added", result.summary + "\n\n" + result.technical_details, parent=dialog)
                dialog.destroy()
            else:
                messagebox.showerror("EZO-HUM not added", result.summary + "\n\n" + result.technical_details, parent=dialog)

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Configure, check and save", command=check_and_save).grid(row=0, column=1)
