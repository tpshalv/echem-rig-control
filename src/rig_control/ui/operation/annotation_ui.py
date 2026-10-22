"""Notes on the live graphs: one shared hub, and one controller per chart.

The hub owns the shared "Show notes" setting and every change to notes, so
all open graphs agree. Each chart gets a ``ChartAnnotations`` controller
that draws its markers and turns double-clicks and right-clicks on that
chart into calls on the hub.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

from rig_control.data.annotations import Annotation, format_duration
from rig_control.ui.operation.annotation_dialog import AnnotationDialog, time_context
from rig_control.ui.operation.annotation_overlay import (
    AnnotationOverlay,
    GraphPlacement,
    MarkerCluster,
    NotePopup,
    PlotMapping,
    cluster_markers,
    describe_note,
    live_edge_seconds,
    place_on_graph,
)
from rig_control.ui.operation.model import OperationActionResult


class AnnotationHub:
    """Shared note state and actions for every graph and the note box."""

    def __init__(
        self,
        root: tk.Misc,
        view_model,
        *,
        on_changed: Callable[[], None],
        on_status: Callable[[str], None],
    ) -> None:
        self._root = root
        self._view_model = view_model
        self._on_changed = on_changed
        self._on_status = on_status
        self.show = tk.BooleanVar(master=root, value=True)

    @property
    def is_recording(self) -> bool:
        return bool(self._view_model.is_recording)

    @property
    def run_started_at(self) -> datetime | None:
        return getattr(self._view_model, "run_started_at", None)

    def notes_between(self, start: float, end: float) -> tuple[Annotation, ...]:
        if not self.show.get():
            return ()
        return self._view_model.annotations_between(start, end)

    def changed(self) -> None:
        self._on_changed()

    def placement(
        self,
        x: float,
        mapping: PlotMapping,
        signal: tuple[str, str] | None,
    ) -> GraphPlacement:
        def nearest(timestamp: datetime) -> datetime | None:
            if signal is None:
                return None
            reading = self._view_model.nearest_reading(*signal, timestamp)
            return None if reading is None else reading.timestamp

        return place_on_graph(x, mapping, nearest)

    def add_from_graph(
        self,
        parent: tk.Misc,
        placement: GraphPlacement,
        signal: tuple[str, str] | None,
        signal_label: str | None,
    ) -> None:
        if not self._require_recording("add notes"):
            return
        started = self.run_started_at
        if started is not None and placement.timestamp < started:
            # Graphs also show monitoring from before the run; say so before
            # the note is typed rather than refusing it afterwards.
            self._on_status("That point is before this recording started; notes "
                            "can only be placed within the run.")
            return
        context = time_context(placement.timestamp)
        context += f" · ±{format_duration(placement.uncertainty_seconds)}"
        if placement.snapped:
            context += " · snapped to nearest reading"
        if signal_label:
            context += f"\n{signal_label}"

        def done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            kind, text = result
            self._report(self._view_model.add_annotation(
                kind, text, placement.timestamp, origin="graph",
                uncertainty_seconds=placement.uncertainty_seconds, signal=signal,
            ))

        AnnotationDialog(parent, title="Add note", context=context, on_done=done)

    def edit(self, parent: tk.Misc, note: Annotation) -> None:
        if not self._require_recording("change notes"):
            return

        def done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            kind, text = result
            self._report(self._view_model.edit_annotation(
                note.annotation_id, kind=kind, text=text,
            ))

        AnnotationDialog(
            parent, title="Edit note",
            context=describe_note(note, self.run_started_at).split("\n", 1)[0],
            kind=note.kind, text=note.text, on_done=done,
        )

    def move(
        self,
        note: Annotation,
        placement: GraphPlacement,
        signal: tuple[str, str] | None,
    ) -> None:
        self._report(self._view_model.edit_annotation(
            note.annotation_id, timestamp=placement.timestamp, origin="graph",
            uncertainty_seconds=placement.uncertainty_seconds, signal=signal,
        ))

    def delete(self, parent: tk.Misc, note: Annotation) -> None:
        if not self._require_recording("change notes"):
            return
        if not messagebox.askyesno(
            "Delete note",
            f"Delete this note?\n\n{note.text}\n\nThe run folder keeps a record "
            "that it was deleted.",
            parent=parent,
        ):
            return
        self._report(self._view_model.delete_annotation(note.annotation_id))

    def require_recording(self, action: str) -> bool:
        return self._require_recording(action)

    def _require_recording(self, action: str) -> bool:
        if self.is_recording:
            return True
        self._on_status(f"Start recording to {action}; notes are saved with the run.")
        return False

    def _report(self, result: OperationActionResult) -> None:
        self._on_status(result.summary)
        if not result.succeeded:
            messagebox.showerror("Note not saved", result.summary, parent=self._root)
        self._on_changed()


class ChartAnnotations:
    """Markers, hover summaries and note editing for one chart canvas."""

    def __init__(
        self,
        canvas: tk.Canvas,
        hub: AnnotationHub,
        *,
        signal_at: Callable[[float, float], tuple[str, str] | None],
        signal_label: Callable[[tuple[str, str]], str] = lambda key: ".".join(key),
    ) -> None:
        self._canvas = canvas
        self._hub = hub
        self._signal_at = signal_at
        self._signal_label = signal_label
        self._mapping: PlotMapping | None = None
        self._moving: Annotation | None = None
        self._marker_clicked = False
        self._overlay = AnnotationOverlay(canvas)
        self._overlay.on_enter = self._show_popup
        self._overlay.on_leave = lambda _cluster, _event: self._popup.hide()
        self._overlay.on_click = self._pin_popup
        self._overlay.on_menu = self._show_menu
        self._popup = NotePopup(canvas)
        canvas.bind("<Double-Button-1>", self._on_double_click, add="+")
        canvas.bind("<Button-1>", self._on_click, add="+")
        canvas.bind("<Escape>", lambda _event: self._cancel_move(), add="+")

    @property
    def item_count(self) -> int:
        return self._overlay.item_count

    @property
    def moving(self) -> Annotation | None:
        return self._moving

    def draw(self, mapping: PlotMapping | None) -> None:
        """Call after each chart redraw with the frame's mapping (None if empty)."""
        self._mapping = mapping
        if mapping is None or mapping.end <= mapping.start:
            self._overlay.hide_all()
            return
        notes = self._hub.notes_between(
            mapping.start, mapping.end + live_edge_seconds(mapping)
        )
        self._overlay.draw(cluster_markers(notes, mapping), mapping)

    def _on_double_click(self, event: tk.Event) -> str | None:
        # The time comes from the frame that was on screen when clicked.
        mapping = self._mapping
        if self._moving is not None or mapping is None:
            return None
        if not mapping.contains(event.x, event.y):
            return None
        if not self._hub.require_recording("add notes"):
            return "break"
        signal = self._signal_at(event.x, event.y)
        self._hub.add_from_graph(
            self._canvas, self._hub.placement(event.x, mapping, signal), signal,
            self._signal_label(signal) if signal is not None else None,
        )
        return "break"

    def _on_click(self, event: tk.Event) -> str | None:
        if self._marker_clicked:
            self._marker_clicked = False
            return None
        if self._popup.pinned:
            self._popup.hide(force=True)
        note, mapping = self._moving, self._mapping
        if note is None:
            return None
        self._cancel_move()
        if mapping is None or not mapping.contains(event.x, event.y):
            return "break"
        signal = self._signal_at(event.x, event.y)
        self._hub.move(note, self._hub.placement(event.x, mapping, signal), signal)
        return "break"

    def _start_move(self, note: Annotation) -> None:
        if not self._hub.require_recording("change notes"):
            return
        self._moving = note
        self._canvas.configure(cursor="crosshair")
        self._canvas.focus_set()

    def _cancel_move(self) -> None:
        self._moving = None
        self._canvas.configure(cursor="")

    def _show_popup(self, cluster: MarkerCluster, event: tk.Event) -> None:
        if self._popup.pinned:
            return
        self._popup.show(
            self._popup_text(cluster), event.x_root, event.y_root,
            qc=any(note.kind == "qc_flag" for note in cluster.notes),
        )

    def _pin_popup(self, cluster: MarkerCluster, event: tk.Event) -> None:
        # The canvas-wide click handler runs after this one; it must not
        # treat the same click as "click elsewhere" and unpin straight away.
        self._marker_clicked = True
        self._popup.hide(force=True)
        self._popup.pinned = True
        self._popup.show(
            self._popup_text(cluster), event.x_root, event.y_root,
            qc=any(note.kind == "qc_flag" for note in cluster.notes),
        )

    def _popup_text(self, cluster: MarkerCluster) -> str:
        return "\n\n".join(
            describe_note(note, self._hub.run_started_at) for note in cluster.notes
        )

    def _show_menu(self, cluster: MarkerCluster, event: tk.Event) -> None:
        self._popup.hide(force=True)
        menu = tk.Menu(self._canvas, tearoff=False)
        if len(cluster.notes) == 1:
            self._add_note_actions(menu, cluster.notes[0])
        else:
            for note in cluster.notes:
                submenu = tk.Menu(menu, tearoff=False)
                self._add_note_actions(submenu, note)
                label = note.text.replace("\n", " ")
                menu.add_cascade(
                    label=(
                        f"{note.timestamp.astimezone():%H:%M:%S}  "
                        f"{label[:40]}{'…' if len(label) > 40 else ''}"
                    ),
                    menu=submenu,
                )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _add_note_actions(self, menu: tk.Menu, note: Annotation) -> None:
        menu.add_command(label="Edit…", command=lambda: self._hub.edit(self._canvas, note))
        menu.add_command(label="Move here (click new spot)",
                         command=lambda: self._start_move(note))
        menu.add_separator()
        menu.add_command(label="Delete", command=lambda: self._hub.delete(self._canvas, note))
