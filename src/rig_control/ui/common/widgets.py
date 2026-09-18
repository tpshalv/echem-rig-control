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

        self._scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self._canvas.yview,
        )
        self._scrollbar.grid(row=0, column=1, sticky="ns")
        self._scrollbar_visible = True
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

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
        self._refresh_scrollbar_visibility()

    def _match_content_width(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(
            self._content_window,
            width=event.width,
        )
        self._refresh_scrollbar_visibility()

    def _refresh_scrollbar_visibility(self) -> None:
        """Show the scrollbar only while content is actually taller than
        the visible area; hide it (rather than just leaving it inert) so
        a short dialog never carries a dead scroll track."""

        needed = self.content.winfo_reqheight()
        available = self._canvas.winfo_height()
        should_show = needed > available > 1

        if should_show and not self._scrollbar_visible:
            self._scrollbar.grid(row=0, column=1, sticky="ns")
            self._scrollbar_visible = True
        elif not should_show and self._scrollbar_visible:
            self._scrollbar.grid_remove()
            self._scrollbar_visible = False
            self._canvas.yview_moveto(0)

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

    def scroll_from_event(self, event: tk.Event) -> str | None:
        """Scroll this frame from a child that suppresses its own wheel action."""
        return self._on_mouse_wheel(event)


class HoverToolTip:
    """Small delayed help popup for compact forms."""

    def __init__(self, widget: tk.Misc, text: str, *, delay_ms: int = 450) -> None:
        self._widget = widget
        self._text = text
        self._delay_ms = delay_ms
        self._after_id: str | None = None
        self._popup: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay_ms, self._show)

    def _show(self) -> None:
        self._after_id = None
        if self._popup is not None or not self._text:
            return
        popup = tk.Toplevel(self._widget)
        popup.wm_overrideredirect(True)
        popup.wm_geometry(
            f"+{self._widget.winfo_pointerx() + 12}+"
            f"{self._widget.winfo_pointery() + 16}"
        )
        tk.Label(
            popup,
            text=self._text,
            justify="left",
            wraplength=360,
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=5,
            background="#ffffe0",
        ).pack()
        self._popup = popup

    def _cancel(self) -> None:
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None


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
