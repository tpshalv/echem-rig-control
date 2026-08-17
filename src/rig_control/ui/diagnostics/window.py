import tkinter as tk
from tkinter import ttk
from datetime import datetime

from rig_control.device_factory import create_device_manager
from rig_control.rig_profile_loading import load_rig_profile

from rig_control.ui.diagnostics.model import (
    DiagnosticViewModel,
)


class DiagnosticWindow:
    """Tkinter diagnostic screen backed by DiagnosticViewModel."""

    def __init__(
        self,
        root: tk.Tk,
        view_model: DiagnosticViewModel,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._history: list[str] = []
        self._technical_details: list[str] = []

        self._configure_window()
        self._create_widgets()
        self.refresh()

    def _configure_window(self) -> None:
        self._root.title("Echem Rig Control — Diagnostics")
        self._root.geometry("850x550")
        self._root.minsize(700, 450)

        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=12)
        main.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(4, weight=1)

        title = ttk.Label(
            main,
            text="Device diagnostics",
            font=("Segoe UI", 16, "bold"),
        )
        title.grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 10),
        )

        self._device_table = ttk.Treeview(
            main,
            columns=("device_type", "status"),
            show="tree headings",
            selectmode="browse",
        )
        self._device_table.heading(
            "#0",
            text="Device ID",
        )
        self._device_table.heading(
            "device_type",
            text="Device type",
        )
        self._device_table.heading(
            "status",
            text="Status",
        )

        self._device_table.column(
            "#0",
            width=250,
            minwidth=150,
        )
        self._device_table.column(
            "device_type",
            width=260,
            minwidth=160,
        )
        self._device_table.column(
            "status",
            width=130,
            minwidth=100,
        )

        self._device_table.tag_configure(
            "ready",
            foreground="#087A28",
            background="#E6F4EA",
            font=("Segoe UI", 9, "bold"),
        )
        self._device_table.tag_configure(
            "disconnected",
            foreground="#555555",
            background="#F3F3F3",
            font=("Segoe UI", 9, "normal"),
        )

        self._device_table.tag_configure(
            "warning",
            foreground="#8A4B00",
            background="#FFF4CE",
            font=("Segoe UI", 9, "bold"),
        )

        self._device_table.tag_configure(
            "error",
            foreground="#B3261E",
            background="#FDE7E9",
            font=("Segoe UI", 9, "bold"),
        )

        self._device_table.grid(
            row=1,
            column=0,
            sticky="nsew",
        )

        buttons = ttk.Frame(main)
        buttons.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=10,
        )

        ttk.Button(
            buttons,
            text="Connect selected",
            command=self._connect_selected,
        ).grid(row=0, column=0, padx=(0, 8))

        ttk.Button(
            buttons,
            text="Disconnect selected",
            command=self._disconnect_selected,
        ).grid(row=0, column=1, padx=(0, 8))

        ttk.Button(
            buttons,
            text="Refresh",
            command=self.refresh,
        ).grid(row=0, column=2)

        details_label = ttk.Label(
            main,
            text="Diagnostic details",
            font=("Segoe UI", 10, "bold"),
        )
        details_label.grid(
            row=3,
            column=0,
            sticky="w",
            pady=(6, 4),
        )

        self._details = tk.Text(
            main,
            height=10,
            wrap="word",
            font=("Consolas", 9),
        )
        self._details.grid(
            row=4,
            column=0,
            sticky="nsew",
        )
        self._details.configure(state="disabled")

        details_buttons = ttk.Frame(main)
        details_buttons.grid(
            row=5,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Button(
            details_buttons,
            text="Copy diagnostic log",
            command=self._copy_diagnostic_log,
        ).grid(row=0, column=0)

    def refresh(self) -> None:
        selected = self._selected_device_id()

        for item in self._device_table.get_children():
            self._device_table.delete(item)

        for row in self._view_model.device_rows():
            self._device_table.insert(
                "",
                "end",
                iid=row.device_id,
                text=row.device_id,
                values=(
                    row.device_type,
                    row.status,
                ),
                tags=(row.status,),
            )

        if (
            selected is not None
            and self._device_table.exists(selected)
        ):
            self._device_table.selection_set(selected)

    def _connect_selected(self) -> None:
        device_id = self._require_selection()

        if device_id is None:
            return

        result = self._view_model.connect_device(device_id)
        self._display_result(result)
        self.refresh()

    def _disconnect_selected(self) -> None:
        device_id = self._require_selection()

        if device_id is None:
            return

        result = self._view_model.disconnect_device(device_id)
        self._display_result(result)
        self.refresh()

    def _require_selection(self) -> str | None:
        device_id = self._selected_device_id()

        if device_id is None:
            timestamp = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            self._history.append(
                f"[{timestamp}] Select a device from the table first."
            )
            self._set_details("\n".join(self._history))

        return device_id

    def _selected_device_id(self) -> str | None:
        selected_items = self._device_table.selection()

        if not selected_items:
            return None

        return selected_items[0]

    def _display_result(
        self,
        result: DiagnosticActionResult,
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        self._history.append(
            f"[{timestamp}] {result.summary}"
        )

        if result.technical_details:
            self._technical_details.append(
                f"[{timestamp}] {result.summary}\n"
                f"{result.technical_details}"
            )

        self._set_details("\n".join(self._history))

    def _set_details(self, text: str) -> None:
        self._details.configure(state="normal")
        self._details.delete("1.0", "end")
        self._details.insert("1.0", text)
        self._details.configure(state="disabled")

    def _copy_diagnostic_log(self) -> None:
        sections: list[str] = []

        if self._history:
            sections.append(
                "DIAGNOSTIC ACTION HISTORY\n"
                + "\n".join(self._history)
            )

        if self._technical_details:
            sections.append(
                "TECHNICAL ERROR DETAILS\n"
                + "\n\n".join(self._technical_details)
            )

        if sections:
            text = "\n\n".join(sections)
        else:
            text = "No diagnostic actions have been recorded."

        self._root.clipboard_clear()
        self._root.clipboard_append(text)
        self._root.update()



def main() -> None:
    root = tk.Tk()
    profile = load_rig_profile(
        "rig-profile.simulation.toml"
    )
    manager = create_device_manager(profile)    
    view_model = DiagnosticViewModel(manager)

    DiagnosticWindow(root, view_model)
    root.mainloop()


if __name__ == "__main__":
    main()