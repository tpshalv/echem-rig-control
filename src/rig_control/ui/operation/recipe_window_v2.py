"""The v2 recipe builder: the same editor, with the plan drawn as graphs.

Editing, validation, running and run edits are all inherited from
``RecipeBuilderWindow``; this adds two views of the plan:

* Steps tab: a one-strip preview above the table, showing one setting that
  changes (picked from a list), with the settings that are only held named
  in a line above it.
* Timeline tab: every setting as a strip (held ones as thin faint rows) above
  the timeline table.

Selecting a step highlights it on both graphs; clicking a graph selects the
step.  On the Timeline tab, a row in the table and a moment on the graph find
each other.
"""

import tkinter as tk
from bisect import bisect_right
from dataclasses import replace
from tkinter import ttk

from rig_control.recipes import preview_recipe
from rig_control.recipes.graph import RecipeGraph, SettingLine, StepKey, recipe_graph
from rig_control.recipes.outline import LoopEnd, from_outline, partner
from rig_control.ui.operation.recipe_graph import RecipeGraphView
from rig_control.ui.operation.recipe_settings import format_value, spec_for
from rig_control.ui.operation.recipe_window import RecipeBuilderWindow

_LineKey = tuple[str, str]
_TIMELINE_LIMIT = 500


def _key(line: SettingLine) -> _LineKey:
    return line.device_id, line.setting


