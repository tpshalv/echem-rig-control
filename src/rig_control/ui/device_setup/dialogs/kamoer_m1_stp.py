import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.ui.device_setup.types import AddKamoerM1StpRequest


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class KamoerM1StpDialogs(SetupDialog):
    def _open_add_kamoer_m1_stp(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add Kamoer M1-STP peristaltic pump")
        dialog.transient(self._root); dialog.grab_set()
        form = ttk.Frame(dialog, padding=14); form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)
        fields = (("Device ID", "pump1"), ("Hardware label", "Kamoer M1-STP"),
                  ("Purpose (optional)", ""), ("Modbus slave address", "1"),
                  ("Maximum speed (rpm)", "350"), ("Timeout (seconds)", "2.0"))
        entries = {}
        for row, (label, default) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            entry = ttk.Entry(form, width=36); entry.insert(0, default); entry.grid(row=row, column=1, sticky="ew", pady=4); entries[label] = entry
        port_row = len(fields)
        ttk.Label(form, text="Serial port").grid(row=port_row, column=0, sticky="w", padx=(0, 10), pady=4)
        ports = tuple(port.device for port in self._view_model.serial_ports())
        port_selector = ttk.Combobox(form, values=ports, width=33); port_selector.grid(row=port_row, column=1, sticky="ew", pady=4)
        if ports: port_selector.current(0)
        note = ttk.Label(
            form,
            text="The read-only check reads the pump's status over Modbus. It does not "
                 "start, stop or reconfigure the pump. The pump's own RS485 screen shows "
                 "its slave address, baud rate and framing - this driver expects 9600 8N1.",
            wraplength=480,
        )
        note.grid(row=port_row + 1, column=0, columnspan=2, sticky="w", pady=8)
        buttons = ttk.Frame(form); buttons.grid(row=port_row + 2, column=0, columnspan=2, sticky="e")
        def check_and_save():
            try:
                request = AddKamoerM1StpRequest(
                    entries["Device ID"].get(), entries["Hardware label"].get(),
                    entries["Purpose (optional)"].get(), port_selector.get(),
                    int(entries["Modbus slave address"].get()),
                    float(entries["Timeout (seconds)"].get()),
                    float(entries["Maximum speed (rpm)"].get()),
                )
            except (TypeError, ValueError) as error:
                messagebox.showerror("Invalid value", str(error), parent=dialog); return
            result = self._view_model.add_kamoer_m1_stp_and_check(request)
            self._record_result(result); self.refresh()
            if result.succeeded:
                messagebox.showinfo("Pump added", result.summary + "\n\n" + result.technical_details, parent=dialog); dialog.destroy()
            else:
                messagebox.showerror("Pump not added", result.summary + "\n\n" + result.technical_details, parent=dialog)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Read-only check and save", command=check_and_save).grid(row=0, column=1)
