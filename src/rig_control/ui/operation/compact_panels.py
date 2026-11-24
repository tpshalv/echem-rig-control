"""Tk widgets for the v2 Operation screen: a compact quantity × device grid.

``QuantityGrid`` draws one ``SystemLayout`` (see ``compact_layout``).  It
builds its widgets only when lines or devices change; every other refresh
just updates text and colours, so a 1 Hz update is cheap.

Readings are plain text; a setting is accent-coloured and changes on a
click.  A value needing attention turns red (or amber), and hovering any
value shows its quality and the time it was read.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from rig_control.ui.common.theme_v2 import (
    ACCENT, BODY_FONT, BOLD_FONT, BORDER_SOFT, CAPTION_FONT, DANGER_SOFT, DISABLED_TEXT,
    DynamicToolTip, ERROR, FAINT, GOOD_SOFT, GOOD_TEXT, HEADING_FONT, MUTED, NOTICE_SOFT, NOTICE_TEXT, PANEL,
    SPLITTER, SURFACE, TEXT, VALUE_FONT,
)
from rig_control.ui.operation.compact_layout import (
    Cell, Line, SystemLayout, control_prefix, details, format_value, needs_attention, severity,
    unit_text,
)
from rig_control.ui.operation.model import OperationChannelRow

_SEVERITY_COLOURS = {"": TEXT, "warning": NOTICE_TEXT, "error": ERROR}
#: Width of each device's column, in pixels.
_COLUMN = 128
#: Editors whose value is a state (On/Off, a mode) rather than a number.
_STATE_EDITORS = frozenset({"boolean", "running", "mode", "direction"})


class ScrollArea(ttk.Frame):
    """A vertically scrolling frame; the scrollbar shows only when needed."""

    def __init__(self, parent: tk.Misc, *, background: str = PANEL) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._canvas = tk.Canvas(self, background=background, highlightthickness=0, borderwidth=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._bar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._bar.set)
        self.inner = tk.Frame(self._canvas, background=background)
        self._window = self._canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self._fit())
        self._canvas.bind("<Configure>", lambda _e: self._fit())
        for widget in (self._canvas, self.inner):
            widget.bind("<Enter>", lambda _e: self._wheel(True), add="+")
            widget.bind("<Leave>", lambda _e: self._wheel(False), add="+")

    def _fit(self) -> None:
        self._canvas.itemconfigure(self._window, width=self._canvas.winfo_width())
        self._canvas.configure(scrollregion=self._canvas.bbox("all") or (0, 0, 0, 0))
        needed = self.inner.winfo_reqheight() > self._canvas.winfo_height() > 1
        if needed and not self._bar.winfo_ismapped():
            self._bar.grid(row=0, column=1, sticky="ns")
        elif not needed and self._bar.winfo_ismapped():
            self._bar.grid_remove()
            self._canvas.yview_moveto(0)

    def _wheel(self, on: bool) -> None:
        if on:
            self._canvas.bind_all("<MouseWheel>", self._scroll)
        else:
            self._canvas.unbind_all("<MouseWheel>")

    def _scroll(self, event: tk.Event) -> None:
        if self._bar.winfo_ismapped():
            self._canvas.yview_scroll(int(-event.delta / 120), "units")


class _CellWidgets:
    """The labels of one cell, updated in place."""

    def __init__(self, frame: tk.Frame, value: tk.Label, extra: tk.Label | None,
                 control: tk.Label | None) -> None:
        self.frame, self.value, self.extra, self.control = frame, value, extra, control
        self.cell = Cell()


class QuantityGrid(tk.Frame):
    """Lines of one quantity across devices; settings change on a click."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_edit: Callable[[OperationChannelRow, tk.Widget], None],
        on_trend: Callable[[tuple[str, str]], None],
        title: str = "",
    ) -> None:
        super().__init__(parent, background=PANEL)
        self._on_edit = on_edit
        self._on_trend = on_trend
        self._title = title
        self._shape: tuple | None = None
        self._cells: dict[tuple[str, str], _CellWidgets] = {}
        self._headers: dict[str, tk.Label] = {}
        self._notes: dict[tuple[str, str], tk.Label] = {}
        self._note_rows: dict[tuple[str, str], OperationChannelRow] = {}
        self.layout: SystemLayout | None = None

    # --- building ---

    def show(self, layout: SystemLayout) -> None:
        if layout.shape != self._shape:
            self._build(layout)
            self._shape = layout.shape
        self.layout = layout
        self._update(layout)

    def _build(self, layout: SystemLayout) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._cells.clear()
        self._headers.clear()
        self._notes.clear()
        row = 0
        if self._title:
            tk.Label(self, text=self._title, font=HEADING_FONT, background=PANEL,
                     foreground=TEXT, anchor="w").grid(row=row, column=0, columnspan=99,
                                                       sticky="w", padx=8, pady=(8, 0))
            row += 1
        if layout.is_empty:
            tk.Label(self, text="No readings yet. Connect devices and start monitoring.",
                     background=PANEL, foreground=MUTED).grid(row=row, column=0, sticky="w",
                                                              padx=8, pady=8)
            return
        # Fixed-width columns kept to the left, so values sit near their labels;
        # the spare width goes to an empty last column.
        self.columnconfigure(0, weight=0, minsize=150)
        span = len(layout.devices) + 1
        for column, (device_id, name) in enumerate(layout.devices, start=1):
            self.columnconfigure(column, weight=0, uniform="device", minsize=_COLUMN)
            header = tk.Label(self, text=name, font=BOLD_FONT, background=PANEL,
                              foreground=MUTED, anchor="e", justify="right",
                              wraplength=_COLUMN - 10)
            header.grid(row=row, column=column, sticky="se", padx=(4, 10), pady=(6, 2))
            self._headers[device_id] = header
        self.columnconfigure(span, weight=1)
        row += 1
        tk.Frame(self, height=1, background=BORDER_SOFT).grid(
            row=row, column=0, columnspan=span, sticky="ew", padx=6)
        row += 1
        for line in layout.lines:
            self._build_line(line, layout, row)
            row += 1
            tk.Frame(self, height=1, background=SPLITTER).grid(
                row=row, column=0, columnspan=span, sticky="ew", padx=6)
            row += 1
        for note in layout.notes:
            label = tk.Label(self, background=PANEL, foreground=MUTED, font=CAPTION_FONT,
                             anchor="w", justify="left", wraplength=640)
            label.grid(row=row, column=0, columnspan=len(layout.devices) + 1, sticky="w",
                       padx=8, pady=(4, 0))
            self._notes[note.key] = label
            DynamicToolTip(label, lambda key=note.key: self._note_details(key))
            row += 1
        tk.Frame(self, height=6, background=PANEL).grid(row=row, column=0)

    def _build_line(self, line: Line, layout: SystemLayout, row: int) -> None:
        name = tk.Frame(self, background=PANEL)
        name.grid(row=row, column=0, sticky="w", padx=(8, 4), pady=2)
        tk.Label(name, text=line.label, background=PANEL, foreground=MUTED, font=BODY_FONT,
                 anchor="w").pack(side="left")
        if line.unit:
            tk.Label(name, text=unit_text(line.unit), background=PANEL, foreground=FAINT,
                     font=CAPTION_FONT, anchor="w").pack(side="left", padx=(5, 0), pady=(2, 0))
        for column, (device_id, _name) in enumerate(layout.devices, start=1):
            cell = line.cells.get(device_id)
            if cell is None:
                continue
            frame = tk.Frame(self, background=PANEL)
            frame.grid(row=row, column=column, sticky="e", padx=(4, 10), pady=2)
            top = tk.Frame(frame, background=PANEL)
            top.pack(anchor="e")
            extra = control = None
            # Reading and its setting on one line: "49.8  set 50".
            if cell.reading is not None and cell.control is not None:
                control = tk.Label(top, background=PANEL, font=CAPTION_FONT, anchor="e")
                control.pack(side="right", padx=(6, 0), pady=(3, 0))
            value = tk.Label(top, background=PANEL, font=VALUE_FONT, anchor="e")
            value.pack(side="right")
            if cell.reading is not None and (cell.reading.equivalent or not line.unit):
                extra = tk.Label(frame, background=PANEL, foreground=FAINT, font=CAPTION_FONT,
                                 anchor="e")
                extra.pack(anchor="e")
            widgets = _CellWidgets(frame, value, extra, control)
            key = (line.key, device_id)
            self._cells[key] = widgets
            self._bind_cell(key, widgets)

    def _bind_cell(self, key: tuple[str, str], widgets: _CellWidgets) -> None:
        def cell() -> Cell:
            return self._cells[key].cell if key in self._cells else Cell()

        def edit(row: OperationChannelRow | None, anchor: tk.Widget) -> None:
            if row is not None and row.writable:
                self._on_edit(row, anchor)

        if widgets.control is not None:
            # Value is a reading (double-click: trend); the small line below is its setting.
            widgets.value.bind("<Double-1>", lambda _e: self._trend(cell().reading))
            widgets.control.bind("<Button-1>", lambda _e: edit(cell().control, widgets.control))
            DynamicToolTip(widgets.control, lambda: details(cell().control) if cell().control else "")
        else:
            def click(_event: tk.Event) -> None:
                current = cell()
                if current.control is not None:
                    edit(current.control, widgets.value)
            widgets.value.bind("<Button-1>", click)
            widgets.value.bind("<Double-1>", lambda _e: self._trend(cell().reading))
        DynamicToolTip(widgets.value, lambda: details(cell().reading or cell().control)
                       if (cell().reading or cell().control) else "")

    def _trend(self, row: OperationChannelRow | None) -> None:
        if row is not None and not row.writable and isinstance(row.value, (int, float)) \
                and not isinstance(row.value, bool):
            self._on_trend(row.key)

    def _note_details(self, key: tuple[str, str]) -> str:
        row = self._note_rows.get(key)
        return "" if row is None else details(row)

    # --- updating ---

    def _update(self, layout: SystemLayout) -> None:
        for line in layout.lines:
            for device_id, cell in line.cells.items():
                widgets = self._cells.get((line.key, device_id))
                if widgets is not None:
                    widgets.cell = cell
                    self._paint(widgets, cell, line)
        for device_id, header in self._headers.items():
            rows = [row for line in layout.lines for cell in
                    [line.cells.get(device_id)] if cell is not None for row in cell.rows]
            level = needs_attention(rows)
            header.configure(foreground=_SEVERITY_COLOURS[level] if level else MUTED,
                             text=("● " if level else "") + dict(layout.devices)[device_id])
        self._note_rows = {row.key: row for row in layout.notes}
        for row in layout.notes:
            label = self._notes.get(row.key)
            if label is not None:
                # A note marked bad (e.g. control mode not verified) is worth reading.
                colour = ERROR if row.quality == "bad" else (
                    _SEVERITY_COLOURS[severity(row)] if severity(row) else MUTED)
                _set(label, text=f"{row.device_name}: {row.value}", foreground=colour)

    def _paint(self, widgets: _CellWidgets, cell: Cell, line: Line) -> None:
        main = cell.reading or cell.control
        assert main is not None
        level = severity(main)
        text = format_value(main)
        if cell.reading is None and cell.control is not None:
            self._paint_setting(widgets.value, cell.control, line)
        else:
            colour = FAINT if main.value is None else _SEVERITY_COLOURS[level]
            _set(widgets.value, text=text, foreground=colour, background=PANEL,
                 cursor="")
        if widgets.extra is not None and cell.reading is not None:
            parts = []
            if not line.unit and cell.reading.unit:
                parts.append(unit_text(cell.reading.unit))
            if cell.reading.equivalent:
                parts.append(f"= {cell.reading.equivalent}")
            _set(widgets.extra, text="  ".join(parts))
        if widgets.control is not None and cell.control is not None:
            control = cell.control
            unit = "" if control.unit == (cell.reading.unit if cell.reading else "") else f" {unit_text(control.unit)}"
            _set(widgets.control,
                 text=f"{control_prefix(control)} {format_value(control)}{unit}",
                 foreground=ACCENT if control.writable else DISABLED_TEXT,
                 cursor="hand2" if control.writable else "")

    @staticmethod
    def _paint_setting(label: tk.Label, row: OperationChannelRow, line: Line) -> None:
        """A setting with no reading of its own: On/Off as a pill, others as accent text."""
        text = format_value(row)
        cursor = "hand2" if row.writable else ""
        if row.editor in {"boolean", "running"} and isinstance(row.value, bool):
            on = row.value
            _set(label, text=f" {text} ", foreground=GOOD_TEXT if on else MUTED,
                 background=GOOD_SOFT if on else SURFACE, cursor=cursor)
            return
        if row.editor == "action":
            _set(label, text=f" {text} ", foreground=ACCENT if row.writable else DISABLED_TEXT,
                 background=SURFACE, cursor=cursor)
            return
        level = severity(row)
        if level == "error" and not row.writable:
            colour = ERROR
        elif row.writable:
            colour = ACCENT
        elif row.quality == "bad":
            colour = DISABLED_TEXT  # Not usable yet, e.g. no active area entered.
        else:
            colour = _SEVERITY_COLOURS[level] if level else (FAINT if row.value is None else TEXT)
        unit = "" if line.unit or not row.unit or row.editor in _STATE_EDITORS else f" {unit_text(row.unit)}"
        _set(label, text=text + unit, foreground=colour,
             background=(NOTICE_SOFT if level == "warning" else DANGER_SOFT)
             if level and not row.writable else PANEL,
             cursor=cursor)


def _set(widget: tk.Label, **options: object) -> None:
    """Configure only what changed; reconfiguring a label is not free at 1 Hz × many cells."""
    changed = {name: value for name, value in options.items() if str(widget.cget(name)) != str(value)}
    if changed:
        widget.configure(**changed)
