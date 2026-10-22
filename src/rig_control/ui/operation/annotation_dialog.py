"""Small modal dialogs for writing a note and for the end-of-run review.

Both report back through ``on_done``, like the recipe end-state dialog, and
both close with Esc so they never stand between the operator and the rig.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import tkinter as tk
from tkinter import ttk

from rig_control.data.annotations import (
    ANNOTATION_KIND_LABELS,
    ANNOTATION_KINDS,
    MAX_ANNOTATION_TEXT,
    OVERALL_QC_LABELS,
    OVERALL_QC_LEVELS,
)
from rig_control.ui.common.theme import ERROR_TEXT, MUTED_TEXT, SECTION_FONT
from rig_control.ui.operation.model import RunReviewContext


_WRAP = 420


def time_context(timestamp: datetime, *, now: datetime | None = None) -> str:
    """'14:32:10 · ~6 min ago' for the note dialog."""

    now = now or datetime.now(UTC)
    clock = timestamp.astimezone().strftime("%H:%M:%S")
    ago = max(0, round((now - timestamp).total_seconds()))
    if ago < 10:
        return f"{clock} · just now"
    if ago < 120:
        return f"{clock} · ~{ago} s ago"
    return f"{clock} · ~{round(ago / 60)} min ago"


class AnnotationDialog:
    """Type and text for a note. ``on_done`` gets (kind, text), or None on Cancel."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        context: str,
        on_done: Callable[[tuple[str, str] | None], None],
        kind: str = "lab_note",
        text: str = "",
    ) -> None:
        self._on_done = on_done
        self._closed = False
        top = self._top = tk.Toplevel(parent)
        top.transient(parent.winfo_toplevel())
        top.title(title)
        top.resizable(False, False)
        body = ttk.Frame(top, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=context, wraplength=_WRAP, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 8),
        )
        kinds = ttk.Frame(body)
        kinds.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self._kind = tk.StringVar(master=top, value=kind)
        for column, value in enumerate(ANNOTATION_KINDS):
            ttk.Radiobutton(
                kinds, text=ANNOTATION_KIND_LABELS[value], value=value,
                variable=self._kind,
            ).grid(row=0, column=column, padx=(0, 12))
        self._text = tk.Text(body, width=52, height=4, wrap="word", undo=True)
        self._text.grid(row=2, column=0, sticky="ew")
        self._text.insert("1.0", text)
        ttk.Label(
            body, text="Enter saves  •  Shift+Enter starts a new line  •  Esc cancels",
            foreground=MUTED_TEXT,
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._error = ttk.Label(body, foreground=ERROR_TEXT, wraplength=_WRAP)
        self._error.grid(row=4, column=0, sticky="w")
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Save", command=self._save, default="active").pack(
            side="left", padx=3,
        )
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="left")
        self._text.bind("<Return>", self._on_return)
        top.bind("<Escape>", lambda _event: self._cancel())
        top.protocol("WM_DELETE_WINDOW", self._cancel)
        self._text.focus_set()
        self._text.mark_set("insert", "end-1c")
        top.grab_set()

    def _on_return(self, event: tk.Event) -> str | None:
        if int(event.state) & 0x0001:  # Shift: a new line, as usual.
            return None
        self._save()
        return "break"

    def _save(self) -> None:
        text = self._text.get("1.0", "end-1c").strip()
        if not text:
            self._error.configure(text="Write something for the note, or press Esc.")
            return
        if len(text) > MAX_ANNOTATION_TEXT:
            self._error.configure(
                text=f"A note can be at most {MAX_ANNOTATION_TEXT} characters."
            )
            return
        self._close((self._kind.get(), text))

    def _cancel(self) -> None:
        self._close(None)

    def _close(self, result: tuple[str, str] | None) -> None:
        if self._closed:
            return
        self._closed = True
        self._top.grab_release()
        self._top.destroy()
        self._on_done(result)


