"""Asking before a faulted device is reconnected.

A driver that faulted refuses commands until it is disconnected and
connected again, and disconnecting puts it in its safe state (a pump stops,
a supply's output turns off).  That is usually exactly what is wanted after
a fault, but it changes the rig, so the operator says yes first.
"""

from collections.abc import Sequence
import tkinter as tk
from tkinter import messagebox


def reconnect_question(device_ids: Sequence[str]) -> str:
    names = ", ".join(device_ids)
    one = len(device_ids) == 1
    return (
        f"{names} {'is' if one else 'are'} faulted.\n\n"
        f"Reconnect {'it' if one else 'them'} as well?  "
        f"{'It is' if one else 'Each is'} put in its safe state first (a pump stops, "
        "a supply's output turns off), then connected again.\n\n"
        f"Yes: reconnect {'it' if one else 'them'}.\n"
        f"No: connect the rest and leave {'it' if one else 'them'} as {'it is' if one else 'they are'}."
    )


def ask_reconnect_faulted(parent: tk.Misc, device_ids: Sequence[str]) -> bool | None:
    """True to reconnect the faulted devices, False to leave them, None to do nothing."""

    if not device_ids:
        return False
    return messagebox.askyesnocancel("Reconnect faulted devices?", reconnect_question(device_ids),
                                     parent=parent)
