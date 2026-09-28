import tkinter as tk
from dataclasses import replace
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk

from rig_control.ui.device_setup.types import (
    BPR_INSTALLATION_WARNING,
    AddAlicatRequest,
)


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class AlicatDialogs(SetupDialog):
    def _acknowledge_bpr_installation(self) -> None:
        """Record the hardware acknowledgement for a BPR saved earlier.

        Adding a BPR records this automatically; this button exists only for
        devices saved before the acknowledgement was stored with them.
        """

        device_id = self._selected_device_id()
        if not device_id:
            messagebox.showinfo(
                "Select Alicat",
                "Select the saved Alicat BPR first.",
                parent=self._root,
            )
            return
        role = self._view_model.profile.get_role(device_id)
        if (
            role.driver != "alicat"
            or role.capability.value != "back_pressure_controller"
        ):
            messagebox.showinfo(
                "Select a BPR",
                "Select an Alicat back-pressure controller.",
                parent=self._root,
            )
            return
        if not messagebox.askokcancel(
            "Back-pressure installation",
            BPR_INSTALLATION_WARNING,
            parent=self._root,
            icon=messagebox.WARNING,
        ):
            return
        result = self._view_model.acknowledge_bpr_installation(device_id)
        self._record_result(result)
        self.refresh()
        if result.succeeded:
            messagebox.showinfo("Acknowledged", result.summary, parent=self._root)
        else:
            messagebox.showerror(
                "Not acknowledged",
                result.summary + "\n\n" + result.technical_details,
                parent=self._root,
            )

    def _open_scan_alicat(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Find Alicat devices")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.geometry("820x460")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(
            frame,
            wraplength=740,
            text=(
                "Scanning addresses A-Z read-only. Each Alicat's type is read "
                "from its own control settings, so there is nothing to choose "
                "and no separate verification step."
            ),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        ttk.Label(frame, text="COM port").grid(row=2, column=0, sticky="w")
        known_ports = tuple(
            port.device for port in self._view_model.serial_ports()
        )
        port = ttk.Combobox(frame, values=known_ports, width=16)
        if "COM5" in known_ports:
            port.set("COM5")
        elif known_ports:
            port.set(known_ports[0])
        else:
            port.set("COM5")
        port.grid(row=2, column=1, sticky="w", padx=(8, 16))
        status = ttk.Label(frame, text="Not scanned")
        status.grid(row=2, column=2, sticky="w")

        results = ttk.Treeview(
            frame,
            columns=("status", "detected", "saved", "model", "range", "device_id"),
            show="tree headings",
            selectmode="browse",
            height=10,
        )
        results.heading("#0", text="Address")
        results.heading("status", text="Configuration")
        results.heading("detected", text="Detected type")
        results.heading("saved", text="Saved type")
        results.heading("model", text="Reported configuration")
        results.heading("range", text="Range")
        results.heading("device_id", text="Device ID")
        results.column("#0", width=65)
        results.column("status", width=95)
        results.column("detected", width=85)
        results.column("saved", width=80)
        results.column("model", width=280)
        results.column("range", width=105)
        results.column("device_id", width=110)
        results.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=10)

        result_queue: Queue[object] = Queue()
        discovered_by_address = {}

        def range_text(device) -> str:
            if device.detected_role == "bpr" and device.maximum_setpoint is not None:
                return f"{device.maximum_setpoint:g} {device.setpoint_unit or ''}".strip()
            if device.maximum_setpoint is not None:
                return f"{device.maximum_setpoint:g} {device.setpoint_unit or ''}".strip()
            if device.inferred_maximum_flow_sccm is not None:
                return f"{device.inferred_maximum_flow_sccm:g} SCCM"
            return "enter manually"

        def poll_result() -> None:
            if not dialog.winfo_exists():
                return
            try:
                discovered = result_queue.get_nowait()
            except Empty:
                dialog.after(100, poll_result)
                return
            scan_button.configure(state="normal")
            if isinstance(discovered, Exception):
                status.configure(text="Scan failed")
                messagebox.showerror(
                    "Alicat scan failed",
                    f"{type(discovered).__name__}: {discovered}",
                    parent=dialog,
                )
                return
            for item in results.get_children():
                results.delete(item)
            discovered_by_address.clear()
            for device in discovered:
                discovered_by_address[device.address] = device
                results.insert(
                    "",
                    "end",
                    iid=device.address,
                    text=device.address,
                    values=(
                        device.configuration_status,
                        device.detected_type,
                        device.configured_kind or "-",
                        f"{device.model or 'model not reported'}: "
                        f"{device.control_description}",
                        range_text(device),
                        device.configured_device_id or "-",
                    ),
                )
            unreadable = sum(1 for device in discovered if not device.usable)
            status.configure(
                text=f"Found {len(discovered)} device(s)"
                + (f", {unreadable} unreadable" if unreadable else "")
            )

        def scan() -> None:
            selected_port = port.get().strip()
            if not selected_port:
                messagebox.showinfo(
                    "Select a port", "Select or enter a COM port.", parent=dialog
                )
                return
            scan_button.configure(state="disabled")
            status.configure(text="Scanning A-Z...")

            def run() -> None:
                try:
                    found = self._view_model.scan_alicats(selected_port, 19200)
                except Exception as error:
                    result_queue.put(error)
                else:
                    result_queue.put(found)

            Thread(target=run, name="alicat-address-scan", daemon=True).start()
            dialog.after(100, poll_result)

        def add_selected() -> None:
            selection = results.selection()
            if not selection:
                messagebox.showinfo(
                    "Select an address",
                    "Select a discovered Alicat first.",
                    parent=dialog,
                )
                return
            address = selection[0]
            selected = discovered_by_address[address]
            if selected.configured_device_id is not None:
                check_result = self._view_model.check_device(
                    selected.configured_device_id
                )
                self._record_result(check_result)
                self.refresh()
                if check_result.succeeded:
                    messagebox.showinfo(
                        "Read-only check passed",
                        check_result.summary,
                        parent=dialog,
                    )
                else:
                    messagebox.showerror(
                        "Read-only check failed",
                        check_result.summary + "\n\n" + check_result.technical_details,
                        parent=dialog,
                    )
                return
            if not selected.usable:
                messagebox.showerror(
                    "Configuration could not be read",
                    selected.configuration_error
                    or "This Alicat's control configuration could not be read.",
                    parent=dialog,
                )
                return
            selected_port = port.get().strip()
            dialog.destroy()
            self._open_add_alicat(
                initial_port=selected_port,
                initial_address=address,
                detected=selected,
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=4, sticky="e")
        scan_button = ttk.Button(buttons, text="Scan A-Z", command=scan)
        scan_button.grid(row=0, column=0, padx=(0, 8))
        continue_button = ttk.Button(
            buttons,
            text="Continue with selected device",
            command=add_selected,
        )
        continue_button.grid(row=0, column=1, padx=(0, 8))

        def handle_result_selection(_: tk.Event | None = None) -> None:
            selection = results.selection()
            selected = (
                discovered_by_address.get(selection[0]) if selection else None
            )
            continue_button.configure(
                text=(
                    "Run check for configured device"
                    if selected is not None
                    and selected.configured_device_id is not None
                    else "Continue with selected device"
                )
            )

        results.bind("<<TreeviewSelect>>", handle_result_selection)
        ttk.Button(
            buttons,
            text="Enter manually",
            command=lambda: (
                dialog.destroy(),
                self._open_add_alicat(initial_port=port.get().strip()),
            ),
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(
            row=0, column=3
        )
        dialog.after(100, scan)

    def _open_add_alicat(
        self,
        *,
        initial_port: str = "",
        initial_address: str | None = None,
        detected=None,
    ) -> None:
        """Name and save one Alicat; its role is read from the instrument."""

        dialog = tk.Toplevel(self._root)
        dialog.title("Add Alicat device")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        detected_role = getattr(detected, "detected_role", None)
        is_meter = getattr(detected, "is_controller", None) is False
        default_id = (
            "bpr_a" if detected_role == "bpr"
            else "flow_meter_b" if is_meter
            else "mfc_a"
        )
        default_label = (
            "Outlet BPR" if detected_role == "bpr"
            else "Flow meter B" if is_meter
            else "MFC A"
        )
        default_maximum = ""
        if detected is not None:
            if detected_role == "mfc" and detected.maximum_setpoint:
                default_maximum = f"{detected.maximum_setpoint:g}"
            elif detected.inferred_maximum_flow_sccm:
                default_maximum = f"{detected.inferred_maximum_flow_sccm:g}"

        fields = (
            ("Device ID", default_id),
            ("Hardware label", default_label),
            ("Purpose (optional)", "" if detected_role == "bpr" else "Nitrogen"),
            ("Alicat address", initial_address or "A"),
            ("Maximum flow (blank uses the instrument's range)", default_maximum),
            ("Measurement interval (seconds)", "1.0"),
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
        ttk.Label(form, text="BB3 COM port").grid(
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
        if initial_port:
            port_selector.set(initial_port)
        elif known_ports:
            port_selector.set(known_ports[0])
        port_selector.grid(row=port_row, column=1, sticky="ew", pady=4)

        detected_text = (
            f"Detected: {detected.control_description}."
            if detected is not None
            else "The device type is read from the instrument when you save."
        )
        ttk.Label(
            form,
            text=(
                detected_text
                + " Saving reads the instrument's configuration and one status "
                "frame. No setpoint, gas or configuration command is sent."
            ),
            wraplength=430,
        ).grid(
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
            maximum_text = entries[
                "Maximum flow (blank uses the instrument's range)"
            ].get().strip()
            try:
                maximum_flow = float(maximum_text) if maximum_text else None
                poll_interval_seconds = float(
                    entries["Measurement interval (seconds)"].get().strip()
                )
            except ValueError:
                messagebox.showerror(
                    "Invalid numeric value",
                    "Maximum flow and measurement interval must be numbers.",
                    parent=dialog,
                )
                return

            request = AddAlicatRequest(
                device_id=entries["Device ID"].get(),
                hardware_label=entries["Hardware label"].get(),
                purpose_label=entries["Purpose (optional)"].get(),
                port=port_selector.get(),
                unit_address=entries["Alicat address"].get(),
                maximum_flow=maximum_flow,
                poll_interval_seconds=poll_interval_seconds,
            )
            result = self._view_model.add_alicat_and_check(request)
            if result.requires_acknowledgement:
                acknowledged = messagebox.askokcancel(
                    "Back-pressure controller detected",
                    result.requires_acknowledgement,
                    parent=dialog,
                    icon=messagebox.WARNING,
                )
                if not acknowledged:
                    return
                result = self._view_model.add_alicat_and_check(
                    replace(request, downstream_valve_acknowledged=True)
                )
            self._record_result(result)
            self.refresh()
            if result.succeeded:
                messagebox.showinfo(
                    "Alicat added",
                    result.summary,
                    parent=dialog,
                )
                dialog.destroy()
            else:
                messagebox.showerror(
                    "Alicat not added",
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
            text="Detect and save",
            command=check_and_save,
        ).grid(row=0, column=1)
        for field_entry in entries.values():
            field_entry.bind("<Return>", lambda _event: check_and_save())
            field_entry.bind("<KP_Enter>", lambda _event: check_and_save())
        port_selector.bind("<Return>", lambda _event: check_and_save())
        port_selector.bind("<KP_Enter>", lambda _event: check_and_save())
        entries["Device ID"].focus_set()
        entries["Device ID"].selection_range(0, "end")

