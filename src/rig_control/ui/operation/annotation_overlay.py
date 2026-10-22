"""Note markers drawn over the live graphs, and the maths behind placing them.

The pure functions here (time <-> x, clustering, snapping, uncertainty) are
kept free of Tk so they can be tested directly. ``AnnotationOverlay`` draws
markers from a reusable pool of canvas items, like the chart renderers do,
so showing notes never grows the canvas on each refresh.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import tkinter as tk

from rig_control.data.annotations import Annotation, format_duration, nice_uncertainty
from rig_control.ui.common.theme import BODY_FONT, ERROR_TEXT, MUTED_TEXT, NAVY_INK


LAB_NOTE_COLOUR = "#6B7785"
QC_FLAG_COLOUR = "#D9480F"
MARKER_DASH = (4, 3)
CLUSTER_GAP_PIXELS = 6
# Placing a note snaps to a reading only if one is this close on screen, so
# a click between sparse readings stays where it was made.
SNAP_PIXELS = 6
# The uncertainty of a click: about two pixels' worth of time either side.
CLICK_UNCERTAINTY_PIXELS = 2
FLAG_SIZE = 7
MARKER_TAG = "annotation-marker"


@dataclass(frozen=True, slots=True)
class PlotMapping:
    """How a drawn frame maps time (epoch seconds) onto canvas x."""

    left: float
    top: float
    right: float
    bottom: float
    start: float
    end: float

    @property
    def seconds_per_pixel(self) -> float:
        return (self.end - self.start) / max(self.right - self.left, 1e-9)

    def x_for_time(self, timestamp: float) -> float:
        return self.left + (timestamp - self.start) / self.seconds_per_pixel

    def time_for_x(self, x: float) -> float:
        return self.start + (x - self.left) * self.seconds_per_pixel

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True, slots=True)
class MarkerCluster:
    x: float
    notes: tuple[Annotation, ...]

    @property
    def colour(self) -> str:
        if any(note.kind == "qc_flag" for note in self.notes):
            return QC_FLAG_COLOUR
        return LAB_NOTE_COLOUR


@dataclass(frozen=True, slots=True)
class GraphPlacement:
    timestamp: datetime
    uncertainty_seconds: float
    snapped: bool


def click_uncertainty(mapping: PlotMapping) -> float:
    return nice_uncertainty(CLICK_UNCERTAINTY_PIXELS * mapping.seconds_per_pixel)


def place_on_graph(
    x: float,
    mapping: PlotMapping,
    nearest_reading_time: Callable[[datetime], datetime | None],
) -> GraphPlacement:
    """Turn a click into a note time, snapped to a nearby reading if any."""

    clicked = datetime.fromtimestamp(mapping.time_for_x(x), UTC)
    reading = nearest_reading_time(clicked)
    snapped = reading is not None and (
        abs((reading - clicked).total_seconds())
        <= SNAP_PIXELS * mapping.seconds_per_pixel
    )
    return GraphPlacement(
        timestamp=reading if snapped and reading is not None else clicked,
        uncertainty_seconds=click_uncertainty(mapping),
        snapped=snapped,
    )


def cluster_markers(
    notes: Sequence[Annotation],
    mapping: PlotMapping,
    *,
    gap_pixels: float = CLUSTER_GAP_PIXELS,
) -> tuple[MarkerCluster, ...]:
    """Group notes too close to tell apart; each group is one marker."""

    clusters: list[tuple[list[float], list[Annotation]]] = []
    for note in sorted(notes, key=lambda item: item.timestamp):
        x = mapping.x_for_time(note.timestamp.timestamp())
        if x < mapping.left:
            continue
        if x > mapping.right:
            if note.timestamp.timestamp() > mapping.end + live_edge_seconds(mapping):
                continue
            x = mapping.right  # Newer than the latest reading: held at the edge.
        if clusters and x - clusters[-1][0][-1] <= gap_pixels:
            clusters[-1][0].append(x)
            clusters[-1][1].append(note)
        else:
            clusters.append(([x], [note]))
    return tuple(
        MarkerCluster(x=sum(xs) / len(xs), notes=tuple(group))
        for xs, group in clusters
    )


def live_edge_seconds(mapping: PlotMapping) -> float:
    """How far past the newest reading a note may be and still show at the edge.

    A note typed now is usually a moment newer than the last reading, which
    sets the graph's right edge; without this it would stay off-screen until
    the next poll.
    """
    return max(60.0, 0.05 * (mapping.end - mapping.start))


def describe_note(note: Annotation, run_started_at: datetime | None) -> str:
    """Hover text for one note: when, what kind, what, and how it was placed."""

    when = note.timestamp.astimezone().strftime("%H:%M:%S")
    if run_started_at is not None:
        elapsed = max(0, int((note.timestamp - run_started_at).total_seconds()))
        hours, remainder = divmod(elapsed, 3_600)
        minutes, seconds = divmod(remainder, 60)
        into = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        when += f"  (+{into} into run)"
    if note.origin == "graph":
        placed = "placed from graph"
        if note.uncertainty_seconds:
            placed += f", ±{format_duration(note.uncertainty_seconds)}"
    else:
        placed = "typed"
    heading = note.kind_label + ("  ·  edited" if note.edited else "")
    return f"{when}\n{heading}\n{note.text}\n{placed}"


class AnnotationOverlay:
    """Dashed note markers over a chart, drawn from a reusable item pool."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self._canvas = canvas
        self._slots: list[tuple[int, int, int]] = []
        self._clusters: list[MarkerCluster] = []
        self.on_enter: Callable[[MarkerCluster, tk.Event], None] | None = None
        self.on_leave: Callable[[MarkerCluster, tk.Event], None] | None = None
        self.on_click: Callable[[MarkerCluster, tk.Event], None] | None = None
        self.on_menu: Callable[[MarkerCluster, tk.Event], None] | None = None

    @property
    def item_count(self) -> int:
        return 3 * len(self._slots)

    def draw(self, clusters: Sequence[MarkerCluster], mapping: PlotMapping) -> None:
        self._clusters = list(clusters)
        while len(self._slots) < len(clusters):
            self._slots.append(self._create_slot(len(self._slots)))
        for index, (line, flag, count) in enumerate(self._slots):
            if index >= len(clusters):
                for item in (line, flag, count):
                    self._canvas.itemconfigure(item, state="hidden")
                continue
            cluster = clusters[index]
            x, top, bottom = cluster.x, mapping.top, mapping.bottom
            self._canvas.coords(line, x, top, x, bottom)
            self._canvas.itemconfigure(line, fill=cluster.colour, state="normal")
            self._canvas.coords(
                flag, x, top, x + FLAG_SIZE * 1.4, top + FLAG_SIZE / 2, x, top + FLAG_SIZE,
            )
            self._canvas.itemconfigure(
                flag, fill=cluster.colour, outline=cluster.colour, state="normal",
            )
            if len(cluster.notes) > 1:
                self._canvas.coords(count, x + FLAG_SIZE * 1.6, top + FLAG_SIZE / 2)
                self._canvas.itemconfigure(
                    count, text=str(len(cluster.notes)), fill=cluster.colour,
                    state="normal",
                )
            else:
                self._canvas.itemconfigure(count, state="hidden")
            for item in (line, flag, count):
                self._canvas.tag_raise(item)

    def hide_all(self) -> None:
        self._clusters = []
        for slot in self._slots:
            for item in slot:
                self._canvas.itemconfigure(item, state="hidden")

    def _create_slot(self, index: int) -> tuple[int, int, int]:
        tag = f"{MARKER_TAG}-{index}"
        tags = (MARKER_TAG, tag)
        line = self._canvas.create_line(
            0, 0, 0, 0, dash=MARKER_DASH, width=1, activewidth=3,
            state="hidden", tags=tags,
        )
        flag = self._canvas.create_polygon(0, 0, 0, 0, 0, 0, state="hidden", tags=tags)
        count = self._canvas.create_text(
            0, 0, anchor="w", font=BODY_FONT, state="hidden", tags=tags,
        )
        for sequence, handler in (
            ("<Enter>", lambda event: self._dispatch(self.on_enter, index, event)),
            ("<Leave>", lambda event: self._dispatch(self.on_leave, index, event)),
            ("<Button-1>", lambda event: self._dispatch(self.on_click, index, event)),
            ("<Button-3>", lambda event: self._dispatch(self.on_menu, index, event)),
        ):
            self._canvas.tag_bind(tag, sequence, handler)
        return (line, flag, count)

    def _dispatch(self, handler, index: int, event: tk.Event) -> str | None:
        if handler is None or index >= len(self._clusters):
            return None
        handler(self._clusters[index], event)
        return "break"


