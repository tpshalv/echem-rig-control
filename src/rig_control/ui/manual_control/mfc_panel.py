import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import ttk

from rig_control.ui.common.theme import (
    BODY_BOLD_FONT,
    MUTED_TEXT,
    SECTION_FONT,
)
from rig_control.ui.common.widgets import create_device_status_indicator
from rig_control.ui.manual_control.model import ManualControlViewModel


class MfcPanel(ttk.Frame):
    """Manual controls for the configured mass flow controllers."""

    def __init__(
        self,
        parent: tk.Misc,
        view_model: ManualControlViewModel,
        on_set_flow: Callable[[str, ttk.Entry], None],
    ) -> None:
        super().__init__(parent, padding=10)
        self._view_model = view_model
        self._on_set_flow = on_set_flow
        self.first_entry: ttk.Entry | None = None
        self.columnconfigure(0, weight=1)

    def refresh(self) -> None:
        """Rebuild the panel from the latest view-model values."""

        for child in self.winfo_children():
            child.destroy()

        self.first_entry = None
        rows = self._view_model.mfc_rows()

        if not rows:
            ttk.Label(
                self,
                text="No mass flow controllers are configured.",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, row in enumerate(rows):
            frame = ttk.LabelFrame(
                self,
                text=row.device_id,
                padding=10,
            )
            frame.grid(
                row=index,
                column=0,
                sticky="ew",
                pady=(0, 10),
            )
            frame.columnconfigure(1, weight=1)

            create_device_status_indicator(
                frame,
                status=row.status,
                columnspan=4,
            )

            headings = ttk.Frame(frame)
            headings.grid(
                row=1,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(10, 2),
            )
            headings.columnconfigure(1, weight=1)
            headings.columnconfigure(2, weight=1)

            ttk.Label(
                headings,
                text="Measurement",
                font=BODY_BOLD_FONT,
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(
                headings,
                text="Set",
                font=BODY_BOLD_FONT,
            ).grid(row=0, column=1, sticky="w", padx=(18, 0))
            ttk.Label(
                headings,
                text="Actual",
                font=BODY_BOLD_FONT,
            ).grid(row=0, column=2, sticky="w", padx=(18, 0))

            values = ttk.Frame(frame)
            values.grid(
                row=2,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 10),
            )
            values.columnconfigure(1, weight=1)
            values.columnconfigure(2, weight=1)

            ttk.Label(values, text="Flow").grid(
                row=0,
                column=0,
                sticky="w",
            )
            ttk.Label(
                values,
                text=(
                    f"{self._format_number(row.flow_setpoint)} "
                    f"{row.flow_unit}"
                ),
            ).grid(row=0, column=1, sticky="w", padx=(18, 0))
            ttk.Label(
                values,
                text=self._format_measurement(
                    row.measured_flow,
                    row.flow_unit,
                ),
                font=SECTION_FONT,
            ).grid(row=0, column=2, sticky="w", padx=(18, 0))

            ttk.Label(
                frame,
                text=self._measurement_detail(
                    row.measurement_quality,
                    row.measurement_time,
                ),
                foreground=MUTED_TEXT,
            ).grid(
                row=3,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(0, 10),
            )

            ttk.Separator(frame, orient="horizontal").grid(
                row=4,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 10),
            )
            ttk.Label(
                frame,
                text=f"New flow ({row.flow_unit}):",
            ).grid(row=5, column=0, sticky="w")

            entry = ttk.Entry(frame, width=14)
            entry.insert(0, str(row.flow_setpoint))
            entry.grid(row=5, column=1, sticky="w", padx=8)

            if self.first_entry is None and row.is_available:
                self.first_entry = entry

            set_flow = (
                lambda device_id=row.device_id,
                selected_entry=entry: self._on_set_flow(
                    device_id,
                    selected_entry,
                )
            )
            entry.bind("<Return>", lambda _event, action=set_flow: action())
            entry.bind("<KP_Enter>", lambda _event, action=set_flow: action())

            set_button = ttk.Button(
                frame,
                text="Set flow",
                command=set_flow,
            )
            set_button.grid(row=5, column=2, padx=(0, 8))

            if not row.is_available:
                entry.configure(state="disabled")
                set_button.configure(state="disabled")

    @staticmethod
    def _format_number(value: float) -> str:
        return format(value, ".6g")

    @classmethod
    def _format_measurement(
        cls,
        value: float | None,
        unit: str,
    ) -> str:
        if value is None:
            return "—"

        return f"{cls._format_number(value)} {unit}"

    @staticmethod
    def _measurement_detail(
        quality: str | None,
        timestamp: datetime | None,
    ) -> str:
        if timestamp is None:
            return "No current measurement available"

        local_timestamp = timestamp.astimezone().isoformat(
            timespec="seconds"
        )
        displayed_quality = (
            quality.upper() if quality is not None else "UNKNOWN"
        )
        return (
            f"Quality: {displayed_quality}; "
            f"updated {local_timestamp}"
        )
