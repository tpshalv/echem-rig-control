"""The v2 look: compact, light, flat, one teal accent.

Used only when the app is started with ``--ui v2`` (see ``ui/home/window.py``);
the classic look in ``theme.py`` is untouched, so either can be run.

Two background levels give depth without boxes everywhere: a grey
``SURFACE`` behind everything and near-white ``PANEL`` cards for the
content.  Labels are muted and values dark.  The accent marks the one
thing to press or the thing selected; red is kept for problems and Stop.

Widgets opt into variants by style name::

    ttk.Button(..., style="Accent.TButton")   # the main action
    ttk.Button(..., style="Danger.TButton")   # safe state / stop
    ttk.Frame(..., style="Card.TFrame")       # a white card
    ttk.Label(..., style="Card.TLabel")       # text on a card
"""

import tkinter as tk
from tkinter import ttk

ACCENT = "#137d91"
ACCENT_DARK = "#0f6a7b"
ACCENT_DISABLED = "#a9cdd4"
ACCENT_SOFT = "#e3f1f3"
TEXT = "#1d2b36"
MUTED = "#5b6b77"
FAINT = "#8a98a3"
BORDER = "#c9d3db"
BORDER_SOFT = "#d6dee4"
SURFACE = "#f4f6f8"
PANEL = "#fbfcfd"
WHITE = "#ffffff"
HOVER = "#edf3f6"
HOVER_BORDER = "#9fb3c1"
PRESSED = "#dde8ee"
DISABLED_TEXT = "#9aa6ae"
SPLITTER = "#e3e8ec"
ERROR = "#a13b25"
DANGER = "#c62828"
DANGER_DARK = "#9e1f1f"
DANGER_SOFT = "#fde4e1"
DANGER_TEXT = "#7a1711"
NOTICE_SOFT = "#fff3d6"
NOTICE_TEXT = "#6b4700"
GOOD_TEXT = "#1f7a3a"
GOOD_SOFT = "#e4f3e8"

FAMILY = "Segoe UI"
BODY_FONT = (FAMILY, 9)
BOLD_FONT = (FAMILY, 9, "bold")
CAPTION_FONT = (FAMILY, 8)
HEADING_FONT = ("Segoe UI Semibold", 10)
TITLE_FONT = ("Segoe UI Semibold", 13)
VALUE_FONT = ("Segoe UI Semibold", 11)
BIG_VALUE_FONT = ("Segoe UI Semibold", 17)


