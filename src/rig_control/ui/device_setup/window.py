import argparse
import tkinter as tk
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from rig_control.rig_profile import RigProfile
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.ui.common.theme import MONOSPACE_FONT, SECTION_FONT, TITLE_FONT
from rig_control.ui.device_setup.model import (
    AddAlicatRequest,
    AddKeithleyRequest,
    DeviceSetupViewModel,
    ReadinessCheckResult,
)


class DeviceSetupWindow:
    """Manage configured devices using cautious read-only checks."""

    def __init__(self, root: tk.Tk, view_model: DeviceSetupViewModel) -> None:
        self._root = root
        self._view_model = view_model
        self._history: list[str] = []
        self._configure_window()
        self._create_widgets()
        self.refresh()

    def _configure_window(self) -> None:
        self._root.title("Echem Rig Control - Device setup")
        self._root.geometry("1050x700")
        self._root.minsize(820, 560)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=3)
        main.rowconfigure(4, weight=1)
        main.rowconfigure(7, weight=2)

        ttk.Label(main, text="Device setup and readiness", font=TITLE_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )

        self._devices = ttk.Treeview(
            main,
            columns=("label", "type", "connection", "readiness"),
            show="tree headings",
            selectmode="browse",
        )
        self._devices.heading("#0", text="Device ID")
        self._devices.heading("label", text="Label")
        self._devices.heading("type", text="Type")
        self._devices.heading("connection", text="Connection")
        self._devices.heading("readiness", text="Readiness")
        self._devices.column("#0", width=150)
        self._devices.column("label", width=160)
        self._devices.column("type", width=230)
        self._devices.column("connection", width=180)
        self._devices.column("readiness", width=110)
        self._devices.grid(row=1, column=0, sticky="nsew")

        device_buttons = ttk.Frame(main)
        device_buttons.grid(row=2, column=0, sticky="w", pady=(8, 12))
        ttk.Button(
            device_buttons,
            text="Add device",
            command=self._open_add_device,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Run read-only check",
            command=self._check_selected,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Refresh lists",
            command=self.refresh,
        ).grid(row=0, column=2)

        ttk.Label(main, text="Available Windows serial ports", font=SECTION_FONT).grid(
            row=3, column=0, sticky="w", pady=(0, 4)
        )
        self._ports = ttk.Treeview(
            main,
            columns=("description", "hardware_id"),
            show="tree headings",
            height=4,
        )
        self._ports.heading("#0", text="Port")
        self._ports.heading("description", text="Description")
        self._ports.heading("hardware_id", text="Hardware ID")
        self._ports.column("#0", width=100)
        self._ports.column("description", width=350)
        self._ports.column("hardware_id", width=500)
        self._ports.grid(row=4, column=0, sticky="nsew")

        self._port_note = ttk.Label(main, text="")
        self._port_note.grid(row=5, column=0, sticky="w", pady=(4, 12))

        ttk.Label(main, text="Read-only check log", font=SECTION_FONT).grid(
            row=6, column=0, sticky="w", pady=(0, 4)
        )
        self._log = tk.Text(
            main,
            height=9,
            wrap="word",
            font=MONOSPACE_FONT,
            state="disabled",
        )
        self._log.grid(row=7, column=0, sticky="nsew")

    def refresh(self) -> None:
        selected = self._selected_device_id()
        for item in self._devices.get_children():
            self._devices.delete(item)
        for row in self._view_model.device_rows():
            self._devices.insert(
                "",
                "end",
                iid=row.device_id,
                text=row.device_id,
                values=(
                    row.friendly_name,
                    row.device_type,
                    row.connection,
                    row.readiness,
                ),
            )
        if selected and self._devices.exists(selected):
            self._devices.selection_set(selected)

        for item in self._ports.get_children():
            self._ports.delete(item)
        ports = self._view_model.serial_ports()
        for port in ports:
            self._ports.insert(
                "",
                "end",
                text=port.device,
                values=(port.description, port.hardware_id),
            )
        if self._view_model.serial_port_error:
            self._port_note.configure(
                text="Port discovery failed: "
                + self._view_model.serial_port_error
            )
        elif ports:
            self._port_note.configure(text=f"{len(ports)} serial port(s) found.")
        else:
            self._port_note.configure(text="No Windows serial ports found.")

    def _check_selected(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            messagebox.showinfo(
                "Select a device",
                "Select a configured device before running a check.",
                parent=self._root,
            )
            return
        result = self._view_model.check_device(device_id)
        self._record_result(result)
        self.refresh()

    def _open_add_device(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add device")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame,
            text="What type of device do you want to add?",
            font=SECTION_FONT,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        selector = ttk.Combobox(
            frame,
            state="readonly",
            values=("Alicat mass-flow controller", "Keithley 2260B"),
            width=34,
        )
        selector.current(0)
        selector.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        def continue_to_form() -> None:
            selected = selector.get()
            dialog.destroy()
            if selected == "Keithley 2260B":
                self._open_add_keithley()
            else:
                self._open_add_alicat()

        ttk.Button(frame, text="Cancel", command=dialog.destroy).grid(
            row=2, column=0, padx=(0, 8)
        )
        ttk.Button(frame, text="Continue", command=continue_to_form).grid(
            row=2, column=1
        )
        selector.focus_set()

    def _open_add_alicat(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add Alicat MFC")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        fields = (
            ("Device ID", "mfc_a"),
            ("Hardware label", "MFC A"),
            ("Purpose (optional)", "Nitrogen"),
            ("Alicat address", "A"),
            ("Maximum flow (SCCM)", "200"),
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
        if known_ports:
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
                    entries["Maximum flow (SCCM)"].get().strip()
                )
            except ValueError:
                messagebox.showerror(
                    "Invalid maximum flow",
                    "Maximum flow must be a number.",
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
        entries["Device ID"].focus_set()
        entries["Device ID"].selection_range(0, "end")

    def _open_add_keithley(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add Keithley 2260B")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        form = ttk.Frame(dialog, padding=14)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)
        fields = (
            ("Device ID", "main_power_supply"),
            ("Hardware label", "Main power supply"),
            ("Purpose (optional)", "Electrolysis supply"),
            ("IP address or host name", ""),
            ("SCPI port", "2268"),
            ("Timeout (seconds)", "5"),
            ("Maximum voltage (V)", "30"),
            ("Maximum current (A)", "108"),
            ("Maximum power (W)", "1080"),
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
                "Connect to the entered Ethernet target and send *IDN? "
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
                    "Keithley added",
                    result.summary,
                    parent=dialog,
                )
                dialog.destroy()
            else:
                messagebox.showerror(
                    "Keithley not added",
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
        entries["Device ID"].focus_set()
        entries["Device ID"].selection_range(0, "end")

    def _selected_device_id(self) -> str | None:
        selected = self._devices.selection()
        return selected[0] if selected else None

    def _record_result(self, result: ReadinessCheckResult) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {result.summary}"
        if result.technical_details:
            line += "\n" + result.technical_details
        self._history.append(line)
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.insert("1.0", "\n\n".join(self._history))
        self._log.configure(state="disabled")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open device setup and readiness checks"
    )
    parser.add_argument(
        "configuration",
        nargs="?",
        default="device-library.toml",
        help="Hardware-library path (default: device-library.toml)",
    )
    parsed = parser.parse_args(arguments)

    profile_path = Path(parsed.configuration)
    try:
        if profile_path.exists():
            profile = load_rig_profile(profile_path)
        elif profile_path == Path("device-library.toml"):
            profile = RigProfile(
                profile_id="device_library",
                friendly_name="Local device library",
                device_roles=(),
            )
        else:
            profile = load_rig_profile(profile_path)
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Cannot open device setup",
            f"Could not load {parsed.configuration!r}.\n\n"
            f"{type(error).__name__}: {error}",
            parent=root,
        )
        root.destroy()
        return 1

    root = tk.Tk()
    DeviceSetupWindow(
        root,
        DeviceSetupViewModel(profile, profile_path=profile_path),
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