class RunReviewDialog:
    """End-of-run review. ``on_done`` gets (overall_qc, end_notes), or None on Skip."""

    def __init__(
        self,
        parent: tk.Misc,
        context: RunReviewContext,
        *,
        on_done: Callable[[tuple[str, str] | None], None],
    ) -> None:
        self._on_done = on_done
        self._closed = False
        top = self._top = tk.Toplevel(parent)
        top.transient(parent.winfo_toplevel())
        top.title(f"Run review — {context.experiment_id}")
        top.minsize(520, 520)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        body = ttk.Frame(top, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(
            body,
            text=(
                "Optional: how did this run go? Skip leaves the run as it is; "
                "you can review it later with Review run…."
            ),
            wraplength=_WRAP + 60, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(body, text="Start notes", font=SECTION_FONT).grid(
            row=1, column=0, sticky="w",
        )
        start = tk.Text(body, height=3, wrap="word", background="#F3F3F3", relief="flat")
        start.insert("1.0", context.start_notes or "(none)")
        start.configure(state="disabled")
        start.grid(row=2, column=0, sticky="ew", pady=(2, 8))

        notes_frame = ttk.Frame(body)
        notes_frame.grid(row=3, column=0, sticky="nsew")
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(1, weight=1)
        ttk.Label(
            notes_frame, text=f"Notes during the run ({len(context.annotations)})",
            font=SECTION_FONT,
        ).grid(row=0, column=0, sticky="w")
        notes = ttk.Treeview(
            notes_frame, columns=("time", "type", "text"), show="headings", height=5,
        )
        for column, heading, width, stretch in (
            ("time", "Time", 70, False), ("type", "Type", 70, False),
            ("text", "Note", 340, True),
        ):
            notes.heading(column, text=heading, anchor="w")
            notes.column(column, width=width, stretch=stretch, anchor="w")
        for note in context.annotations:
            notes.insert("", "end", values=(
                note.timestamp.astimezone().strftime("%H:%M:%S"),
                note.kind_label, note.text.replace("\n", " "),
            ))
        notes.grid(row=1, column=0, sticky="nsew", pady=(2, 8))

        ttk.Label(body, text="Overall QC flag", font=SECTION_FONT).grid(
            row=4, column=0, sticky="w",
        )
        self._qc_values = {OVERALL_QC_LABELS[level]: level for level in OVERALL_QC_LEVELS}
        current = context.review.overall_qc if context.review else "not_assessed"
        self._qc = ttk.Combobox(
            body, values=tuple(self._qc_values), state="readonly", width=16,
        )
        self._qc.set(OVERALL_QC_LABELS[current])
        self._qc.grid(row=5, column=0, sticky="w", pady=(2, 8))

        ttk.Label(body, text="End notes", font=SECTION_FONT).grid(
            row=6, column=0, sticky="w",
        )
        self._end_notes = tk.Text(body, height=5, wrap="word", undo=True)
        if context.review is not None:
            self._end_notes.insert("1.0", context.review.end_notes)
        self._end_notes.grid(row=7, column=0, sticky="ew", pady=(2, 0))
        if context.review is not None:
            ttk.Label(
                body,
                text=(
                    "Reviewed before at "
                    f"{context.review.reviewed_at.astimezone():%Y-%m-%d %H:%M}. "
                    "Saving adds a new review; the earlier one stays in the run folder."
                ),
                foreground=MUTED_TEXT, wraplength=_WRAP + 60, justify="left",
            ).grid(row=8, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Save", command=self._save, default="active").pack(
            side="left", padx=3,
        )
        ttk.Button(buttons, text="Skip", command=self._skip).pack(side="left")
        top.bind("<Escape>", lambda _event: self._skip())
        top.protocol("WM_DELETE_WINDOW", self._skip)
        self._end_notes.focus_set()

    def _save(self) -> None:
        self._close((
            self._qc_values[self._qc.get()],
            self._end_notes.get("1.0", "end-1c").strip(),
        ))

    def _skip(self) -> None:
        self._close(None)

    def _close(self, result: tuple[str, str] | None) -> None:
        if self._closed:
            return
        self._closed = True
        self._top.destroy()
        self._on_done(result)
