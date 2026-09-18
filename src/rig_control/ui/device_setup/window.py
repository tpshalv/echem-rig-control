import tkinter as tk
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
import re
from threading import Thread
from tkinter import filedialog, messagebox, ttk

from rig_control.ui.common.theme import MONOSPACE_FONT, SECTION_FONT, TITLE_FONT
from rig_control.ui.common.widgets import VerticalScrolledFrame
from rig_control.ui.device_setup.model import (
    AddAlicatRequest,
    AddEsp32Request,
    AddKeithleyRequest,
    DeviceSetupViewModel,
    EditDeviceRequest,
    ReadinessCheckResult,
    SCPI_POWER_SUPPLY_DRIVER_LABELS,
    SCPI_POWER_SUPPLY_DRIVERS,
    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER,
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
            profile_buttons, text="New rig", command=self._new_profile
        ).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Edit details", command=self._edit_profile
        ).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Switch profile", command=self._switch_profile
        ).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Duplicate / Save as", command=self._save_profile_as
        ).grid(row=0, column=3, padx=(0, 4))
        ttk.Button(
            profile_buttons, text="Reload", command=self._reload_profile
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
            command=self._edit_selected_device,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Run read-only check",
            command=self._check_selected,
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Edit measurement interval",
            command=self._edit_measurement_interval,
        ).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(
            device_buttons,
            text="Remove selected",
            command=self._remove_selected_device,
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
        result = self._view_model.check_device(device_id)
        self._record_result(result)
        self.refresh()

    def _switch_profile(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML rig profiles", "*.toml"),),
        )
        if not selected:
            return
        self._record_result(self._view_model.switch_profile(selected))
        self.refresh()

    def _new_profile(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Create new rig")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        friendly_name = tk.StringVar()
        profile_id = tk.StringVar()
        destination = tk.StringVar()
        for row, (label, variable) in enumerate(
            (
                ("Display name", friendly_name),
                ("Internal ID", profile_id),
                ("Profile filename", destination),
            )
        ):
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(frame, textvariable=variable, width=52)
            entry.grid(
                row=row, column=1, sticky="ew", padx=(8, 4), pady=3
            )
            if variable is destination:
                entry.bind(
                    "<FocusOut>",
                    lambda _event: update_id_from_filename(),
                )

        ttk.Label(
            frame,
            text="Shown in the app; spaces and capitals are allowed.",
        ).grid(row=0, column=2, sticky="w")
        ttk.Label(
            frame,
            text="Stable software label, for example main_echem_rig.",
        ).grid(row=1, column=2, sticky="w")

        def id_from_filename(filename: str) -> str:
            stem = Path(filename).stem.casefold()
            for prefix in ("rig-profile.", "rig_profile_", "rig-profile-"):
                if stem.startswith(prefix):
                    stem = stem[len(prefix):]
                    break
            value = re.sub(r"[^a-z0-9_-]+", "_", stem).strip("_-")
            if value and not value[0].isalpha():
                value = "rig_" + value
            return value

        def update_id_from_filename() -> None:
            derived = id_from_filename(destination.get())
            if derived:
                profile_id.set(derived)

        def browse() -> None:
            suggested_id = profile_id.get().strip() or id_from_filename(
                friendly_name.get()
            )
            selected = filedialog.asksaveasfilename(
                parent=dialog,
                defaultextension=".toml",
                initialfile=(
                    f"rig-profile.{suggested_id}.toml"
                    if suggested_id
                    else "rig-profile.toml"
                ),
                filetypes=(("TOML rig profiles", "*.toml"),),
            )
            if selected:
                destination.set(selected)
                update_id_from_filename()

        ttk.Button(frame, text="Browse…", command=browse).grid(
            row=2, column=2, pady=3
        )
        ttk.Label(
            frame,
            text=(
                "The new profile starts with no devices or connections. "
                "Devices can be added after creation."
            ),
            wraplength=480,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 4))

        def create() -> None:
            result = self._view_model.create_new_profile(
                profile_id.get(),
                friendly_name.get(),
                destination.get(),
            )
            self._record_result(result)
            if result.succeeded:
                dialog.destroy()
                self.refresh()
            else:
                messagebox.showerror(
                    "Could not create rig",
                    result.technical_details or result.summary,
                    parent=dialog,
                )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(buttons, text="Create rig", command=create).grid(row=0, column=1)

    def _save_profile_as(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            defaultextension=".toml",
            filetypes=(("TOML rig profiles", "*.toml"),),
        )
        if not selected:
            return
        self._record_result(self._view_model.save_profile_as(selected))
        self.refresh()

    def _reload_profile(self) -> None:
        if not messagebox.askyesno(
            "Reload profile",
            "Reload this profile from disk? Any unsaved dialog entries will "
            "not be applied.",
            parent=self._root,
        ):
            return
        self._record_result(
            self._view_model.switch_profile(self._view_model.profile_path)
        )
        self.refresh()

    def _edit_profile(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Edit rig profile details")
        dialog.transient(self._root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        values = {
            "Display name": tk.StringVar(
                value=self._view_model.profile.friendly_name
            ),
            "Internal ID": tk.StringVar(value=self._view_model.profile.profile_id),
        }
        for row, (label, variable) in enumerate(values.items()):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(frame, textvariable=variable, width=42).grid(
                row=row, column=1, sticky="ew", padx=(8, 0), pady=3
            )
        ttk.Label(
            frame,
            text="Shown in the app; spaces and capitals are allowed.",
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(
            frame,
            text="Stable software label, for example main_echem_rig.",
        ).grid(row=1, column=2, sticky="w", padx=(8, 0))

        def save() -> None:
            result = self._view_model.update_profile_identity(
                values["Internal ID"].get(),
                values["Display name"].get(),
            )
            self._record_result(result)
            if result.succeeded:
                dialog.destroy()
                self.refresh()

        ttk.Button(frame, text="Save", command=save).grid(
            row=2, column=1, sticky="e", pady=(10, 0)
        )

    def _edit_selected_device(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            messagebox.showinfo(
                "Select a device",
                "Select a configured device before editing it.",
                parent=self._root,
            )
            return
        original = self._view_model.device_edit_values(device_id)
        dialog = tk.Toplevel(self._root)
        dialog.title(f"Edit device — {device_id}")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.geometry("720x720")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        scroll = VerticalScrolledFrame(dialog)
        scroll.grid(row=0, column=0, sticky="nsew")
        frame = ttk.Frame(scroll.content, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        friendly_name = tk.StringVar(value=original.friendly_name)
        enabled = tk.BooleanVar(value=original.enabled)
        required = tk.BooleanVar(value=original.required)
        system = tk.StringVar(value=original.system)
        interval = tk.StringVar(
            value=(
                "" if original.poll_interval_seconds is None
                else f"{original.poll_interval_seconds:g}"
            )
        )
        common = (
            ("Device ID", ttk.Label(frame, text=device_id)),
            ("Friendly name", ttk.Entry(frame, textvariable=friendly_name)),
            ("System/group", ttk.Entry(frame, textvariable=system)),
            ("Measurement interval (s)", ttk.Entry(frame, textvariable=interval)),
        )
        next_row = 0
        for label, widget in common:
            ttk.Label(frame, text=label).grid(row=next_row, column=0, sticky="w")
            widget.grid(row=next_row, column=1, sticky="ew", padx=(8, 0), pady=3)
            next_row += 1
        flags = ttk.Frame(frame)
        flags.grid(row=next_row, column=1, sticky="w", padx=(8, 0), pady=3)
        ttk.Checkbutton(flags, text="Enabled", variable=enabled).grid(row=0, column=0)
        ttk.Checkbutton(flags, text="Required", variable=required).grid(
            row=0, column=1, padx=(12, 0)
        )
        next_row += 1
        mapping_entries: dict[str, tuple[dict[str, object], dict[str, tk.StringVar]]] = {}
        for title, key, mapping in (
            ("Shared connection", "connection", original.connection_parameters),
            ("Device connection", "device_connection", original.device_connection_parameters),
            ("Driver settings and limits", "settings", original.settings),
        ):
            box = ttk.LabelFrame(frame, text=title, padding=8)
            box.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            box.columnconfigure(1, weight=1)
            variables: dict[str, tk.StringVar] = {}
            for mapping_row, (name, value) in enumerate(mapping.items()):
                variable = tk.StringVar(value=str(value))
                variables[name] = variable
                ttk.Label(box, text=name.replace("_", " ")).grid(
                    row=mapping_row, column=0, sticky="w"
                )
                ttk.Entry(box, textvariable=variable).grid(
                    row=mapping_row, column=1, sticky="ew", padx=(8, 0), pady=2
                )
            if not mapping:
                ttk.Label(box, text="No values for this device.").grid(row=0, column=0)
            mapping_entries[key] = (mapping, variables)
            next_row += 1
        ttk.Label(
            frame,
            text=(
                "Connection values may be shared by multiple devices on the same "
                "bus. Changes are validated and backed up before saving."
            ),
            wraplength=650,
        ).grid(row=next_row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        next_row += 1

        def parsed_mapping(key: str) -> dict[str, object]:
            originals, variables = mapping_entries[key]
            return {
                name: _parse_existing_value(variables[name].get(), value)
                for name, value in originals.items()
            }

        def save() -> None:
            try:
                interval_text = interval.get().strip()
                interval_value = float(interval_text) if interval_text else None
                request = EditDeviceRequest(
                    device_id,
                    friendly_name.get(),
                    enabled.get(),
                    required.get(),
                    system.get(),
                    interval_value,
                    parsed_mapping("connection"),
                    parsed_mapping("device_connection"),
                    parsed_mapping("settings"),
                )
            except Exception as error:
                messagebox.showerror("Invalid value", str(error), parent=dialog)
                return
            result = self._view_model.update_device(request)
            self._record_result(result)
            if result.succeeded:
                dialog.destroy()
                self.refresh()

        ttk.Button(frame, text="Save device", command=save).grid(
            row=next_row, column=1, sticky="e", pady=(12, 0)
        )

    def _remove_selected_device(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            messagebox.showinfo(
                "Select a device",
                "Select a configured device before removing it.",
                parent=self._root,
            )
            return
        if not messagebox.askyesno(
            "Remove configured device",
            f"Remove {device_id!r} from this rig profile?\n\n"
            "A backup of the current profile will be created.",
            parent=self._root,
        ):
            return
        self._record_result(self._view_model.remove_device(device_id))
        self.refresh()

    def _edit_measurement_interval(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            messagebox.showinfo(
                "Select a device",
                "Select a configured device before editing its interval.",
                parent=self._root,
            )
            return

        dialog = tk.Toplevel(self._root)
        dialog.title("Edit measurement interval")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame,
            text=f"Measurement interval for {device_id}",
            font=SECTION_FONT,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(frame, text="Seconds:").grid(
            row=1, column=0, sticky="w", padx=(0, 10)
        )
        entry = ttk.Entry(frame, width=18)
        current = self._view_model.device_poll_interval(device_id)
        if current is not None:
            entry.insert(0, f"{current:g}")
        entry.grid(row=1, column=1, sticky="w")
        ttk.Label(
            frame,
            text=(
                "Enter a positive interval, for example 0.1 for ten readings "
                "per second. Leave blank to use the application default."
            ),
            wraplength=410,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 12))

        def save() -> None:
            text = entry.get().strip()
            try:
                interval = None if not text else float(text)
            except ValueError:
                messagebox.showerror(
                    "Invalid measurement interval",
                    "The interval must be a positive number of seconds.",
                    parent=dialog,
                )
                return
            result = self._view_model.update_measurement_interval(
                device_id,
                interval,
            )
            self._record_result(result)
            if not result.succeeded:
                messagebox.showerror(
                    "Interval not changed",
                    result.summary + "\n\n" + result.technical_details,
                    parent=dialog,
                )
                return
            self.refresh()
            messagebox.showinfo(
                "Measurement interval saved",
                result.summary,
                parent=dialog,
            )
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(buttons, text="Save", command=save).grid(row=0, column=1)
        entry.bind("<Return>", lambda _event: save())
        entry.focus_set()
        entry.selection_range(0, "end")

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
            "Hotplate": ("No direct hotplate drivers installed",),
        }
        unavailable = {
            "No direct peristaltic pump drivers installed",
            "No direct analytical instrument drivers installed",
            "No direct GC drivers installed",
            "No direct potentiostat drivers installed",
            "No direct hotplate drivers installed",
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
                self._open_add_keithley(
                    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER[selected]
                )
            elif selected == "ESP32 controller (auto-discover)":
                self._open_add_esp32()
            else:
                self._open_scan_alicat()

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

    def _open_add_esp32(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Discover ESP32 controller")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        known_ports = tuple(port.device for port in self._view_model.serial_ports())
        port = tk.StringVar(value=known_ports[0] if known_ports else "")
        baud_rate = tk.StringVar(value="115200")
        timeout = tk.StringVar(value="2.0")
        heartbeat_interval = tk.StringVar(value="2.0")
        fields = (
            ("COM port", port),
            ("Baud rate", baud_rate),
            ("Timeout (seconds)", timeout),
            ("Heartbeat interval (seconds)", heartbeat_interval),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=0, sticky="w")
            if variable is port:
                field = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=known_ports,
                    width=34,
                )
            else:
                field = ttk.Entry(frame, textvariable=variable, width=36)
            field.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        ttk.Label(
            frame,
            text=(
                "Discovery is read-only. The ESP32 reports its controller ID, "
                "sensor devices, channels and writable outputs."
            ),
            wraplength=500,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 6))
        status = ttk.Label(frame, text="Ready to scan.", wraplength=500)
        status.grid(row=5, column=0, columnspan=2, sticky="w")
        results: Queue[tuple[AddEsp32Request, object, Exception | None]] = Queue()

        def request_values() -> AddEsp32Request:
            return AddEsp32Request(
                port=port.get().strip(),
                baud_rate=int(baud_rate.get()),
                timeout_seconds=float(timeout.get()),
                heartbeat_interval_seconds=float(heartbeat_interval.get()),
            )

        def scan() -> None:
            try:
                request = request_values()
            except ValueError as error:
                messagebox.showerror("Invalid value", str(error), parent=dialog)
                return
            scan_button.configure(state="disabled")
            status.configure(text=f"Scanning {request.port}…")

            def worker() -> None:
                try:
                    discovery = self._view_model.scan_esp32(request)
                    results.put((request, discovery, None))
                except Exception as error:
                    results.put((request, None, error))

            Thread(target=worker, name="esp32-discovery", daemon=True).start()
            dialog.after(100, poll_result)

        def poll_result() -> None:
            if not dialog.winfo_exists():
                return
            try:
                request, discovery, error = results.get_nowait()
            except Empty:
                dialog.after(100, poll_result)
                return
            scan_button.configure(state="normal")
            if error is not None:
                status.configure(text=f"Discovery failed: {type(error).__name__}: {error}")
                return
            identity = discovery.identity
            capabilities = discovery.capabilities
            devices = capabilities.get("devices", [])
            outputs = capabilities.get("outputs", [])
            device_lines = [
                f"{item.get('label', item.get('id', 'unnamed'))} "
                f"({item.get('kind', 'unknown')})"
                for item in devices
                if isinstance(item, dict)
            ]
            output_lines = [
                str(item.get("name", "unnamed"))
                for item in outputs
                if isinstance(item, dict)
            ]
            summary = (
                f"Controller: {identity.get('controller_id', 'unknown')}\n"
                f"Firmware: {identity.get('firmware_version', 'unknown')}\n"
                f"Devices: {', '.join(device_lines) or 'none'}\n"
                f"Outputs: {', '.join(output_lines) or 'none'}"
            )
            status.configure(text=summary)
            dialog.destroy()
            self._open_esp32_discovery_selection(request, discovery)

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        scan_button = ttk.Button(buttons, text="Discover", command=scan)
        scan_button.grid(row=0, column=1)

    def _open_esp32_discovery_selection(self, request, discovery) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Select discovered ESP32 devices")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.geometry("850x500")
        dialog.minsize(720, 400)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        outer = ttk.Frame(dialog, padding=14)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        ttk.Label(
            outer,
            text="Discovered ESP32 devices",
            font=TITLE_FONT,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=(
                "Choose the supported devices to add and give each a useful "
                "display name. The controller is required for selected sensors."
            ),
            wraplength=760,
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))

        table = ttk.Frame(outer)
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(2, weight=1)
        for column, heading in enumerate(
            (
                "Add",
                "Reported item",
                "Display name",
                "Interval (s)",
                "Channels",
                "Support",
            )
        ):
            ttk.Label(table, text=heading, font=SECTION_FONT).grid(
                row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 6)
            )

        controller_id = str(
            discovery.identity.get("controller_id", "esp32_controller")
        )
        controller_name = tk.StringVar(value="Main ESP32 controller")
        controller_selected = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            table, variable=controller_selected, state="disabled"
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(table, text=controller_id).grid(
            row=1, column=1, sticky="w", padx=(0, 8)
        )
        ttk.Entry(table, textvariable=controller_name).grid(
            row=1, column=2, sticky="ew", padx=(0, 8), pady=3
        )
        outputs = discovery.capabilities.get("outputs", [])
        output_names = [
            str(item.get("name", "unnamed"))
            for item in outputs
            if isinstance(item, dict)
        ]
        ttk.Label(table, text="—").grid(
            row=1, column=3, sticky="w", padx=(0, 8)
        )
        ttk.Label(table, text=", ".join(output_names) or "—").grid(
            row=1, column=4, sticky="w", padx=(0, 8)
        )
        ttk.Label(table, text="Controller").grid(row=1, column=5, sticky="w")

        choices: list[
            tuple[str, tk.BooleanVar, tk.StringVar, tk.StringVar]
        ] = []
        devices = discovery.capabilities.get("devices", [])
        for row, item in enumerate(
            (value for value in devices if isinstance(value, dict)),
            start=2,
        ):
            device_id = str(item.get("id", "unnamed"))
            kind = str(item.get("kind", "unknown"))
            supported = kind in {"dht11", "lumel_re72"}
            selected = tk.BooleanVar(value=supported)
            display_name = tk.StringVar(
                value=str(item.get("label", device_id))
            )
            recommended_interval = item.get(
                "recommended_poll_interval_seconds",
                request.sensor_poll_interval_seconds,
            )
            try:
                interval_text = f"{float(recommended_interval):g}"
            except (TypeError, ValueError):
                interval_text = f"{request.sensor_poll_interval_seconds:g}"
            interval = tk.StringVar(value=interval_text)
            check = ttk.Checkbutton(table, variable=selected)
            if not supported:
                check.configure(state="disabled")
            check.grid(row=row, column=0, sticky="w")
            ttk.Label(table, text=f"{device_id} ({kind})").grid(
                row=row, column=1, sticky="w", padx=(0, 8)
            )
            name_entry = ttk.Entry(table, textvariable=display_name)
            name_entry.grid(row=row, column=2, sticky="ew", padx=(0, 8), pady=3)
            if not supported:
                name_entry.configure(state="disabled")
            interval_entry = ttk.Entry(table, textvariable=interval, width=10)
            interval_entry.grid(
                row=row, column=3, sticky="w", padx=(0, 8), pady=3
            )
            if not supported:
                interval_entry.configure(state="disabled")
            channels = item.get("channels", [])
            channel_names = [
                str(channel.get("name", "unnamed"))
                for channel in channels
                if isinstance(channel, dict)
            ]
            ttk.Label(table, text=", ".join(channel_names) or "—").grid(
                row=row, column=4, sticky="w", padx=(0, 8)
            )
            ttk.Label(
                table,
                text="Ready" if supported else "Driver not installed",
            ).grid(row=row, column=5, sticky="w")
            if supported:
                choices.append((device_id, selected, display_name, interval))

        ttk.Label(
            outer,
            text=(
                "Writable outputs are exposed by the controller. Unsupported "
                "reported devices remain visible but cannot be selected yet."
            ),
            wraplength=760,
        ).grid(row=3, column=0, sticky="w", pady=(12, 0))

        def add_selected() -> None:
            selected_names = {
                device_id: name.get()
                for device_id, selected, name, _interval in choices
                if selected.get()
            }
            try:
                selected_intervals = {
                    device_id: float(interval.get())
                    for device_id, selected, _name, interval in choices
                    if selected.get()
                }
            except ValueError:
                messagebox.showerror(
                    "Invalid measurement interval",
                    "Every selected sensor interval must be a positive number.",
                    parent=dialog,
                )
                return
            result = self._view_model.add_discovered_esp32(
                request,
                discovery,
                controller_friendly_name=controller_name.get(),
                selected_device_names=selected_names,
                selected_device_intervals=selected_intervals,
            )
            self._record_result(result)
            if result.succeeded:
                dialog.destroy()
                self.refresh()
            else:
                messagebox.showerror(
                    "Could not add ESP32",
                    result.technical_details or result.summary,
                    parent=dialog,
                )

        buttons = ttk.Frame(outer)
        buttons.grid(row=4, column=0, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(buttons, text="Add selected", command=add_selected).grid(
            row=0, column=1
        )

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


def _parse_existing_value(text: str, original: object) -> object:
    value = text.strip()
    if isinstance(original, bool):
        normalised = value.casefold()
        if normalised in {"true", "yes", "1", "on"}:
            return True
        if normalised in {"false", "no", "0", "off"}:
            return False
        raise ValueError(f"Expected True or False, received {text!r}")
    if isinstance(original, int):
        return int(value)
    if isinstance(original, float):
        return float(value)
    if isinstance(original, str):
        return text.strip()
    raise TypeError(f"Unsupported profile value {original!r}")
