import tkinter as tk

from rig_control.ui.common.theme import (
    SECTION_FONT,
    STATUS_TEXT_COLOURS,
)


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
