"""Ask which period of the live graph data to save, and where."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, ttk

from rig_control.ui.operation.trend_window import TIME_RANGES

_WRAP = 420


class GraphDataDialog:
    """Modal: ``on_save(period, folder)`` when the operator chooses Save."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_folder: str,
        on_save: Callable[[str, str], None],
        initial_period: str = "Whole run",
    ) -> None:
        self._on_save = on_save
        top = self._top = tk.Toplevel(parent)
        top.transient(parent.winfo_toplevel())
        top.title("Save graph data")
        top.resizable(False, False)
        body = ttk.Frame(top, padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, wraplength=_WRAP, justify="left", text=(
            "Saves the readings the graphs hold in memory, whether or not you were "
            "recording, as CSV files in a new dated folder. The last 8 hours are every "
            "reading; anything older is thinned to the highs and lows (marked "
            "\"thinned\"). Values are in the display units.")
                  ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(body, text="Period:").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self._period = tk.StringVar(value=initial_period if initial_period in TIME_RANGES else "Whole run")
        ttk.Combobox(body, textvariable=self._period, values=tuple(TIME_RANGES),
                     state="readonly", width=16).grid(row=1, column=1, sticky="w")

        ttk.Label(body, text="Save in:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self._folder = ttk.Entry(body, width=46)
        self._folder.insert(0, initial_folder)
        self._folder.grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(body, text="Browse…", command=self._browse).grid(row=2, column=2, padx=(6, 0), pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Save", command=self._save, default="active").pack(side="left", padx=3)
        ttk.Button(buttons, text="Cancel", command=self._close).pack(side="left")
        top.bind("<Return>", lambda _e: self._save())
        top.bind("<Escape>", lambda _e: self._close())
        top.protocol("WM_DELETE_WINDOW", self._close)
        top.grab_set()

    def _browse(self) -> None:
        selected = filedialog.askdirectory(parent=self._top, initialdir=self._folder.get() or None,
                                           title="Choose where to save the graph data")
        if selected:
            self._folder.delete(0, "end")
            self._folder.insert(0, selected)

    def _save(self) -> None:
        folder = self._folder.get().strip()
        if not folder:
            self._browse()
            folder = self._folder.get().strip()
            if not folder:
                return
        period = self._period.get()
        self._close()
        self._on_save(period, folder)

    def _close(self) -> None:
        if self._top.winfo_exists():
            self._top.grab_release()
            self._top.destroy()
