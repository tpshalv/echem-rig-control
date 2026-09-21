import tkinter as tk
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk

from rig_control.ui.common.theme import MONOSPACE_FONT, SECTION_FONT, TITLE_FONT
from rig_control.ui.device_setup.model import DeviceSetupViewModel
from rig_control.ui.device_setup.types import (
    ReadinessCheckResult, SCPI_POWER_SUPPLY_DRIVER_LABELS,
    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER,
)
from rig_control.ui.device_setup.dialogs.base import DialogContext
from rig_control.ui.device_setup.dialogs.editing import parse_channel_labels
from rig_control.ui.device_setup.dialogs.profile import ProfileDialogs
from rig_control.ui.device_setup.dialogs.editing import DeviceEditDialogs
from rig_control.ui.device_setup.dialogs.alicat import AlicatDialogs
from rig_control.ui.device_setup.dialogs.esp32 import Esp32Dialogs
from rig_control.ui.device_setup.dialogs.temperature_probe import TemperatureProbeDialogs
from rig_control.ui.device_setup.dialogs.guardian import GuardianDialogs
from rig_control.ui.device_setup.dialogs.power_supply import PowerSupplyDialogs


class DeviceSetupWindow:
    """Manage configured devices using cautious read-only checks."""

    def __init__(self, root: tk.Tk, view_model: DeviceSetupViewModel) -> None:
        self._root = root
        self._view_model = view_model
        self._history: list[str] = []
        self._check_results: Queue[ReadinessCheckResult] = Queue()
        self._check_in_progress = False
        context = DialogContext(
            root, view_model, self.refresh, self._record_result,
            self._selected_device_id,
        )
        self._profile_dialogs = ProfileDialogs(context)
        self._editing_dialogs = DeviceEditDialogs(context)
        self._alicat_dialogs = AlicatDialogs(context)
        self._esp32_dialogs = Esp32Dialogs(context)
        self._temperature_probe_dialogs = TemperatureProbeDialogs(context)
        self._guardian_dialogs = GuardianDialogs(context)
        self._power_supply_dialogs = PowerSupplyDialogs(context)
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
        main.rowconfigure(2, weight=3)
        main.rowconfigure(5, weight=1)
        main.rowconfigure(8, weight=2)

        profile = ttk.LabelFrame(main, text="Rig profile", padding=8)
        profile.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        profile.columnconfigure(0, weight=1)
        self._profile_summary = ttk.Label(profile, justify="left")
        self._profile_summary.grid(row=0, column=0, sticky="w")
        profile_buttons = ttk.Frame(profile)
        profile_buttons.grid(row=0, column=1, sticky="e")
        ttk.Button(
            profile_buttons, text="New rig", command=self._profile_dialogs._new_profile
        ).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Edit details", command=self._profile_dialogs._edit_profile
        ).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Switch profile", command=self._profile_dialogs._switch_profile
        ).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Duplicate / Save as", command=self._profile_dialogs._save_profile_as
        ).grid(row=0, column=3, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Reload", command=self._profile_dialogs._reload_profile
        ).grid(row=0, column=4)

        ttk.Label(main, text="Configured devices", font=TITLE_FONT).grid(
            row=1, column=0, sticky="w", pady=(0, 6)
        )

        self._devices = ttk.Treeview(
            main,
            columns=("label", "type", "connection", "interval", "enabled", "readiness"),
            show="tree headings",
            selectmode="browse",
        )
        self._devices.heading("#0", text="Device ID")
        self._devices.heading("label", text="Label")
        self._devices.heading("type", text="Type")
        self._devices.heading("connection", text="Connection")
        self._devices.heading("interval", text="Measurement interval")
        self._devices.heading("enabled", text="Enabled")
        self._devices.heading("readiness", text="Readiness")
        self._devices.column("#0", width=150)
        self._devices.column("label", width=160)
        self._devices.column("type", width=230)
        self._devices.column("connection", width=180)
        self._devices.column("interval", width=135)
        self._devices.column("enabled", width=70)
        self._devices.column("readiness", width=110)
        self._devices.grid(row=2, column=0, sticky="nsew")

        device_buttons = ttk.Frame(main)
        device_buttons.grid(row=3, column=0, sticky="w", pady=(8, 12))
        ttk.Button(
            device_buttons,
            text="Add device",
            command=self._open_add_device,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Edit selected",
            command=self._editing_dialogs._edit_selected_device,
        ).grid(row=0, column=1, padx=(0, 8))
        self._check_button = ttk.Button(
            device_buttons,
            text="Run read-only check",
            command=self._check_selected,
        )
        self._check_button.grid(row=0, column=2, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Edit measurement interval",
            command=self._editing_dialogs._edit_measurement_interval,
        ).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Remove selected",
            command=self._editing_dialogs._remove_selected_device,
        ).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Refresh lists",
            command=self.refresh,
        ).grid(row=0, column=5)

        ttk.Label(main, text="Available Windows serial ports", font=SECTION_FONT).grid(
            row=4, column=0, sticky="w", pady=(0, 4)
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
        self._ports.grid(row=5, column=0, sticky="nsew")

        self._port_note = ttk.Label(main, text="")
        self._port_note.grid(row=6, column=0, sticky="w", pady=(4, 12))

        ttk.Label(main, text="Read-only check log", font=SECTION_FONT).grid(
            row=7, column=0, sticky="w", pady=(0, 4)
        )
        self._log = tk.Text(
            main,
            height=9,
            wrap="word",
            font=MONOSPACE_FONT,
            state="disabled",
        )
        self._log.grid(row=8, column=0, sticky="nsew")

    def refresh(self) -> None:
        profile = self._view_model.profile
        self._profile_summary.configure(
            text=(
                f"{profile.friendly_name}  ({profile.profile_id})\n"
                f"{self._view_model.profile_path.resolve()}"
            )
        )
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
                    row.measurement_interval,
                    "Yes" if row.enabled else "No",
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
        if self._check_in_progress:
            return
        self._check_in_progress = True
        self._check_button.configure(state="disabled")

        def run() -> None:
            self._check_results.put(self._view_model.check_device(device_id))

        def poll_result() -> None:
            try:
                result = self._check_results.get_nowait()
            except Empty:
                self._root.after(100, poll_result)
                return
            self._check_in_progress = False
            self._check_button.configure(state="normal")
            self._record_result(result)
            self.refresh()

        Thread(target=run, name="device-readiness-check", daemon=True).start()
        self._root.after(100, poll_result)

    def _open_add_device(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Add device")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="What type of device do you want to add?",
            font=SECTION_FONT,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        categories = {
            "Power supply": tuple(SCPI_POWER_SUPPLY_DRIVER_LABELS),
            "Mass-flow device": ("Alicat mass-flow device",),
            "Peristaltic pump": ("No direct peristaltic pump drivers installed",),
            "Analytical instrument": ("No direct analytical instrument drivers installed",),
            "Controller / autodiscovery": ("ESP32 controller (auto-discover)",),
            "Gas chromatograph": ("No direct GC drivers installed",),
            "Potentiostat": ("No direct potentiostat drivers installed",),
            "Hotplate": ("OHAUS Guardian 5000",),
            "Temperature probe": ("TA612C protocol (TA612C / DP-373 trial)",),
        }
        unavailable = {
            "No direct peristaltic pump drivers installed",
            "No direct analytical instrument drivers installed",
            "No direct GC drivers installed",
            "No direct potentiostat drivers installed",
        }

        ttk.Label(frame, text="Category").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=3,
        )
        category_selector = ttk.Combobox(
            frame,
            state="readonly",
            values=tuple(categories),
            width=34,
        )
        category_selector.current(0)
        category_selector.grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(frame, text="Specific device").grid(
            row=2,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=3,
        )
        device_selector = ttk.Combobox(
            frame,
            state="readonly",
            width=34,
        )
        device_selector.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        note = ttk.Label(frame, text="", wraplength=430)
        note.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 12))

        def update_device_options(*_args: object) -> None:
            values = categories[category_selector.get()]
            device_selector.configure(values=values)
            device_selector.current(0)
            is_unavailable = values[0] in unavailable
            note.configure(
                text=(
                    "No direct driver is available for this category yet. "
                    "Once a driver is added, its specific brand/model/range "
                    "will appear in the list above."
                    if is_unavailable
                    else "Choose the broad category first, then the specific "
                    "device or discovery path to add."
                )
            )
            continue_button.configure(
                state="disabled" if is_unavailable else "normal"
            )

        def continue_to_form() -> None:
            selected = device_selector.get()
            dialog.destroy()
            if selected in SCPI_POWER_SUPPLY_LABEL_TO_DRIVER:
                self._power_supply_dialogs._open_add_keithley(
                    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER[selected]
                )
            elif selected == "ESP32 controller (auto-discover)":
                self._esp32_dialogs._open_add_esp32()
            elif selected == "OHAUS Guardian 5000":
                self._guardian_dialogs._open_add_guardian()
            elif selected == "TA612C protocol (TA612C / DP-373 trial)":
                self._temperature_probe_dialogs._open_add_temperature_probe()
            else:
                self._alicat_dialogs._open_scan_alicat()

        ttk.Button(frame, text="Cancel", command=dialog.destroy).grid(
            row=4, column=1, padx=(0, 8), sticky="e"
        )
        continue_button = ttk.Button(
            frame,
            text="Continue",
            command=continue_to_form,
        )
        continue_button.grid(row=4, column=2, sticky="e")
        category_selector.bind("<<ComboboxSelected>>", update_device_options)
        update_device_options()
        category_selector.focus_set()

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
