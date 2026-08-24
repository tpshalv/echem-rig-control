import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from datetime import datetime
from queue import Empty, Queue
from threading import Thread

from rig_control.ui.diagnostics.model import (
    DiagnosticActionResult,
    DiagnosticViewModel,
)
from rig_control.ui.common.theme import (
    BODY_BOLD_FONT,
    BODY_FONT,
    ERROR_BACKGROUND,
    ERROR_TEXT,
    MONOSPACE_FONT,
    MUTED_TEXT,
    NEUTRAL_BACKGROUND,
    SECTION_FONT,
    SUCCESS_BACKGROUND,
    SUCCESS_TEXT,
    TITLE_FONT,
    WARNING_BACKGROUND,
    WARNING_TEXT,
)

class DiagnosticWindow:
    """Tkinter diagnostic screen backed by DiagnosticViewModel."""

    def __init__(
        self,
        root: tk.Tk,
        view_model: DiagnosticViewModel,
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._history: list[str] = []
        self._technical_details: list[str] = []
        self._action_results: Queue[DiagnosticActionResult] = Queue()
        self._action_in_progress = False
        self._last_health_timestamp: datetime | None = None
        self._after_id: str | None = None

        self._configure_window()
        self._create_widgets()
        self.refresh()
        self._schedule_update()

    def _configure_window(self) -> None:
        self._root.title("Echem Rig Control — Diagnostics")
        self._root.geometry("950x780")
        self._root.minsize(760, 620)

        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=12)
        main.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(6, weight=1)

        title = ttk.Label(
            main,
            text="Device diagnostics",
            font=TITLE_FONT,
        )
        title.grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 10),
        )

        self._device_table = ttk.Treeview(
            main,
            columns=("device_type", "status"),
            show="tree headings",
            selectmode="browse",
        )
        self._device_table.heading(
            "#0",
            text="Device ID",
        )
        self._device_table.heading(
            "device_type",
            text="Device type",
        )
        self._device_table.heading(
            "status",
            text="Status",
        )

        self._device_table.column(
            "#0",
            width=250,
            minwidth=150,
        )
        self._device_table.column(
            "device_type",
            width=260,
            minwidth=160,
        )
        self._device_table.column(
            "status",
            width=130,
            minwidth=100,
        )

        self._device_table.tag_configure(
            "ready",
            foreground=SUCCESS_TEXT,
            background=SUCCESS_BACKGROUND,
            font=BODY_BOLD_FONT,
        )
        self._device_table.tag_configure(
            "disconnected",
            foreground=MUTED_TEXT,
            background=NEUTRAL_BACKGROUND,
            font=BODY_FONT,
        )
        self._device_table.tag_configure(
            "warning",
            foreground=WARNING_TEXT,
            background=WARNING_BACKGROUND,
            font=BODY_BOLD_FONT,
        )
        self._device_table.tag_configure(
            "error",
            foreground=ERROR_TEXT,
            background=ERROR_BACKGROUND,
            font=BODY_BOLD_FONT,
        )

        self._device_table.grid(
            row=1,
            column=0,
            sticky="nsew",
        )

        buttons = ttk.Frame(main)
        buttons.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=10,
        )

        self._connect_button = ttk.Button(
            buttons,
            text="Connect selected",
            command=self._connect_selected,
        )
        self._connect_button.grid(row=0, column=0, padx=(0, 8))

        self._disconnect_button = ttk.Button(
            buttons,
            text="Disconnect selected",
            command=self._disconnect_selected,
        )
        self._disconnect_button.grid(row=0, column=1, padx=(0, 8))

        self._test_button = ttk.Button(
            buttons,
            text="Test communication",
            command=self._test_selected_communication,
        )
        self._test_button.grid(row=0, column=2, padx=(0, 8))

        ttk.Button(
            buttons,
            text="Refresh",
            command=self.refresh,
        ).grid(row=0, column=3)

        health = ttk.LabelFrame(main, text="Runtime health", padding=8)
        health.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        health.columnconfigure(0, weight=1)
        self._health_summary = ttk.Label(
            health,
            text="Waiting for health sample",
            justify="left",
        )
        self._health_summary.grid(row=0, column=0, sticky="w")

        details_label = ttk.Label(
            main,
            text="Diagnostic details",
            font=SECTION_FONT,
        )
        details_label.grid(
            row=5,
            column=0,
            sticky="w",
            pady=(6, 4),
        )

        self._details = tk.Text(
            main,
            height=10,
            wrap="word",
            font=MONOSPACE_FONT,
        )
        self._details.grid(
            row=6,
            column=0,
            sticky="nsew",
        )
        self._details.configure(state="disabled")

        details_buttons = ttk.Frame(main)
        details_buttons.grid(
            row=7,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Button(
            details_buttons,
            text="Copy diagnostic log",
            command=self._copy_diagnostic_log,
        ).grid(row=0, column=0)

    def refresh(self) -> None:
        selected = self._selected_device_id()

        for item in self._device_table.get_children():
            self._device_table.delete(item)

        for row in self._view_model.device_rows():
            self._device_table.insert(
                "",
                "end",
                iid=row.device_id,
                text=row.device_id,
                values=(
                    row.device_type,
                    row.status,
                ),
                tags=(row.status,),
            )

        if (
            selected is not None
            and self._device_table.exists(selected)
        ):
            self._device_table.selection_set(selected)

    def _connect_selected(self) -> None:
        device_id = self._require_selection()

        if device_id is None:
            return

        self._start_device_action(
            lambda: self._view_model.connect_device(device_id)
        )

    def _disconnect_selected(self) -> None:
        device_id = self._require_selection()

        if device_id is None:
            return

        self._start_device_action(
            lambda: self._view_model.disconnect_device(device_id)
        )

    def _test_selected_communication(self) -> None:
        device_id = self._require_selection()
        if device_id is None:
            return
        self._start_device_action(
            lambda: self._view_model.test_communication(device_id)
        )

    def _start_device_action(
        self,
        action: Callable[[], DiagnosticActionResult],
    ) -> None:
        if self._action_in_progress:
            return
        self._action_in_progress = True
        self._connect_button.configure(state="disabled")
        self._disconnect_button.configure(state="disabled")
        self._test_button.configure(state="disabled")

        def run() -> None:
            self._action_results.put(action())

        Thread(target=run, name="device-diagnostic-action", daemon=True).start()

    def _poll_action_results(self) -> None:
        try:
            result = self._action_results.get_nowait()
        except Empty:
            pass
        else:
            self._action_in_progress = False
            self._connect_button.configure(state="normal")
            self._disconnect_button.configure(state="normal")
            self._test_button.configure(state="normal")
            self._display_result(result)
            self.refresh()
        self._refresh_runtime_health()
        self._schedule_update()

    def _schedule_update(self) -> None:
        self._after_id = self._root.after(100, self._poll_action_results)

    def cancel_updates(self) -> None:
        if self._after_id is None:
            return
        try:
            self._root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def _refresh_runtime_health(self) -> None:
        latest = self._view_model.latest_runtime_health()
        if latest is None:
            return
        if latest.timestamp == self._last_health_timestamp:
            return
        self._last_health_timestamp = latest.timestamp
        history = self._view_model.runtime_health_history()
        hour_ago = latest.timestamp.timestamp() - 3600
        comparison = next(
            (
                point
                for point in history
                if point.timestamp.timestamp() >= hour_ago
            ),
            history[0],
        )
        memory_change = _format_memory_change(
            latest.private_bytes,
            comparison.private_bytes,
        )
        queue = _format_pair(
            latest.results_queue_size,
            latest.results_queue_capacity,
        )
        status = _health_status(latest)
        self._health_summary.configure(
            text=(
                f"Status: {status}\n"
                f"Memory: {_format_mb(latest.private_bytes)} "
                f"({memory_change} over the last hour)  |  "
                f"Python tracked: {_format_mb(latest.traced_python_bytes)}\n"
                f"Queue: {queue}  |  "
                f"Dropped display updates: "
                f"{latest.dropped_results_batches or 0}  |  "
                f"Threads: {latest.thread_count}  |  "
                f"Retained events: "
                f"{_format_optional(latest.retained_event_count)}\n"
                "Latest sample: "
                f"{latest.timestamp.astimezone().isoformat(timespec='seconds')}"
            )
        )

    def _require_selection(self) -> str | None:
        device_id = self._selected_device_id()

        if device_id is None:
            timestamp = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            self._history.append(
                f"[{timestamp}] Select a device from the table first."
            )
            self._set_details("\n".join(self._history))

        return device_id

    def _selected_device_id(self) -> str | None:
        selected_items = self._device_table.selection()

        if not selected_items:
            return None

        return selected_items[0]

    def _display_result(
        self,
        result: DiagnosticActionResult,
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        self._history.append(
            f"[{timestamp}] {result.summary}"
        )

        if result.technical_details:
            self._technical_details.append(
                f"[{timestamp}] {result.summary}\n"
                f"{result.technical_details}"
            )

        self._set_details("\n".join(self._history))

    def _set_details(self, text: str) -> None:
        self._details.configure(state="normal")
        self._details.delete("1.0", "end")
        self._details.insert("1.0", text)
        self._details.configure(state="disabled")

    def _copy_diagnostic_log(self) -> None:
        sections: list[str] = []

        if self._history:
            sections.append(
                "DIAGNOSTIC ACTION HISTORY\n"
                + "\n".join(self._history)
            )

        if self._technical_details:
            sections.append(
                "TECHNICAL ERROR DETAILS\n"
                + "\n\n".join(self._technical_details)
            )

        if sections:
            text = "\n\n".join(sections)
        else:
            text = "No diagnostic actions have been recorded."

        self._root.clipboard_clear()
        self._root.clipboard_append(text)
        self._root.update()


def _format_mb(value: int | None) -> str:
    return "unavailable" if value is None else f"{value / 1_000_000:.1f} MB"


def _format_optional(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def _format_pair(first: int | None, second: int | None) -> str:
    return f"{_format_optional(first)}/{_format_optional(second)}"


def _format_memory_change(current: int | None, previous: int | None) -> str:
    if current is None or previous is None:
        return "change unavailable"
    change_mb = (current - previous) / 1_000_000
    return f"{change_mb:+.1f} MB"


def _health_status(point) -> str:
    if point.private_bytes is not None and point.private_bytes >= 1_000_000_000:
        return "WARNING"
    if (
        point.results_queue_size is not None
        and point.results_queue_capacity is not None
        and point.results_queue_size >= point.results_queue_capacity * 0.8
    ):
        return "WARNING"
    return "Healthy"
