"""New electrolyser and edit-details forms.

A new build starts from the builder's most recent electrolyser, so only
what changed needs typing; its name and number are suggested. Nothing but
the name is compulsory.
"""

import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from tkinter import simpledialog, ttk

from rig_control.data.electrolysers import (
    DETAIL_FIELDS,
    Electrolyser,
    create_electrolyser,
    latest_by,
    list_electrolysers,
    suggest_name,
    update_electrolyser,
)
from rig_control.ui.common.theme import ERROR_TEXT, MUTED_TEXT, SECTION_FONT

_BLANK = "(start blank)"
_LABELS = dict(DETAIL_FIELDS)


class ElectrolyserDialog:
    """``on_saved`` gets the saved electrolyser; nothing is called on Cancel.

    With ``existing`` the form edits that electrolyser and logs the change;
    otherwise it creates a new one in ``root_directory``.
    """

    def __init__(
        self,
        parent: tk.Misc,
        root_directory: str | Path,
        *,
        operator: str,
        on_saved: Callable[[Electrolyser], None],
        existing: Electrolyser | None = None,
        today: date | None = None,
    ) -> None:
        self._root_directory = Path(root_directory)
        self._operator = operator.strip()
        self._on_saved = on_saved
        self._existing = existing
        self._today = today or date.today()
        self._all = {item.name: item for item in list_electrolysers(root_directory, include_retired=True)}
        self._suggested = ""
        self._detail_entries: dict[str, ttk.Entry] = {}

        top = self.window = tk.Toplevel(parent)
        top.transient(parent.winfo_toplevel())
        top.title(f"Electrolyser {existing.name}" if existing else "New electrolyser")
        top.resizable(False, False)
        body = ttk.Frame(top, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        self._builder = self._entry(body, 0, "Builder initials")
        self._built_on = self._entry(body, 1, "Build date")
        ttk.Label(body, text="YYYY-MM-DD", foreground=MUTED_TEXT).grid(row=1, column=2, sticky="w")
        ttk.Label(body, text="Name:").grid(row=2, column=0, sticky="w", pady=2)
        if existing is None:
            self._name = ttk.Entry(body, width=28)
            self._name.grid(row=2, column=1, sticky="ew", padx=(8, 4), pady=2)
            ttk.Label(body, text="suggested; editable", foreground=MUTED_TEXT).grid(
                row=2, column=2, sticky="w")
            ttk.Label(body, text="Copy details from:").grid(row=3, column=0, sticky="w", pady=2)
            self._source = ttk.Combobox(body, state="readonly", width=26,
                                        values=(_BLANK, *sorted(self._all)))
            self._source.grid(row=3, column=1, sticky="ew", padx=(8, 4), pady=2)
            self._source.bind("<<ComboboxSelected>>", lambda _event: self._fill_details())
        else:
            ttk.Label(body, text=existing.name, font=SECTION_FONT).grid(
                row=2, column=1, sticky="w", padx=(8, 4), pady=2)

        ttk.Separator(body).grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        self._details = ttk.Frame(body)
        self._details.grid(row=5, column=0, columnspan=3, sticky="ew")
        self._details.columnconfigure(1, weight=1)
        ttk.Button(body, text="Add field…", command=self._add_field).grid(
            row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Label(body, text="No field is compulsory.", foreground=MUTED_TEXT).grid(
            row=6, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        next_row = 7
        if existing is not None:
            ttk.Separator(body).grid(row=next_row, column=0, columnspan=3, sticky="ew", pady=8)
            self._change = self._entry(body, next_row + 1, "What changed")
            ttk.Label(body, text="kept in the history", foreground=MUTED_TEXT).grid(
                row=next_row + 1, column=2, sticky="w")
            self._retired = tk.BooleanVar(master=top, value=existing.retired)
            ttk.Checkbutton(body, text="Retired (hidden from the list; its number stays taken)",
                            variable=self._retired).grid(
                row=next_row + 2, column=0, columnspan=3, sticky="w", pady=(4, 0))
            next_row += 3
        self._error = ttk.Label(body, foreground=ERROR_TEXT, wraplength=420)
        self._error.grid(row=next_row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=next_row + 1, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Save", command=self._save, default="active").pack(side="left", padx=3)
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side="left")
        top.bind("<Escape>", lambda _event: self.close())
        top.protocol("WM_DELETE_WINDOW", self.close)

        if existing is None:
            self._builder.insert(0, self._operator)
            self._built_on.insert(0, self._today.isoformat())
            self._auto_source = ""
            self._source.set("")
            self._update_name()
            self._choose_source()
            for entry in (self._builder, self._built_on):
                entry.bind("<KeyRelease>", lambda _event: self._builder_or_date_typed())
        else:
            self._builder.insert(0, existing.builder)
            self._built_on.insert(0, existing.built_on.isoformat() if existing.built_on else "")
            self._show_details(existing.details)
        top.grab_set()

    def _entry(self, parent: ttk.Frame, row: int, label: str) -> ttk.Entry:
        ttk.Label(parent, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, width=28)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 4), pady=2)
        return entry

    # --- new builds ---

    def _choose_source(self) -> None:
        """Copy from the builder's latest build, unless another was picked."""
        if self._source.get() != self._auto_source:
            return
        source = latest_by(list(self._all.values()), self._builder.get())
        self._auto_source = source.name if source else _BLANK
        if self._source.get() != self._auto_source:
            self._source.set(self._auto_source)
            self._fill_details()

    def _builder_or_date_typed(self) -> None:
        self._update_name()
        self._choose_source()

    def _update_name(self) -> None:
        """Replace the name with a new suggestion unless it has been edited."""
        if self._name.get() != self._suggested:
            return
        try:
            built_on = date.fromisoformat(self._built_on.get().strip())
            suggestion = suggest_name(self._root_directory, self._builder.get(), built_on)
        except ValueError:
            suggestion = ""
        self._name.delete(0, "end")
        self._name.insert(0, suggestion)
        self._suggested = suggestion

    def _fill_details(self) -> None:
        source = self._all.get(self._source.get())
        if source is None:
            self._show_details({})
        else:
            self._show_details(source.copied_as("", "", self._today).details)

    # --- details ---

    def _show_details(self, details: dict[str, str]) -> None:
        for child in self._details.winfo_children():
            child.destroy()
        self._detail_entries = {}
        keys = [key for key, _label in DETAIL_FIELDS]
        keys += [key for key in details if key not in _LABELS]
        for key in keys:
            self._add_detail_row(key, details.get(key, ""))

    def _add_detail_row(self, key: str, value: str) -> None:
        row = len(self._detail_entries)
        ttk.Label(self._details, text=f"{_LABELS.get(key, key)}:").grid(
            row=row, column=0, sticky="w", pady=1)
        entry = ttk.Entry(self._details, width=40)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=1)
        entry.insert(0, value)
        self._detail_entries[key] = entry

    def _add_field(self) -> None:
        name = simpledialog.askstring("Add field", "Field name (e.g. Membrane batch):",
                                      parent=self.window)
        name = (name or "").strip()
        if not name:
            return
        if name in self._detail_entries or name in _LABELS.values():
            self._error.configure(text=f"{name} is already in the form.")
            return
        self._add_detail_row(name, "")
        self._detail_entries[name].focus_set()

    def details(self) -> dict[str, str]:
        """Filled-in fields only; a blank field is simply not recorded."""
        return {key: entry.get().strip() for key, entry in self._detail_entries.items()
                if entry.get().strip()}

    # --- saving ---

    def _save(self) -> None:
        text = self._built_on.get().strip()
        try:
            built_on = date.fromisoformat(text) if text else None
        except ValueError:
            self._error.configure(text="Enter the build date as YYYY-MM-DD, or leave it blank.")
            return
        builder = self._builder.get().strip()
        try:
            if self._existing is None:
                source = self._all.get(self._source.get())
                saved = Electrolyser(
                    name=self._name.get().strip(), builder=builder, built_on=built_on,
                    details=self.details(), copied_from=source.name if source else "",
                )
                folder = create_electrolyser(self._root_directory, saved)
                saved = replace(saved, name=folder.name)
            else:
                saved = update_electrolyser(
                    self._root_directory,
                    replace(self._existing, builder=builder, built_on=built_on,
                            details=self.details(), retired=self._retired.get()),
                    by=self._operator, change=self._change.get(),
                )
        except (OSError, ValueError) as error:
            self._error.configure(text=str(error))
            return
        self.close()
        self._on_saved(saved)

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.grab_release()
            self.window.destroy()
