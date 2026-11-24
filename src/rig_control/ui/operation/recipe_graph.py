"""The recipe drawn as stacked strips: one per setting, sharing a time axis.

The full graph gives settings that change a tall strip and settings that are
only held a thin faint row; the preview draws one strip.

Numbers are step lines, On/Off is a filled band, and modes are labelled
segments.  Measurement waits are shaded across every strip, so it is plain
where data is taken and where the rig is only settling.  The selected step
is highlighted; clicking the graph picks the step running at that time, and
hovering shows every setting's value at that moment.
"""

import tkinter as tk
from collections.abc import Callable

from rig_control.recipes.graph import RecipeGraph, SettingLine, StepKey
from rig_control.recipes.model import WaitPurpose
from rig_control.ui.common.theme_v2 import (
    ACCENT, ACCENT_SOFT, BORDER_SOFT, CAPTION_FONT, FAINT, GOOD_SOFT, GOOD_TEXT, MUTED,
    NOTICE_SOFT, PANEL, SPLITTER, TEXT, WHITE,
)

_LEFT = 178
_RIGHT = 70
_TOP = 6
_AXIS = 20
#: A strip's height per unit of weight: settings that change weigh 2, held ones 1.
_UNIT_MIN = 16
_UNIT_MAX = 29
#: The tallest the full graph asks to be before its strips start to shrink.
_FULL_HEIGHT_LIMIT = 400
#: The preview's one strip.
_PREVIEW_STRIP = 64
_HIGHLIGHT = "#f7dc7a"
#: One moment picked in the timeline table, drawn over the step highlight.
_MOMENT = "#d08a00"
_MODE_FILLS = ("#e3edf5", "#efe6f5", "#e6f2e6", "#f6ece0")


def nice_step(span: float, ticks: int) -> float:
    """A round tick interval in seconds giving about ``ticks`` ticks over ``span``."""
    for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800,
                 21600, 43200, 86400):
        if span / step <= ticks:
            return float(step)
    return 86400.0 * max(1, round(span / ticks / 86400))


def time_text(seconds: float) -> str:
    """Axis label: ``45 s``, ``12 min``, ``1 h 30``."""
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes} min" if not rest else f"{minutes}:{rest:02d}"
    hours, rest = divmod(seconds, 3600)
    return f"{hours} h" if rest < 60 else f"{hours} h {rest // 60:02d}"


def _weight(line: SettingLine) -> int:
    return 2 if line.varies else 1