def apply_v2_theme(root: tk.Misc) -> None:
    """Apply the v2 look to every ttk widget and the plain tk ones."""

    root.configure(background=SURFACE)
    # Fonts for the plain tk widgets only: a bare "*Font" would also set every
    # ttk widget's own font option, which overrides the styles below.
    for widget_class in ("Label", "Button", "Checkbutton", "Radiobutton", "Entry", "Listbox",
                         "Text", "Menu", "Menubutton", "Message", "Spinbox", "Labelframe",
                         "Scale"):
        root.option_add(f"*{widget_class}.Font", BODY_FONT)
    for pattern, value in (
        ("*Background", SURFACE),
        ("*Foreground", TEXT),
        ("*selectBackground", ACCENT),
        ("*selectForeground", WHITE),
        ("*Listbox.background", WHITE),
        ("*Listbox.relief", "solid"),
        ("*Listbox.borderWidth", 1),
        ("*Listbox.highlightThickness", 0),
        ("*Text.background", WHITE),
        ("*Text.relief", "solid"),
        ("*Text.borderWidth", 1),
        ("*TCombobox*Listbox.background", WHITE),
        ("*TCombobox*Listbox.selectBackground", ACCENT),
        ("*TCombobox*Listbox.selectForeground", WHITE),
    ):
        root.option_add(pattern, value)

    style = ttk.Style(root)
    # "clam" is the built-in theme that honours colours; the Windows native
    # themes ignore most of them.
    style.theme_use("clam")
    flat = {"lightcolor": SURFACE, "darkcolor": SURFACE}
    style.configure(
        ".", background=SURFACE, foreground=TEXT, font=BODY_FONT,
        bordercolor=BORDER, troughcolor=SURFACE, focuscolor=ACCENT,
        selectbackground=ACCENT, selectforeground=WHITE,
        fieldbackground=WHITE, insertcolor=TEXT, **flat,
    )
    style.map(".", foreground=[("disabled", DISABLED_TEXT)])

    style.configure("TFrame", background=SURFACE)
    style.configure("TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Caption.TLabel", foreground=MUTED, font=CAPTION_FONT)
    style.configure("Heading.TLabel", font=HEADING_FONT)
    style.configure("Title.TLabel", font=TITLE_FONT)
    style.configure("Error.TLabel", foreground=ERROR)

    # Cards: the near-white panels content sits on.
    style.configure("Card.TFrame", background=PANEL, bordercolor=BORDER_SOFT,
                    relief="solid", borderwidth=1)
    style.configure("Plain.Card.TFrame", background=PANEL, borderwidth=0, relief="flat")
    for name, extra in (
        ("Card.TLabel", {}),
        ("Muted.Card.TLabel", {"foreground": MUTED}),
        ("Caption.Card.TLabel", {"foreground": MUTED, "font": CAPTION_FONT}),
        ("Heading.Card.TLabel", {"font": HEADING_FONT}),
        ("Error.Card.TLabel", {"foreground": ERROR}),
    ):
        style.configure(name, background=PANEL, **extra)
    style.configure("Card.TCheckbutton", background=PANEL)
    style.configure("Card.TSeparator", background=BORDER_SOFT)

    # Labelled frames look like the Qt group boxes: a soft line and a muted title.
    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER_SOFT,
                    relief="solid", borderwidth=1, **flat)
    style.configure("TLabelframe.Label", background=SURFACE, foreground=MUTED,
                    font=BOLD_FONT)

    # clam gives every button room for 11 characters, so "Add" is as wide as
    # "Run read-only check"; buttons here fit their text instead.
    button = {"background": WHITE, "foreground": TEXT, "bordercolor": BORDER,
              "lightcolor": WHITE, "darkcolor": WHITE, "padding": (9, 3),
              "focuscolor": WHITE, "width": -4, "anchor": "center"}
    style.configure("TButton", **button)
    style.map(
        "TButton",
        background=[("disabled", SURFACE), ("pressed", PRESSED), ("active", HOVER)],
        lightcolor=[("disabled", SURFACE), ("pressed", PRESSED), ("active", HOVER)],
        darkcolor=[("disabled", SURFACE), ("pressed", PRESSED), ("active", HOVER)],
        bordercolor=[("disabled", BORDER_SOFT), ("active", HOVER_BORDER)],
        foreground=[("disabled", DISABLED_TEXT)],
    )
    for name, fill, dark, disabled in (
        ("Accent.TButton", ACCENT, ACCENT_DARK, ACCENT_DISABLED),
        ("Danger.TButton", DANGER, DANGER_DARK, "#e2a3a3"),
    ):
        style.configure(name, background=fill, foreground=WHITE, bordercolor=dark,
                        lightcolor=fill, darkcolor=fill, font=BOLD_FONT, focuscolor=fill)
        style.map(
            name,
            background=[("disabled", disabled), ("pressed", dark), ("active", dark)],
            lightcolor=[("disabled", disabled), ("pressed", dark), ("active", dark)],
            darkcolor=[("disabled", disabled), ("pressed", dark), ("active", dark)],
            bordercolor=[("disabled", disabled)],
            foreground=[("disabled", WHITE)],
        )
    style.configure("Small.TButton", padding=(6, 1), font=CAPTION_FONT)
    style.configure("Link.TButton", background=PANEL, bordercolor=PANEL, lightcolor=PANEL,
                    darkcolor=PANEL, foreground=ACCENT, padding=(2, 0), focuscolor=PANEL)
    style.map("Link.TButton", background=[("active", HOVER)], lightcolor=[("active", HOVER)],
              darkcolor=[("active", HOVER)], bordercolor=[("active", HOVER)])

    field = {"fieldbackground": WHITE, "bordercolor": BORDER, "lightcolor": WHITE,
             "darkcolor": WHITE, "arrowcolor": MUTED, "padding": (4, 2)}
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, **field)
        style.map(
            name,
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", ACCENT_SOFT)],
            fieldbackground=[("disabled", SURFACE), ("readonly", WHITE)],
            foreground=[("disabled", DISABLED_TEXT)],
        )
    # A read-only combobox keeps its text plain rather than highlighted blue.
    style.map("TCombobox", selectbackground=[("readonly", WHITE)],
              selectforeground=[("readonly", TEXT)])
    style.configure("TCheckbutton", background=SURFACE, indicatorbackground=WHITE,
                    indicatorforeground=ACCENT)
    style.configure("TRadiobutton", background=SURFACE, indicatorbackground=WHITE,
                    indicatorforeground=ACCENT)

    style.configure("TNotebook", background=SURFACE, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED, padding=(12, 5),
                    bordercolor=SURFACE, font=BOLD_FONT, **flat)
    style.map(
        "TNotebook.Tab",
        background=[("selected", PANEL), ("active", HOVER)],
        foreground=[("selected", ACCENT), ("active", TEXT)],
        bordercolor=[("selected", BORDER_SOFT)],
        lightcolor=[("selected", PANEL)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=TEXT,
                    rowheight=22, bordercolor=BORDER_SOFT, font=BODY_FONT, **flat)
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", WHITE)])
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED,
                    font=(FAMILY, 8, "bold"), relief="flat", bordercolor=BORDER_SOFT, **flat)
    style.map("Treeview.Heading", background=[("active", HOVER)])

    style.configure("TScrollbar", background=BORDER_SOFT, troughcolor=SURFACE,
                    bordercolor=SURFACE, arrowcolor=MUTED, gripcount=0,
                    lightcolor=BORDER_SOFT, darkcolor=BORDER_SOFT)
    style.map("TScrollbar", background=[("active", HOVER_BORDER)],
              lightcolor=[("active", HOVER_BORDER)], darkcolor=[("active", HOVER_BORDER)])
    style.configure("TProgressbar", background=ACCENT, troughcolor=WHITE,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)
    style.configure("TSeparator", background=BORDER_SOFT)
    style.configure("TPanedwindow", background=SURFACE)
    style.configure("Sash", sashthickness=6, gripcount=0, background=SPLITTER)

    # Text boxes start narrower (screens that want a wide one stretch it), and
    # help text that would be cut off wraps instead, on every screen.
    root.option_add("*TEntry.width", 14)
    for label_class in ("TLabel", "Label"):
        root.bind_class(label_class, "<Configure>", lambda e: _wrap_if_cut_off(e.widget), add="+")
    for window_class in ("Tk", "Toplevel"):
        root.bind_class(window_class, "<Map>", _check_window_labels, add="+")