class NotePopup:
    """Small borderless summary shown beside the cursor; a click pins it."""

    def __init__(self, parent: tk.Misc) -> None:
        self._parent = parent
        self._window: tk.Toplevel | None = None
        self.pinned = False

    @property
    def is_shown(self) -> bool:
        return self._window is not None and bool(self._window.winfo_exists())

    def show(self, text: str, x_root: int, y_root: int, *, qc: bool = False) -> None:
        if not self.is_shown:
            window = tk.Toplevel(self._parent)
            window.wm_overrideredirect(True)
            window.attributes("-topmost", True)
            frame = tk.Frame(window, background=NAVY_INK, padx=1, pady=1)
            frame.pack()
            self._label = tk.Label(
                frame, justify="left", anchor="w", font=BODY_FONT,
                background="#FFFDF5", foreground=NAVY_INK,
                padx=8, pady=6, wraplength=320,
            )
            self._label.pack()
            self._hint = tk.Label(
                frame, justify="left", anchor="w", font=("Segoe UI", 8),
                background="#FFFDF5", foreground=MUTED_TEXT, padx=8,
            )
            self._hint.pack(fill="x")
            window.bind("<Escape>", lambda _event: self.hide(force=True))
            self._window = window
        self._label.configure(text=text, foreground=ERROR_TEXT if qc else NAVY_INK)
        self._hint.configure(
            text="Pinned — click elsewhere or Esc to close" if self.pinned
            else "Click to pin  •  right-click to edit, move or delete"
        )
        assert self._window is not None
        self._window.wm_geometry(f"+{x_root + 14}+{y_root + 12}")
        self._window.deiconify()
        self._window.lift()

    def hide(self, *, force: bool = False) -> None:
        if self.pinned and not force:
            return
        self.pinned = False
        if self.is_shown:
            assert self._window is not None
            self._window.destroy()
        self._window = None