class RecipeGraphView(tk.Canvas):
    """Draws a ``RecipeGraph``; ``on_pick`` gets the step clicked and the time clicked.

    ``preview`` draws one strip at a fixed height (the caller passes a graph
    holding just that line); otherwise every line gets a strip, settings that
    change twice the height of held ones.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_pick: Callable[[StepKey, float], None],
        describe: Callable[[SettingLine], tuple[str, str]],
        to_display: Callable[[SettingLine, object], object],
        value_text: Callable[[SettingLine, object], str],
        preview: bool = False,
    ) -> None:
        super().__init__(parent, background=PANEL, highlightthickness=1,
                         highlightbackground=BORDER_SOFT, borderwidth=0, height=120)
        self._on_pick = on_pick
        self._describe = describe
        self._to_display = to_display
        self._value_text = value_text
        self._preview = preview
        self._graph: RecipeGraph | None = None
        self._message = ""
        self._selected: StepKey | None = None
        #: One moment picked in the timeline table: (start, end) seconds.
        self._moment: tuple[float, float] | None = None
        self._strips: list[tuple[SettingLine, float, float]] = []
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _e: self.delete("hover"))

    # --- inputs ---

    def show(self, graph: RecipeGraph | None, message: str = "") -> None:
        self._graph, self._message = graph, message
        lines = graph.lines if graph is not None else ()
        if self._preview or not lines:
            wanted = _TOP + _AXIS + (_PREVIEW_STRIP if lines else _PREVIEW_STRIP // 2)
        else:
            weight = sum(_weight(line) for line in lines)
            wanted = min(_TOP + _AXIS + weight * _UNIT_MAX,
                         max(_FULL_HEIGHT_LIMIT, _TOP + _AXIS + weight * _UNIT_MIN))
        if int(self.cget("height")) != wanted:
            self.configure(height=wanted)
        self._draw()

    def highlight(self, step: StepKey | None) -> None:
        if step != self._selected:
            self._selected = step
            self._draw()

    def mark_moment(self, span: tuple[float, float] | None) -> None:
        """Outline one moment of the run, such as a row picked in the timeline table."""
        if span != self._moment:
            self._moment = span
            self._draw()

    # --- geometry ---

    def _x(self, seconds: float) -> float:
        width = max(self.winfo_width() - _LEFT - _RIGHT, 10)
        total = self._graph.total_seconds if self._graph else 1.0
        return _LEFT + width * (seconds / total if total > 0 else 0)

    def _seconds(self, x: float) -> float:
        width = max(self.winfo_width() - _LEFT - _RIGHT, 10)
        total = self._graph.total_seconds if self._graph else 0.0
        return max(0.0, min(total, (x - _LEFT) / width * total))

    # --- drawing ---

    def _draw(self) -> None:
        self.delete("all")
        self._strips = []
        width, height = self.winfo_width(), self.winfo_height()
        graph = self._graph
        if width < 50:
            return
        if graph is None or not graph.lines or graph.total_seconds <= 0:
            text = self._message or ("Nothing to draw yet: add a Set and a Wait." if graph is None
                                     or not graph.lines else "Add a Wait so the recipe takes time.")
            self.create_text(12, height / 2, text=text, anchor="w", fill=MUTED, font=CAPTION_FONT)
            return
        bottom = height - _AXIS
        weights = [_weight(line) for line in graph.lines]
        if self._preview:
            weights = [1] * len(weights)
            unit = (bottom - _TOP) / len(weights)
        else:
            unit = max(_UNIT_MIN, min(_UNIT_MAX, (bottom - _TOP) / sum(weights)))
        right = width - _RIGHT
        # The selected step, then measurement shading over it, behind the lines.
        selected = graph.spans_of(self._selected) if self._selected is not None else ()
        for start, end in selected:
            x0, x1 = self._x(start), self._x(end)
            self.create_rectangle(x0, _TOP, max(x1, x0 + 2), bottom, fill=NOTICE_SOFT, width=0)
        for span in graph.waits:
            if span.purpose is WaitPurpose.MEASURE:
                self.create_rectangle(self._x(span.start), _TOP, self._x(span.end), bottom,
                                      fill=ACCENT_SOFT, width=0)
        for start, end in selected:
            x0, x1 = self._x(start), self._x(end)
            self.create_rectangle(x0, _TOP, max(x1, x0 + 2), bottom, outline=_HIGHLIGHT, width=2)
        if self._moment is not None:
            x0, x1 = self._x(self._moment[0]), self._x(self._moment[1])
            self.create_rectangle(x0, _TOP, max(x1, x0 + 2), bottom, outline=_MOMENT, width=2)
        for seconds, _index in graph.step_starts[1:]:
            x = self._x(seconds)
            self.create_line(x, _TOP, x, bottom, fill=SPLITTER, dash=(2, 3))
        top = float(_TOP)
        for number, (line, weight) in enumerate(zip(graph.lines, weights)):
            strip_bottom = top + weight * unit
            self._strips.append((line, top, strip_bottom))
            if number:
                self.create_line(0, top, width, top, fill=BORDER_SOFT)
            if self._preview or line.varies:
                self._draw_strip(line, top, strip_bottom, right)
            else:
                self._draw_held(line, top, strip_bottom, right)
            top = strip_bottom
        self.create_line(_LEFT, bottom, right, bottom, fill=BORDER_SOFT)
        self._draw_axis(graph.total_seconds, bottom, right)
        if graph.truncated:
            self.create_text(right, _TOP + 2, anchor="ne", fill=MUTED, font=CAPTION_FONT,
                             text="Very long recipe: showing the start only")

    def _draw_strip(self, line: SettingLine, top: float, bottom: float, right: float) -> None:
        name, unit = self._describe(line)
        self.create_text(10, top + 4, text=name, anchor="nw", fill=TEXT, font=CAPTION_FONT,
                         width=_LEFT - 18)
        end = self._graph.total_seconds if self._graph else 0.0
        segments = [(t, (line.points[i + 1][0] if i + 1 < len(line.points) else end), v)
                    for i, (t, v) in enumerate(line.points)]
        if all(isinstance(v, bool) for _t, _e, v in segments):
            for start, stop, value in segments:
                if value:
                    x0, x1 = self._x(start), self._x(stop)
                    self.create_rectangle(x0, top + 8, max(x1, x0 + 1), bottom - 8, fill=GOOD_SOFT,
                                          outline=GOOD_TEXT)
                    if x1 - x0 > 26:
                        self.create_text((x0 + x1) / 2, (top + bottom) / 2, text="On",
                                         fill=GOOD_TEXT, font=CAPTION_FONT)
            return
        numbers = [(s, e, self._to_display(line, v)) for s, e, v in segments]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for _s, _e, v in numbers):
            for index, (start, stop, value) in enumerate(segments):
                x0, x1 = self._x(start), self._x(stop)
                self.create_rectangle(x0, top + 8, max(x1, x0 + 1), bottom - 8,
                                      fill=_MODE_FILLS[index % len(_MODE_FILLS)], outline=BORDER_SOFT)
                text = self._value_text(line, value)
                if x1 - x0 > 7 * len(text):
                    self.create_text((x0 + x1) / 2, (top + bottom) / 2, text=text, fill=TEXT,
                                     font=CAPTION_FONT)
            return
        values = [float(v) for _s, _e, v in numbers]
        low, high = min(values), max(values)
        if unit:
            low_text = self._value_text(line, segments[values.index(low)][2])
            high_text = self._value_text(line, segments[values.index(high)][2])
            self.create_text(10, bottom - 4, anchor="sw", fill=FAINT, font=CAPTION_FONT,
                             text=low_text if low == high else f"{low_text} – {high_text}")
        pad = 6
        def y(value: float) -> float:
            if high == low:
                return (top + bottom) / 2
            return bottom - pad - (value - low) / (high - low) * (bottom - top - 2 * pad)
        coords: list[float] = []
        for (start, stop, _v), value in zip(segments, values):
            x0, x1, level = self._x(start), self._x(stop), y(value)
            if coords:
                coords += [x0, level]
            coords += [x0, level, x1, level]
        if len(coords) >= 4:
            self.create_line(*coords, fill=ACCENT, width=2)
        # Values over the line where there is room to read them.
        last_label = -1e9
        for (start, stop, value), shown in zip(segments, values):
            x0, x1 = self._x(start), self._x(stop)
            text = self._value_text(line, value)
            if x1 - x0 > 6 * len(text) + 6 and x0 - last_label > 6 * len(text):
                level = y(shown)
                above = level - 7 if level - 14 > top else level + 8
                self.create_text(x0 + 3, above, text=text, anchor="w", fill=MUTED,
                                 font=CAPTION_FONT)
                last_label = x0

    def _draw_held(self, line: SettingLine, top: float, bottom: float, right: float) -> None:
        """A setting the recipe only holds: its name and value on one faint row."""
        name, _unit = self._describe(line)
        middle = (top + bottom) / 2
        self.create_text(10, middle, text=name, anchor="w", fill=MUTED, font=CAPTION_FONT,
                         width=_LEFT - 18)
        start, value = line.points[0]
        x0 = self._x(start)
        self.create_line(x0, middle, right, middle, fill=BORDER_SOFT, width=2)
        text = self._value_text(line, value)
        self.create_rectangle(x0 + 2, middle - 7, x0 + 10 + 6 * len(text), middle + 7,
                              fill=PANEL, width=0)
        self.create_text(x0 + 6, middle, text=text, anchor="w", fill=MUTED, font=CAPTION_FONT)

    def _draw_axis(self, total: float, bottom: float, right: float) -> None:
        width = right - _LEFT
        step = nice_step(total, max(2, int(width / 80)))
        tick = 0.0
        while tick <= total + 1e-9:
            x = self._x(tick)
            self.create_line(x, bottom, x, bottom + 3, fill=FAINT)
            self.create_text(x, bottom + 4, text=time_text(tick), anchor="n", fill=MUTED,
                             font=CAPTION_FONT)
            tick += step
        self.create_text(right + 6, bottom + 4, text=f"{time_text(total)} total", anchor="nw",
                         fill=MUTED, font=CAPTION_FONT)

    # --- interaction ---

    def _click(self, event: tk.Event) -> None:
        if self._graph is None or event.x < _LEFT:
            return
        seconds = self._seconds(event.x)
        step = self._graph.step_at(seconds)
        if step is not None:
            self._on_pick(step, seconds)

    def _hover(self, event: tk.Event) -> None:
        self.delete("hover")
        graph = self._graph
        if graph is None or not self._strips or event.x < _LEFT or event.x > self.winfo_width() - _RIGHT:
            return
        seconds = self._seconds(event.x)
        bottom = self.winfo_height() - _AXIS
        self.create_line(event.x, _TOP, event.x, bottom, fill=MUTED, dash=(1, 2), tags="hover")
        self.create_rectangle(event.x - 26, bottom + 2, event.x + 26, bottom + 17, fill=WHITE,
                              outline=BORDER_SOFT, tags="hover")
        self.create_text(event.x, bottom + 4, text=time_text(seconds), anchor="n", fill=TEXT,
                         font=CAPTION_FONT, tags="hover")
        right = self.winfo_width() - _RIGHT
        for line, top, strip_bottom in self._strips:
            value = line.value_at(seconds)
            text = "—" if value is None else self._value_text(line, value)
            self.create_text(right + 6, (top + strip_bottom) / 2, text=text, anchor="w",
                             fill=ACCENT, font=CAPTION_FONT, tags="hover", width=_RIGHT - 8)
