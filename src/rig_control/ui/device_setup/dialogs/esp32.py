import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk

from rig_control.ui.common.theme import SECTION_FONT, TITLE_FONT
from rig_control.ui.device_setup.types import AddEsp32Request


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class Esp32Dialogs(SetupDialog):
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
