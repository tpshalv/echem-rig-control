"""Shared visual theme for all rig-control screens."""

# Fonts

TITLE_FONT = ("Segoe UI", 16, "bold")
BANNER_FONT = ("Segoe UI", 11, "bold")
SECTION_FONT = ("Segoe UI", 10, "bold")
BODY_FONT = ("Segoe UI", 9, "normal")
BODY_BOLD_FONT = ("Segoe UI", 9, "bold")
MONOSPACE_FONT = ("Consolas", 9)


# Semantic text and background colours

SUCCESS_TEXT = "#087A28"
SUCCESS_BACKGROUND = "#E6F4EA"

WARNING_TEXT = "#8A4B00"
WARNING_BACKGROUND = "#FFF4CE"

ERROR_TEXT = "#B3261E"
ERROR_BACKGROUND = "#FDE7E9"

INFO_TEXT = "#1769AA"

MUTED_TEXT = "#555555"
UNKNOWN_TEXT = "#777777"
NEUTRAL_BACKGROUND = "#F3F3F3"

DANGER_BUTTON_BACKGROUND = "#B71C1C"
DANGER_BUTTON_ACTIVE_BACKGROUND = "#8E0000"


# Device statuses

STATUS_TEXT_COLOURS = {
    "ready": SUCCESS_TEXT,
    "connected": SUCCESS_TEXT,
    "degraded": WARNING_TEXT,
    "connecting": INFO_TEXT,
    "faulted": ERROR_TEXT,
    "disconnected": MUTED_TEXT,
    "unknown": UNKNOWN_TEXT,
}


PAPER_BACKGROUND = "#EEF3F7"
NAVY_INK = "#16253A"
HAIRLINE = "#A9B8C6"


def apply_blueprint_theme(root) -> None:
    """Apply the shared flat blueprint appearance to the whole application."""

    from tkinter import ttk

    root.configure(background=PAPER_BACKGROUND)
    root.option_add("*Font", BODY_FONT)
    root.option_add("*Background", PAPER_BACKGROUND)
    root.option_add("*Foreground", NAVY_INK)
    root.option_add("*Listbox.background", PAPER_BACKGROUND)
    root.option_add("*Text.background", PAPER_BACKGROUND)
    style = ttk.Style(root)
    style.configure("TFrame", background=PAPER_BACKGROUND)
    style.configure("TLabel", background=PAPER_BACKGROUND, foreground=NAVY_INK)
    style.configure("TLabelframe", background=PAPER_BACKGROUND,
                    foreground=NAVY_INK, borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=PAPER_BACKGROUND,
                    foreground=NAVY_INK, font=("Segoe UI", 8, "bold"))
    style.configure("TButton", font=BODY_FONT, padding=(8, 5), relief="flat")
    style.configure("TEntry", fieldbackground="#F8FAFC", foreground=NAVY_INK)
    style.configure("TCombobox", fieldbackground="#F8FAFC", foreground=NAVY_INK)
    style.configure("TSpinbox", fieldbackground="#F8FAFC", foreground=NAVY_INK)
    style.configure("TNotebook", background=PAPER_BACKGROUND, borderwidth=0)
    style.configure("TNotebook.Tab", font=("Segoe UI", 8, "bold"), padding=(12, 6))
    style.configure("Treeview", background=PAPER_BACKGROUND,
                    fieldbackground=PAPER_BACKGROUND, foreground=NAVY_INK,
                    rowheight=25, font=BODY_FONT)
    style.configure("Treeview.Heading", background=PAPER_BACKGROUND,
                    foreground=NAVY_INK, font=("Segoe UI", 8, "bold"),
                    relief="flat")
