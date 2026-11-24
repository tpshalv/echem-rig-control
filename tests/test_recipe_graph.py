"""The recipe graph: the plan as lines over time, drawn above the recipe builder."""
import tkinter as tk

from rig_control.recipes import (
    Assignment, LoopOrder, LoopStep, Ramp, RampKind, Recipe, SetStep, WaitPurpose, WaitStep,
)
from rig_control.recipes.graph import recipe_graph
from rig_control.ui.operation.recipe_graph import nice_step, time_text


def sweep():
    return (
        SetStep((Assignment("mfc", "flow_sccm", 50.0, "sccm"),
                 Assignment("supply", "output_enabled", True))),
        WaitStep(600, WaitPurpose.SETTLE),
        LoopStep("supply", "current_setpoint", (1.0, 2.0, 3.0), "A",
                 steps=(WaitStep(60, WaitPurpose.SETTLE), WaitStep(240, WaitPurpose.MEASURE))),
        SetStep((Assignment("supply", "output_enabled", False),)),
    )


def test_each_setting_is_a_line_holding_its_value_until_the_next_change():
    graph = recipe_graph(sweep())
    lines = {(line.device_id, line.setting): line for line in graph.lines}

    current = lines[("supply", "current_setpoint")]
    assert current.points == ((600.0, 1.0), (900.0, 2.0), (1200.0, 3.0))
    assert current.value_at(0) is None and current.value_at(1000) == 2.0
    assert lines[("supply", "output_enabled")].points == ((0.0, True), (1500.0, False))
    assert graph.total_seconds == 1500.0


def test_waits_are_kept_with_their_purpose_and_step():
    graph = recipe_graph(sweep())
    measures = [w for w in graph.waits if w.purpose is WaitPurpose.MEASURE]
    assert [(w.start, w.end) for w in measures] == [(660, 900), (960, 1200), (1260, 1500)]
    assert {w.step for w in measures} == {(2, 1)}


def test_a_step_can_be_found_by_time_and_its_spans_merged():
    graph = recipe_graph(sweep())
    assert graph.step_at(100) == (1,)
    assert graph.step_at(700) == (2, 1)
    # The whole loop, every pass and wait inside it, is one run of time.
    assert graph.spans_of((2,)) == ((600.0, 1500.0),)


def test_ramps_are_drawn_as_the_steps_that_run():
    steps = (
        SetStep((Assignment("mfc", "flow_sccm", 0.0, "sccm"),)),
        SetStep((Assignment("mfc", "flow_sccm", 40.0, "sccm",
                            ramp=Ramp(RampKind.STEP_COUNT, dwell_seconds=30, count=4)),)),
        WaitStep(60, WaitPurpose.MEASURE),
    )
    line = recipe_graph(steps).lines[0]
    # 0 then the ramp's first step at the same instant: the graph shows what holds.
    assert line.points == ((0.0, 10.0), (30.0, 20.0), (60.0, 30.0), (90.0, 40.0))


def test_a_shuffled_loop_is_drawn_in_the_order_it_will_run():
    steps = (LoopStep("supply", "current_setpoint", (1.0, 2.0, 3.0, 4.0), "A",
                      steps=(WaitStep(10, WaitPurpose.MEASURE),), order=LoopOrder.SHUFFLE_ONCE),)
    drawn = [value for _t, value in recipe_graph(steps, seed=7).lines[0].points]
    assert sorted(drawn) == [1.0, 2.0, 3.0, 4.0]
    assert drawn == [value for _t, value in recipe_graph(steps, seed=7).lines[0].points]


def test_a_very_long_recipe_is_cut_short_and_says_so():
    steps = (LoopStep("mfc", "flow_sccm", tuple(float(v) for v in range(500)), "sccm",
                      steps=(WaitStep(1, WaitPurpose.MEASURE),)),)
    graph = recipe_graph(steps, item_limit=100)
    assert graph.truncated and graph.total_seconds < 500


def test_axis_ticks_are_round_and_readable():
    assert nice_step(3600, 6) == 600
    assert nice_step(45, 10) == 5
    assert [time_text(s) for s in (30, 600, 630, 3600, 5400)] == ["30 s", "10 min", "10:30", "1 h", "1 h 30"]


def test_builder_draws_the_graph_and_selecting_a_step_highlights_it():
    from rig_control.rig_profile import DeviceBackend, DeviceCapability, DeviceRole, RigProfile
    from rig_control.ui.operation.recipe_window_v2 import RecipeBuilderWindowV2
    from tests.test_recipes import make_runner

    roles = (DeviceRole("supply", "Supply", DeviceCapability.DC_POWER_SUPPLY, "simulated", DeviceBackend.SIMULATED),
             DeviceRole("mfc", "MFC", DeviceCapability.MASS_FLOW_CONTROLLER, "simulated", DeviceBackend.SIMULATED))
    root = tk.Tk(); root.withdraw()
    try:
        runner, _supply, _mfc = make_runner()
        window = RecipeBuilderWindowV2(tk.Toplevel(root), runner, RigProfile("rig", "Rig", roles))
        window._load_rows(Recipe("Sweep", sweep()))
        assert window._graph is not None and window._graph.total_seconds == 1500.0

        # Picking the loop's measure wait on the graph selects that row in the table.
        window._pick_from_graph((2, 1))
        assert window._tree.selection() == (str(window._keys.index((2, 1))),)
        assert window._graph_view._selected == (2, 1)
    finally:
        root.destroy()


def test_settings_that_change_are_told_apart_from_ones_only_held():
    graph = recipe_graph(sweep())
    changing = {(line.device_id, line.setting) for line in graph.varying}
    held = {(line.device_id, line.setting) for line in graph.held}
    assert changing == {("supply", "current_setpoint"), ("supply", "output_enabled")}
    assert held == {("mfc", "flow_sccm")}


def test_steps_preview_shows_one_changing_setting_and_the_timeline_links_to_its_table():
    from rig_control.rig_profile import DeviceBackend, DeviceCapability, DeviceRole, RigProfile
    from rig_control.ui.operation.recipe_window_v2 import RecipeBuilderWindowV2
    from tests.test_recipes import make_runner

    roles = (DeviceRole("supply", "Supply", DeviceCapability.DC_POWER_SUPPLY, "simulated", DeviceBackend.SIMULATED),
             DeviceRole("mfc", "MFC", DeviceCapability.MASS_FLOW_CONTROLLER, "simulated", DeviceBackend.SIMULATED))
    root = tk.Tk(); root.withdraw()
    try:
        runner, _supply, _mfc = make_runner()
        window = RecipeBuilderWindowV2(tk.Toplevel(root), runner, RigProfile("rig", "Rig", roles))
        window._load_rows(Recipe("Sweep", sweep()))

        # The preview offers only what changes; the held flow is named above it.
        assert len(window._preview_names) == 2
        assert ("mfc", "flow_sccm") not in window._preview_names.values()
        assert len(window._graph_view._graph.lines) == 1
        assert "Held:" in window._held_label.cget("text")
        # The full graph has every setting.
        assert len(window._full_view._graph.lines) == 3

        # Clicking the timeline graph in the second current's measurement picks
        # that row in the table, marks the moment and selects its step.
        window._tabs.select(window._timeline_tab)
        window._refresh_timeline()
        window._pick_from_full_graph((2, 1), 1000.0)
        row = window._timeline.selection()[0]
        start, end = window._timeline_spans[window._timeline.get_children().index(row)]
        assert start <= 1000.0 < end
        assert window._full_view._moment == (start, end)
        assert window._full_view._selected == (2, 1)
    finally:
        root.destroy()
