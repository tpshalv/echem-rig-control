import tkinter as tk
from tkinter import ttk

from rig_control.ui.common.theme import (
    SECTION_FONT,
    STATUS_TEXT_COLOURS,
)


class VerticalScrolledFrame(ttk.Frame):
    """A frame whose contents can scroll when they are too tall."""

    def __init__(
        self,
        parent: tk.Misc,
        **kwargs: object,
    ) -> None:
        super().__init__(parent, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self._canvas.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self.content = ttk.Frame(self._canvas)
        self.content.columnconfigure(0, weight=1)
        self._content_window = self._canvas.create_window(
            (0, 0),
            window=self.content,
            anchor="nw",
        )

        self.content.bind("<Configure>", self._update_scroll_region)
        self._canvas.bind("<Configure>", self._match_content_width)

        top_level = self.winfo_toplevel()
        top_level.bind("<MouseWheel>", self._on_mouse_wheel, add="+")
        top_level.bind("<Button-4>", self._on_mouse_wheel, add="+")
        top_level.bind("<Button-5>", self._on_mouse_wheel, add="+")

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _match_content_width(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(
            self._content_window,
            width=event.width,
        )

    def _on_mouse_wheel(self, event: tk.Event) -> str | None:
        """Scroll only when the pointer is inside this container."""

        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            widget = widget.master

        if widget is not self:
            return None

        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        elif event.delta > 0:
            direction = -1
        elif event.delta < 0:
            direction = 1
        else:
            return None

        self._canvas.yview_scroll(direction, "units")
        return "break"


def create_device_status_indicator(
    parent: tk.Misc,
    *,
    status: str,
    row: int = 0,
    column: int = 0,
    columnspan: int = 1,
) -> None:
    """Add a consistently styled device-status label to a grid."""

    colour = STATUS_TEXT_COLOURS.get(
        status,
        STATUS_TEXT_COLOURS["unknown"],
    )
    tk.Label(
        parent,
        text=f"●  {status.replace('_', ' ').title()}",
        foreground=colour,
        font=SECTION_FONT,
    ).grid(
        row=row,
        column=column,
        columnspan=columnspan,
        sticky="w",
    )
