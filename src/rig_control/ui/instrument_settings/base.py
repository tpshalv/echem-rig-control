"""Small interface implemented by each detailed instrument editor."""

from tkinter import ttk


class InstrumentSettingsPanel(ttk.Frame):
    def refresh(self) -> None:
        """Refresh cached UI context without reading hardware automatically."""
        raise NotImplementedError

    def apply_changes(self) -> bool:
        """Apply edited settings through the instrument's configuration service."""
        raise NotImplementedError
