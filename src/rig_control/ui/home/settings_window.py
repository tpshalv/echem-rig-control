import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rig_control.ui.common.theme import SECTION_FONT, TITLE_FONT
from rig_control.ui.common.widgets import VerticalScrolledFrame
from rig_control.ui.instrument_settings.catalog import create_instrument_panels
from rig_control.ui.home.model import HomeActionResult, HomeViewModel


class SettingsWindow:
    """Host application preferences and independent instrument settings editors."""

    _HIGH_CURRENT_MODE_KEY = "power_supply_high_current_mode"
    _CEILING_KEY = "power_supply_wiring_current_ceiling_amps"

    # Ordered (category label, setting keys) - one section per category,
    # picked from the sidebar. Add a new category here, or add a key to an
    # existing one, to grow the settings screen without needing to touch
    # its layout code.
    _CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("General", ("publish_interval_seconds",)),
        (
            "File save locations",
            (
                "default_output_directory",
                "export_bin_seconds",
                "live_export_interval_seconds",
                "technical_log_path",
            ),
        ),
        ("Graphical", ("trend_history_readings",)),
        (
            "Power supply safety",
            (
                _HIGH_CURRENT_MODE_KEY,
                _CEILING_KEY,
                "power_supply_default_current_amps",
                "power_supply_default_voltage_volts",
            ),
        ),
    )

    def __init__(
        self,
        root: tk.Toplevel,
        view_model: HomeViewModel,
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._on_change = on_change
        self._entries: dict[str, ttk.Entry] = {}
        self._category_frames: dict[str, ttk.Frame] = {}
        self._high_current_var = tk.BooleanVar(value=False)
        root.title("Settings")
        root.geometry("820x560")
        root.minsize(700, 420)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self._create_widgets()
        self.refresh()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=16)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        ttk.Label(main, text="Settings", font=TITLE_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        file_frame = ttk.LabelFrame(main, text="Application settings file", padding=10)
        self._file_frame = file_frame
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

        body = ttk.Frame(main)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Treeview(
            body,
            columns=(),
            show="tree",
            selectmode="browse",
            height=len(self._CATEGORIES),
        )
        sidebar.column("#0", width=170, stretch=False)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        sidebar.insert("", "end", iid="application", text="Application", open=True)
        sidebar.insert("", "end", iid="instruments", text="Instruments", open=True)
        for category, _keys in self._CATEGORIES:
            sidebar.insert("application", "end", iid=category, text=category)
        sidebar.bind("<<TreeviewSelect>>", self._on_category_selected)
        self._sidebar = sidebar

        detail_scroll = VerticalScrolledFrame(body)
        self._detail_scroll = detail_scroll
        detail_scroll.grid(row=0, column=1, sticky="nsew")
        detail_scroll.content.columnconfigure(0, weight=1)

        rows = self._view_model.setting_rows()
        rows_by_key = {row.key: row for row in rows}
        for category, keys in self._CATEGORIES:
            frame = ttk.Frame(detail_scroll.content, padding=(4, 0))
            frame.grid(row=0, column=0, sticky="new")
            frame.columnconfigure(1, weight=1)
            self._category_frames[category] = frame
            self._build_category(frame, [rows_by_key[key] for key in keys])

        self._instrument_panels = create_instrument_panels(
            detail_scroll.content, lambda: self._view_model.instrument_settings,
            detail_scroll.scroll_from_event,
        )
        for label, panel in self._instrument_panels.items():
            sidebar.insert("instruments", "end", iid=label, text=label)
            panel.grid(row=0, column=0, sticky="new")
            self._category_frames[label] = panel

        sidebar.selection_set(self._CATEGORIES[0][0])

        buttons = ttk.Frame(main)
        buttons.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        ttk.Button(buttons, text="Apply", command=self._apply).grid(row=0, column=0)
        self._save_button = ttk.Button(buttons, text="Save", command=self._save)
        self._save_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text="Close", command=self._root.destroy).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._status = ttk.Label(main, font=SECTION_FONT)
        self._status.grid(row=4, column=0, sticky="w", pady=(10, 0))
        self._on_category_selected(None)

    def _build_category(self, frame: ttk.Frame, rows: list) -> None:
        index = 0
        for row in rows:
            if row.key == self._HIGH_CURRENT_MODE_KEY:
                ttk.Checkbutton(
                    frame,
                    text=row.label,
                    variable=self._high_current_var,
                    command=self._on_high_current_mode_toggled,
                ).grid(row=index, column=0, columnspan=2, sticky="w", pady=4)
                index += 1
                ttk.Label(frame, text=row.description, wraplength=480).grid(
                    row=index, column=0, columnspan=2, sticky="w", pady=(0, 4)
                )
                index += 1
            elif row.key == self._CEILING_KEY:
                self._ceiling_frame = ttk.Frame(frame)
                self._ceiling_frame.columnconfigure(1, weight=1)
                self._add_setting_row(self._ceiling_frame, 0, row)
                self._ceiling_row_index = index
                index += 1
            else:
                self._add_setting_row(frame, index, row)
                index += 1

    def _add_setting_row(self, parent: ttk.Frame, index: int, row) -> None:
        ttk.Label(parent, text=f"{row.label}:").grid(
            row=index, column=0, sticky="w", pady=4
        )
        entry = ttk.Entry(parent, width=48)
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
        for panel in self._instrument_panels.values():
            panel.refresh()

    def _on_category_selected(self, _event: tk.Event) -> None:
        selection = self._sidebar.selection()
        if not selection:
            return
        selected = selection[0]
        children = self._sidebar.get_children(selected)
        if children:
            selected = children[0]
            self._sidebar.selection_set(selected)
        if selected not in self._category_frames:
            return
        if selected in self._instrument_panels:
            self._file_frame.grid_remove()
            self._save_button.grid_remove()
        else:
            self._file_frame.grid()
            self._save_button.grid()
        for category, frame in self._category_frames.items():
            if category == selected:
                frame.grid(row=0, column=0, sticky="new")
            else:
                frame.grid_remove()

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
            self._ceiling_frame.grid(
                row=self._ceiling_row_index, column=0, columnspan=2, sticky="ew"
            )
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
        selection = self._sidebar.selection()
        if selection and selection[0] in self._instrument_panels:
            return self._instrument_panels[selection[0]].apply_changes()
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