def _check_window_labels(event: tk.Event) -> None:
    """Once a window is on screen at its real size, check every label in it once."""
    window = event.widget
    if not isinstance(window, (tk.Tk, tk.Toplevel)):
        return

    def walk(widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            if child.winfo_class() in ("TLabel", "Label"):
                _wrap_if_cut_off(child)
            elif not isinstance(child, tk.Toplevel):
                walk(child)

    window.after(60, lambda: window.winfo_exists() and walk(window))


def _wrap_if_cut_off(label: tk.Misc) -> None:
    """Wrap a label to the width it was given when its text would not fit.

    Many screens give explanations a fixed wrap width or none, so a narrower
    window cut them off mid-word.  Only ever narrows the wrap, so it settles;
    nothing happens until the window is shown, since while a window is being
    built Tk lays it out at a tiny size first.
    """
    try:
        if not label.winfo_toplevel().winfo_ismapped():
            return
        if not str(label.cget("text")) or str(label.cget("image")):
            return
        width = label.winfo_width()
        if width < 80 or label.winfo_reqwidth() <= width + 2:
            return
        wrap = str(label.cget("wraplength"))
        current = int(float(wrap)) if wrap not in ("", "0") else 0
        target = width - 6
        if current and current <= target + 4:
            return
        label.configure(wraplength=target, justify="left")
    except (tk.TclError, ValueError):
        return


class DynamicToolTip:
    """A hover popup whose text is asked for each time it opens.

    ``text`` returns the current text, or "" for no popup, so a value that
    changes every second still shows its latest details.
    """

    def __init__(self, widget: tk.Misc, text, *, delay_ms: int = 350) -> None:
        self._widget = widget
        self._text = text
        self._delay_ms = delay_ms
        self._after_id: str | None = None
        self._popup: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, _event: tk.Event) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay_ms, self._show)

    def _show(self) -> None:
        self._after_id = None
        text = self._text()
        if self._popup is not None or not text:
            return
        popup = tk.Toplevel(self._widget)
        popup.wm_overrideredirect(True)
        popup.wm_geometry(
            f"+{self._widget.winfo_pointerx() + 12}+{self._widget.winfo_pointery() + 16}"
        )
        tk.Label(
            popup, text=text, justify="left", wraplength=380, background=WHITE,
            foreground=TEXT, relief="solid", borderwidth=1, padx=7, pady=4,
            highlightthickness=0, font=BODY_FONT,
        ).pack()
        self._popup = popup

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None
