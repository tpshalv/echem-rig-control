import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.ui.common.theme import SECTION_FONT
from rig_control.ui.common.widgets import VerticalScrolledFrame
from rig_control.ui.device_setup.types import EditDeviceRequest


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class DeviceEditDialogs(SetupDialog):
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
        labels_box = ttk.LabelFrame(frame, text="Signal labels", padding=8)
        labels_box.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        labels_box.columnconfigure(0, weight=1)
        ttk.Label(labels_box, text="One per line, for example: tc1 = Water bath").grid(row=0, column=0, sticky="w")
        channel_labels = tk.Text(labels_box, height=4, width=45)
        channel_labels.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        channel_labels.insert("1.0", "\n".join(f"{key} = {value}" for key, value in original.channel_labels.items()))
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
                    parse_channel_labels(channel_labels.get("1.0", "end")),
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


def parse_channel_labels(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        channel, separator, label = line.partition("=")
        channel, label = channel.strip(), label.strip()
        if not separator or not channel or not label:
            raise ValueError("Signal labels must use channel = label, one per line")
        if channel in labels:
            raise ValueError(f"Duplicate channel label: {channel}")
        labels[channel] = label
    return labels


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