class RecipeBuilderWindowV2(RecipeBuilderWindow):

    def __init__(self, root: tk.Toplevel, *args, **kwargs) -> None:
        self._graph_view: RecipeGraphView | None = None
        self._full_view: RecipeGraphView | None = None
        self._graph: RecipeGraph | None = None
        self._graph_message = ""
        #: The setting the Steps preview shows, kept across edits while it still changes.
        self._preview_key: _LineKey | None = None
        self._preview_names: dict[str, _LineKey] = {}
        #: Where each timeline row runs, (start, end) seconds, in table order.
        self._timeline_spans: list[tuple[float, float]] = []
        #: The step the marked moment belongs to; picking any other step clears the mark.
        self._moment_step: StepKey | None = None
        super().__init__(root, *args, **kwargs)
        root.geometry("1180x820")

    def _build_body(self) -> None:
        super()._build_body()
        self._build_preview(self._tree.master)
        self._build_full_graph(self._timeline_tab)

    @staticmethod
    def _shift_down(frame: tk.Misc) -> None:
        """Move what is already gridded in ``frame`` down a row, leaving row 0 free."""
        for child in frame.grid_slaves():
            child.grid_configure(row=int(child.grid_info()["row"]) + 1)
        weights = [int(frame.rowconfigure(row)["weight"]) for row in range(4)]
        for row, weight in enumerate(weights):
            frame.rowconfigure(row + 1, weight=weight)
        frame.rowconfigure(0, weight=0)

    def _build_preview(self, steps_tab: tk.Misc) -> None:
        self._shift_down(steps_tab)
        top = ttk.Frame(steps_tab)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        top.columnconfigure(2, weight=1)
        ttk.Label(top, text="Plan", style="Heading.TLabel").grid(row=0, column=0, sticky="w",
                                                               padx=(0, 10))
        self._preview_choice = ttk.Combobox(top, state="readonly", width=30)
        self._preview_choice.grid(row=0, column=1, sticky="w")
        self._preview_choice.bind("<<ComboboxSelected>>", lambda _e: self._choose_preview())
        self._held_label = ttk.Label(top, style="Caption.TLabel", justify="left")
        self._held_label.grid(row=0, column=2, sticky="ew", padx=(12, 0))
        self._graph_view = RecipeGraphView(
            top, on_pick=lambda step, _s: self._pick_from_graph(step), preview=True,
            describe=self._graph_describe, to_display=self._graph_to_display,
            value_text=self._graph_value_text,
        )
        self._graph_view.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))

    def _build_full_graph(self, timeline_tab: tk.Misc) -> None:
        self._shift_down(timeline_tab)
        top = ttk.Frame(timeline_tab)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, style="Caption.TLabel", text=(
            "Shaded = measuring  ·  thin grey rows are held all run  ·  click the graph or a row "
            "below to find it in the other  ·  hover to read values"
        )).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._full_view = RecipeGraphView(
            top, on_pick=self._pick_from_full_graph, describe=self._graph_describe,
            to_display=self._graph_to_display, value_text=self._graph_value_text,
        )
        self._full_view.grid(row=1, column=0, sticky="ew")
        self._timeline.bind("<<TreeviewSelect>>", lambda _e: self._on_timeline_select(), add="+")

    # --- keeping the graphs in step ---

    def _refresh(self) -> None:
        super()._refresh()
        self._refresh_graph()

    def _refresh_graph(self) -> None:
        if self._graph_view is None or self._full_view is None:
            return
        try:
            self._graph = recipe_graph(from_outline(self._rows), seed=self._order_seed)
            self._graph_message = ""
        except ValueError as error:
            self._graph, self._graph_message = None, f"No graph: {error}"
        self._full_view.show(self._graph, self._graph_message)
        self._refresh_preview()
        key = self._selected_key()
        self._graph_view.highlight(key)
        self._full_view.highlight(key)

    def _refresh_preview(self) -> None:
        graph = self._graph
        if graph is None or not graph.lines:
            self._preview_names = {}
            self._preview_choice.configure(values=(), state="disabled")
            self._preview_choice.set("")
            self._held_label.configure(text="")
            self._graph_view.show(graph, self._graph_message)
            return
        # Settings that change are the ones worth a graph; if none do, any can be shown.
        choices = graph.varying or graph.lines
        self._preview_names = {self._graph_describe(line)[0]: _key(line) for line in choices}
        keys = [_key(line) for line in choices]
        if self._preview_key not in keys:
            self._preview_key = keys[0]
        self._preview_choice.configure(values=tuple(self._preview_names), state="readonly")
        shown = next(line for line in choices if _key(line) == self._preview_key)
        self._preview_choice.set(self._graph_describe(shown)[0])
        if graph.varying:
            held = "  ·  ".join(f"{self._graph_describe(line)[0]} {self._graph_value_text(line, line.points[0][1])}"
                                for line in graph.held)
            others = len(graph.varying) - 1
            parts = [f"{others} more change{'s' if others != 1 else ''} (see Timeline)"] if others else []
            if held:
                parts.append("Held: " + held)
            self._held_label.configure(text="   ".join(parts))
        else:
            self._held_label.configure(text="Nothing changes during this recipe: every setting is held.")
        self._graph_view.show(replace(graph, lines=(shown,)), self._graph_message)

    def _choose_preview(self) -> None:
        self._preview_key = self._preview_names.get(self._preview_choice.get(), self._preview_key)
        self._refresh_preview()

    def _show_quick(self) -> None:
        super()._show_quick()
        key = self._selected_key()
        if key != self._moment_step:
            self._moment_step = None
        for view in (self._graph_view, self._full_view):
            if view is not None:
                view.highlight(key)
                if self._moment_step is None:
                    view.mark_moment(None)

    def _selected_key(self) -> StepKey | None:
        index = self._selected
        keys = getattr(self, "_keys", None)
        if index is None or keys is None or index >= len(self._rows) or index >= len(keys):
            return None
        if isinstance(self._rows[index], LoopEnd):
            index = partner(self._rows, index)
        return keys[index]

    def _pick_from_graph(self, step: StepKey) -> None:
        keys = getattr(self, "_keys", [])
        if step not in keys:
            return
        iid = str(keys.index(step))
        if not self._tree.exists(iid):
            return
        self._select_line(iid)
        self._tree.selection_set(iid)
        self._tree.see(iid)
        self._show_quick()

    # --- the timeline table and the full graph ---

    def _refresh_timeline(self) -> None:
        super()._refresh_timeline()
        self._timeline_spans = []
        if self._run_edit or not self._rows or self._graph is None:
            return  # A run edit lists what is left from now, not the recipe's own times.
        try:
            rows = preview_recipe(self._recipe(), limit=_TIMELINE_LIMIT, seed=self._order_seed)
        except ValueError:
            return
        starts = [row.planned_start_seconds for row in rows]
        ends = starts[1:] + [self._graph.total_seconds]
        if len(starts) == len(self._timeline.get_children()):
            self._timeline_spans = list(zip(starts, ends))

    def _on_timeline_select(self) -> None:
        selection = self._timeline.selection()
        children = self._timeline.get_children()
        if not selection or not self._timeline_spans or self._graph is None:
            return
        span = self._timeline_spans[children.index(selection[0])]
        step = self._graph.step_at(span[0] if span[0] == span[1] else (span[0] + span[1]) / 2)
        self._moment_step = step
        if step is not None:
            self._pick_from_graph(step)
        for view in (self._graph_view, self._full_view):
            view.mark_moment(span)

    def _pick_from_full_graph(self, step: StepKey, seconds: float) -> None:
        if not self._timeline_spans:
            self._pick_from_graph(step)
            return
        # The last row starting by then: a Set and the wait after it share a start, the wait wins.
        index = max(bisect_right([start for start, _end in self._timeline_spans], seconds) - 1, 0)
        iid = self._timeline.get_children()[index]
        self._timeline.selection_set(iid)
        self._timeline.see(iid)
        self._on_timeline_select()

    # --- how values read on the graph: the builder's own display units ---

    def _graph_describe(self, line: SettingLine) -> tuple[str, str]:
        spec = spec_for(line.setting)
        return self._target_text(line.device_id, line.setting), self._value_unit(line.device_id, spec)

    def _graph_to_display(self, line: SettingLine, value: object) -> object:
        return self._units.to_display(value, spec_for(line.setting))

    def _graph_value_text(self, line: SettingLine, value: object) -> str:
        spec = spec_for(line.setting)
        if isinstance(value, bool):
            return "On" if value else "Off"
        if isinstance(value, str):
            return value.replace("constant_", "").replace("_", " ")
        unit = self._value_unit(line.device_id, spec)
        shown = format_value(self._units.to_display(value, spec))
        return f"{shown} {unit}" if unit else shown
