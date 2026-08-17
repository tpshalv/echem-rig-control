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