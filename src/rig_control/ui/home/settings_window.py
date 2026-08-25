import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rig_control.ui.common.theme import SECTION_FONT, TITLE_FONT
from rig_control.ui.home.model import HomeActionResult, HomeViewModel


class SettingsWindow:
    """Editor for application-wide preferences and their settings file."""

    _ADVANCED_KEYS = frozenset({"technical_log_path"})
    _HIGH_CURRENT_MODE_KEY = "power_supply_high_current_mode"
    _CEILING_KEY = "power_supply_wiring_current_ceiling_amps"

    def __init__(
        self,
        root: tk.Toplevel,
        view_model: HomeViewModel,
        *,
        on_change: callable | None = None,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._on_change = on_change
        self._entries: dict[str, ttk.Entry] = {}
        self._advanced_visible = False
        self._high_current_var = tk.BooleanVar(value=False)
        root.title("Application Settings")
        root.geometry("820x540")
        root.minsize(680, 430)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self._create_widgets()
        self.refresh()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=16)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)

        ttk.Label(main, text="Application settings", font=TITLE_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        file_frame = ttk.LabelFrame(main, text="Settings file", padding=10)
        file_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        file_frame.columnconfigure(0, weight=1)
        self._path_label = ttk.Label(file_frame, wraplength=570)
        self._path_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(file_frame, text="Choose…", command=self._choose_file).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(file_frame, text="Save as…", command=self._save_as).grid(
            row=0, column=2
        )

        ordinary = ttk.LabelFrame(main, text="General", padding=10)
        ordinary.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ordinary.columnconfigure(1, weight=1)
        rows = self._view_model.setting_rows()
        excluded_keys = self._ADVANCED_KEYS | {
            self._HIGH_CURRENT_MODE_KEY,
            self._CEILING_KEY,
        }
        general_rows = [row for row in rows if row.key not in excluded_keys]
        for index, row in enumerate(general_rows):
            self._add_setting_row(ordinary, index, row)

        power_supply = ttk.LabelFrame(
            main, text="Power supply safety", padding=10
        )
        power_supply.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        power_supply.columnconfigure(1, weight=1)
        high_current_row = next(
            row for row in rows if row.key == self._HIGH_CURRENT_MODE_KEY
        )
        ttk.Checkbutton(
            power_supply,
            text=high_current_row.label,
            variable=self._high_current_var,
            command=self._on_high_current_mode_toggled,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(
            power_supply, text=high_current_row.description, wraplength=560
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._ceiling_frame = ttk.Frame(power_supply)
        self._ceiling_frame.columnconfigure(1, weight=1)
        ceiling_row = next(row for row in rows if row.key == self._CEILING_KEY)
        self._add_setting_row(self._ceiling_frame, 0, ceiling_row)
        self._ceiling_frame.grid(row=2, column=0, columnspan=2, sticky="ew")

        self._advanced_button = ttk.Button(
            main, text="Show advanced / technical settings", command=self._toggle_advanced
        )
        self._advanced_button.grid(row=4, column=0, sticky="w")
        self._advanced = ttk.LabelFrame(main, text="Advanced / technical", padding=10)
        self._advanced.columnconfigure(1, weight=1)
        advanced_rows = [row for row in rows if row.key in self._ADVANCED_KEYS]
        for index, row in enumerate(advanced_rows):
            self._add_setting_row(self._advanced, index, row)

        buttons = ttk.Frame(main)
        buttons.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        ttk.Button(buttons, text="Apply", command=self._apply).grid(row=0, column=0)
        ttk.Button(buttons, text="Save", command=self._save).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(buttons, text="Close", command=self._root.destroy).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._status = ttk.Label(main, font=SECTION_FONT)
        self._status.grid(row=7, column=0, sticky="w", pady=(10, 0))

    def _add_setting_row(self, parent: ttk.LabelFrame, index: int, row) -> None:
        ttk.Label(parent, text=f"{row.label}:").grid(
            row=index, column=0, sticky="w", pady=4
        )
        entry = ttk.Entry(parent, width=58)
        entry.grid(row=index, column=1, sticky="ew", padx=8, pady=4)
        self._entries[row.key] = entry
        if row.key == "default_output_directory":
            ttk.Button(
                parent,
                text="Browse…",
                command=lambda field=entry: self._browse_directory(field),
            ).grid(row=index, column=2, pady=4)
        elif row.key == "technical_log_path":
            ttk.Button(
                parent,
                text="Browse…",
                command=lambda field=entry: self._browse_log_file(field),
            ).grid(row=index, column=2, pady=4)
        else:
            ttk.Label(parent, text=row.description, wraplength=250).grid(
                row=index, column=2, sticky="w", pady=4
            )

    def refresh(self) -> None:
        self._path_label.configure(
            text=f"{self._view_model.settings.friendly_name} — {self._view_model.settings_path}"
        )
        for row in self._view_model.setting_rows():
            entry = self._entries.get(row.key)
            if entry is None:
                continue
            entry.delete(0, "end")
            entry.insert(0, row.value)
        self._high_current_var.set(
            self._view_model.settings.power_supply_high_current_mode
        )
        self._update_ceiling_visibility()

    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced.grid(row=5, column=0, sticky="ew", pady=(8, 0))
            self._advanced_button.configure(text="Hide advanced / technical settings")
        else:
            self._advanced.grid_remove()
            self._advanced_button.configure(text="Show advanced / technical settings")

    def _on_high_current_mode_toggled(self) -> None:
        if self._high_current_var.get():
            confirmed = messagebox.askyesno(
                title="Enable High current mode",
                message=(
                    "This allows the manual power-supply current ceiling to "
                    "be raised above the normal 45 A wiring rating.\n\n"
                    "Only enable this if the cables in use are actually "
                    "rated for it.\n\nEnable High current mode?"
                ),
                icon="warning",
                parent=self._root,
            )
            if not confirmed:
                self._high_current_var.set(False)
        self._update_ceiling_visibility()

    def _update_ceiling_visibility(self) -> None:
        if self._high_current_var.get():
            self._ceiling_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        else:
            self._ceiling_frame.grid_remove()

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
        )
        if selected:
            self._show_result(self._view_model.select_settings(selected))
            self.refresh()

    def _browse_directory(self, entry: ttk.Entry) -> None:
        selected = filedialog.askdirectory(parent=self._root)
        if selected:
            entry.delete(0, "end")
            entry.insert(0, selected)

    def _browse_log_file(self, entry: ttk.Entry) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            initialfile="rig-control.log",
            defaultextension=".log",
            filetypes=(("Log files", "*.log"), ("All files", "*.*")),
        )
        if selected:
            entry.delete(0, "end")
            entry.insert(0, selected)

    def _apply(self) -> bool:
        values = {key: entry.get() for key, entry in self._entries.items()}
        values[self._HIGH_CURRENT_MODE_KEY] = (
            "true" if self._high_current_var.get() else "false"
        )
        result = self._view_model.apply_setting_text(values)
        self._show_result(result)
        if result.succeeded and self._on_change is not None:
            self._on_change()
        self.refresh()
        return result.succeeded

    def _save(self) -> None:
        if self._apply():
            self._show_result(self._view_model.save_settings())
            self.refresh()

    def _save_as(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            defaultextension=".toml",
            filetypes=(("TOML files", "*.toml"),),
        )
        if selected and self._apply():
            self._show_result(self._view_model.save_settings(Path(selected)))
            self.refresh()

    def _show_result(self, result: HomeActionResult) -> None:
        self._status.configure(text=result.summary)
        if not result.succeeded:
            messagebox.showerror("Settings", result.summary, parent=self._root)
