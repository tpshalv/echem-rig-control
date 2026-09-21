import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk

from rig_control.ui.device_setup.types import AddAlicatRequest


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class AlicatDialogs(SetupDialog):
    def _open_scan_alicat(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Find Alicat mass-flow device")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.geometry("780x470")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        description = ttk.Label(
            frame,
            wraplength=700,
        )
        description.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        ttk.Label(frame, text="Device function").grid(row=1, column=0, sticky="w")
        device_function = tk.StringVar(value="Controller (MFC)")
        function_selector = ttk.Combobox(
            frame,
            textvariable=device_function,
            values=("Controller (MFC)", "Meter (MFM)"),
            state="readonly",
            width=20,
        )
        function_selector.grid(row=1, column=1, sticky="w", padx=(8, 16))
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
            columns=("status", "type", "model", "range", "device_id", "response"),
            show="tree headings",
            selectmode="browse",
            height=10,
        )
        results.heading("#0", text="Address")
        results.heading("status", text="Configuration")
        results.heading("type", text="Saved type")
        results.heading("model", text="Detected model/type")
        results.heading("range", text="Inferred range")
        results.heading("device_id", text="Device ID")
        results.heading("response", text="Raw response")
        results.column("#0", width=65)
        results.column("status", width=105)
        results.column("type", width=80)
        results.column("model", width=135)
        results.column("range", width=105)
        results.column("device_id", width=115)
        results.column("response", width=220)
        results.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=10)

        result_queue: Queue[object] = Queue()
        discovered_by_address = {}

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
                        device.configured_kind or "-",
                        (
                            f"{device.model or 'unknown'} / "
                            f"{device.inferred_kind or 'ambiguous'}"
                        ),
                        (
                            f"{device.inferred_maximum_flow_sccm:g} SCCM"
                            if device.inferred_maximum_flow_sccm is not None
                            else "confirm manually"
                        ),
                        device.configured_device_id or "-",
                        device.raw_response,
                    ),
                )
            status.configure(text=f"Found {len(discovered)} device(s)")

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
            selected_port = port.get().strip()
            dialog.destroy()
            self._open_add_alicat(
                is_meter=device_function.get() == "Meter (MFM)",
                initial_port=selected_port,
                initial_address=address,
                initial_maximum_flow=(
                    selected.inferred_maximum_flow_sccm
                ),
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=4, sticky="e")
        scan_button = ttk.Button(buttons, text="Scan A-Z", command=scan)
        scan_button.grid(row=0, column=0, padx=(0, 8))
        continue_button = ttk.Button(
            buttons,
            text="Continue with selected controller",
            command=add_selected,
        )
        continue_button.grid(row=0, column=1, padx=(0, 8))

        def update_selected_action(_: tk.Event | None = None) -> None:
            selection = results.selection()
            selected = (
                discovered_by_address.get(selection[0]) if selection else None
            )

        def handle_result_selection(event: tk.Event | None = None) -> None:
            selection = results.selection()
            selected = (
                discovered_by_address.get(selection[0]) if selection else None
            )
            if selected is not None and selected.inferred_kind in {
                "controller",
                "meter",
            }:
                device_function.set(
                    "Meter (MFM)"
                    if selected.inferred_kind == "meter"
                    else "Controller (MFC)"
                )
                update_device_function()
            update_selected_action(event)
            continue_button.configure(
                text=(
                    "Run check for configured device"
                    if selected is not None
                    and selected.configured_device_id is not None
                    else (
                        "Continue with selected meter"
                        if device_function.get() == "Meter (MFM)"
                        else "Continue with selected controller"
                    )
                )
            )

        def update_device_function(_: tk.Event | None = None) -> None:
            is_meter = device_function.get() == "Meter (MFM)"
            device_type = "meter" if is_meter else "controller"
            description.configure(
                text=(
                    f"Finding an Alicat mass-flow {device_type} using a "
                    "read-only scan of addresses A-Z. Devices must have "
                    "unique addresses and be in polling mode."
                )
            )
            dialog.title(f"Find Alicat mass-flow {device_type}")
            update_selected_action()

        results.bind("<<TreeviewSelect>>", handle_result_selection)
        function_selector.bind("<<ComboboxSelected>>", update_device_function)
        ttk.Button(
            buttons,
            text="Enter manually",
            command=lambda: (
                dialog.destroy(),
                self._open_add_alicat(
                    is_meter=device_function.get() == "Meter (MFM)",
                    initial_port=port.get().strip(),
                ),
            ),
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(
            row=0, column=3
        )
        update_device_function()
        dialog.after(100, scan)

    def _open_add_alicat(
        self,
        *,
        is_meter: bool = False,
        initial_port: str = "",
        initial_address: str | None = None,
        initial_maximum_flow: float | None = None,
    ) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add Alicat mass-flow meter" if is_meter else "Add Alicat MFC")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        fields = (
            ("Device ID", "flow_meter_b" if is_meter else "mfc_a"),
            ("Hardware label", "Flow meter B" if is_meter else "MFC A"),
            ("Purpose (optional)", "Flow measurement" if is_meter else "Nitrogen"),
            (
                "Alicat address",
                initial_address or ("B" if is_meter else "A"),
            ),
            (
                "Maximum flow",
                f"{initial_maximum_flow:g}"
                if initial_maximum_flow is not None
                else "2000",
            ),
            ("Mass-flow unit", "SCCM"),
            ("Measurement interval (seconds)", "1.0"),
        )
        entries: dict[str, ttk.Entry | ttk.Combobox] = {}
        for row_index, (label, default) in enumerate(fields):
            ttk.Label(form, text=label).grid(
                row=row_index,
                column=0,
                sticky="w",
                padx=(0, 10),
                pady=4,
            )
            if label == "Mass-flow unit":
                entry = ttk.Combobox(
                    form,
                    width=29,
                    values=("SCCM",),
                    state="readonly",
                )
                entry.set(default)
            else:
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

        note = ttk.Label(
            form,
            text=(
                "Saving is allowed only after one successful read-only "
                "status poll. No flow or gas command will be sent."
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
                maximum_flow = float(
                    entries["Maximum flow"].get().strip()
                )
                poll_interval_seconds = float(
                    entries["Measurement interval (seconds)"].get().strip()
                )
            except ValueError:
                messagebox.showerror(
                    "Invalid numeric value",
                    "Maximum flow and measurement interval must be "
                    "numbers.",
                    parent=dialog,
                )
                return

            confirmed = messagebox.askyesno(
                "Run read-only check",
                "Open the selected COM port and poll the proposed Alicat "
                "address once?\n\nNo setpoint or gas-selection command "
                "will be sent.",
                parent=dialog,
            )
            if not confirmed:
                return

            result = self._view_model.add_alicat_and_check(
                AddAlicatRequest(
                    device_id=entries["Device ID"].get(),
                    hardware_label=entries["Hardware label"].get(),
                    purpose_label=entries["Purpose (optional)"].get(),
                    port=port_selector.get(),
                    unit_address=entries["Alicat address"].get(),
                    maximum_flow=maximum_flow,
                    device_kind="meter" if is_meter else "controller",
                    flow_unit=entries["Mass-flow unit"].get(),
                    poll_interval_seconds=poll_interval_seconds,
                )
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
