"""Shuffled loop orders: optional per loop, drawn from a recorded seed."""
import json

from rig_control.data.in_memory_writer import InMemoryExperimentWriter
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.recipes import (
    LoopOrder, LoopStep, Recipe, WaitPurpose, WaitStep, estimate_recipe, preview_recipe, recipe_from_dict,
    recipe_to_dict,
)
from rig_control.recipes.execution import RecipeRunState
from rig_control.recipes.plan import PlannedLoopOrder, PlannedSet, has_shuffled_loops, plan
from tests.test_recipes import commands, make_runner

FLOWS = (10.0, 20.0, 30.0, 40.0, 50.0)
CURRENTS = (1.0, 2.0, 3.0)


def sweep(order: LoopOrder, inner=(WaitStep(60, WaitPurpose.MEASURE),)) -> Recipe:
    """Currents low to high, as listed; at each, every flow in ``order``."""
    return Recipe("sweep", steps=(
        LoopStep("supply", "current_limit", CURRENTS, steps=(
            LoopStep("mfc", "flow", FLOWS, steps=inner, order=order),
        )),
    ))


def flow_orders(recipe: Recipe, seed: int) -> list[tuple[float, ...]]:
    flows = [a.value for item in plan(recipe.steps, seed=seed) if isinstance(item, PlannedSet)
             for a in item.writes if a.setting == "flow"]
    return [tuple(flows[i:i + len(FLOWS)]) for i in range(0, len(flows), len(FLOWS))]


def currents(recipe: Recipe, seed: int) -> list[float]:
    return [a.value for item in plan(recipe.steps, seed=seed) if isinstance(item, PlannedSet)
            for a in item.writes if a.setting == "current_limit"]


def test_loops_run_as_listed_by_default_whatever_the_seed():
    recipe = sweep(LoopOrder.AS_LISTED)
    assert recipe.steps[0].steps[0].order is LoopOrder.AS_LISTED
    assert not has_shuffled_loops(recipe.steps)
    for seed in (0, 1, 999_999):
        assert flow_orders(recipe, seed) == [FLOWS] * len(CURRENTS)
        assert not any(isinstance(item, PlannedLoopOrder) for item in plan(recipe.steps, seed=seed))


def test_shuffled_each_time_gives_every_outer_value_its_own_order_and_leaves_the_outer_loop_alone():
    recipe = sweep(LoopOrder.SHUFFLE_EACH)
    assert has_shuffled_loops(recipe.steps)
    differs = False
    for seed in range(20):
        orders = flow_orders(recipe, seed)
        assert all(sorted(o) == list(FLOWS) for o in orders)  # Every value once, none lost.
        assert currents(recipe, seed) == list(CURRENTS)  # The listed outer loop keeps its order.
        differs |= len(set(orders)) > 1
    assert differs


def test_shuffled_once_reuses_one_order_and_seeds_change_it():
    recipe = sweep(LoopOrder.SHUFFLE_ONCE)
    seen = set()
    for seed in range(20):
        orders = flow_orders(recipe, seed)
        assert len(set(orders)) == 1 and sorted(orders[0]) == list(FLOWS)
        seen.add(orders[0])
    assert len(seen) > 1


def test_the_same_seed_always_gives_the_same_plan():
    recipe = sweep(LoopOrder.SHUFFLE_EACH)
    assert flow_orders(recipe, 4321) == flow_orders(recipe, 4321)
    # Fixed here so a change to how orders are drawn, which would stop old
    # seeds from repeating their runs, is noticed.
    assert flow_orders(recipe, 4321) == [
        (30.0, 20.0, 50.0, 40.0, 10.0), (40.0, 50.0, 30.0, 10.0, 20.0), (50.0, 40.0, 30.0, 20.0, 10.0)]


def test_pass_names_move_with_their_values():
    loop = LoopStep("mfc", "flow", FLOWS, steps=(WaitStep(1),), pass_names=("a", "b", "c", "d", "e"),
                    order=LoopOrder.SHUFFLE_EACH)
    rows = [r for r in preview_recipe(Recipe("names", steps=(loop,)), seed=7) if r.kind == "set"]
    names = dict(zip(FLOWS, "abcde"))
    for k, row in enumerate(rows):
        value = dict(((d, s), v) for d, s, v in row.parameters)[("mfc", "flow")]
        assert row.position == (f"flow: {names[value]} ({k + 1}/5)",)


def test_estimates_and_checks_use_the_seeded_order():
    recipe = sweep(LoopOrder.SHUFFLE_EACH)
    assert estimate_recipe(recipe, seed=3).duration_seconds == 60 * len(FLOWS) * len(CURRENTS)


def test_order_round_trips_and_files_without_it_run_as_listed():
    recipe = sweep(LoopOrder.SHUFFLE_ONCE)
    data = recipe_to_dict(recipe)
    assert recipe_from_dict(json.loads(json.dumps(data))) == recipe
    del data["steps"][0]["steps"][0]["order"]
    assert recipe_from_dict(data).steps[0].steps[0].order is LoopOrder.AS_LISTED


def test_a_run_follows_and_records_its_seeded_order():
    recipe = sweep(LoopOrder.SHUFFLE_EACH, inner=(WaitStep(0),))
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(recipe, recording_root="unused", order_seed=2024)
    assert runner.join(2).state is RecipeRunState.COMPLETED

    expected = [v for order in flow_orders(recipe, 2024) for v in order]
    assert commands(runner, "flow") == expected
    assert writer.metadata.extra["recipe_order_seed"] == 2024
    events = [json.loads(e.message) for e in writer.events]
    assert next(e for e in events if e["kind"] == "recipe_started")["order_seed"] == 2024
    orders = [e for e in events if e["kind"] == "recipe_loop_order"]
    assert [tuple(e["values"]) for e in orders] == flow_orders(recipe, 2024)
    assert orders[0]["order"] == "shuffle_each" and orders[0]["position"] == ["current limit 1 (1/3)"]


def test_a_run_without_a_seed_draws_one_and_records_it():
    runner, _supply, _mfc = make_runner()
    writer = InMemoryExperimentWriter()
    runner._recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    runner.start(sweep(LoopOrder.SHUFFLE_EACH, inner=(WaitStep(0),)), recording_root="unused")
    assert runner.join(2).state is RecipeRunState.COMPLETED
    seed = writer.metadata.extra["recipe_order_seed"]
    assert isinstance(seed, int)
    assert commands(runner, "flow") == [v for o in flow_orders(sweep(LoopOrder.SHUFFLE_EACH), seed) for v in o]


# --- Builder -----------------------------------------------------------------------

def builder():
    import tkinter as tk
    from rig_control.rig_profile import DeviceBackend, DeviceCapability, DeviceRole, RigProfile
    from rig_control.ui.operation.recipe_window import RecipeBuilderWindow

    roles = (DeviceRole("supply", "Supply", DeviceCapability.DC_POWER_SUPPLY, "simulated", DeviceBackend.SIMULATED),
             DeviceRole("mfc", "MFC", DeviceCapability.MASS_FLOW_CONTROLLER, "simulated", DeviceBackend.SIMULATED))
    root = tk.Tk(); root.withdraw()
    runner, _supply, _mfc = make_runner()
    return root, RecipeBuilderWindow(tk.Toplevel(root), runner, RigProfile("rig", "Rig", roles))


def test_builder_shows_the_order_number_only_for_shuffled_loops_and_reshuffles():
    root, window = builder()
    try:
        window._load_rows(sweep(LoopOrder.AS_LISTED)); root.update_idletasks()
        assert not window._order_row.winfo_ismapped() and not window._order_row.grid_info()

        window._load_rows(sweep(LoopOrder.SHUFFLE_EACH))
        assert window._order_row.grid_info()
        assert window._order_entry.get() == str(window._order_seed)
        loop_line = window._tree.item("1", "values")[3]
        assert "shuffled each time" in loop_line

        window._tabs.select(window._timeline_tab); window._refresh_timeline()
        def shown_flows():
            return [window._timeline.item(i, "values")[2] for i in window._timeline.get_children()
                    if "flow" in window._timeline.item(i, "values")[2]]
        assert f"order no. {window._order_seed}" in window._timeline_note.cget("text")

        window._set_entry(window._order_entry, "4321"); window._order_typed()
        assert window._order_seed == 4321
        expected = [v for o in flow_orders(sweep(LoopOrder.SHUFFLE_EACH), 4321) for v in o]
        assert [float(t.rsplit("= ", 1)[1].split()[0]) for t in shown_flows()] == expected
    finally:
        root.destroy()


def test_loop_editor_sets_the_order():
    from rig_control.ui.operation.recipe_step_dialog import StepEditorDialog

    root, window = builder()
    try:
        loop = sweep(LoopOrder.AS_LISTED).steps[0].steps[0]
        result = []
        dialog = StepEditorDialog(root, loop, roles=window._roles, check=lambda _row: (), on_done=result.append,
                                  units=window._units)
        assert dialog._loop_order.get() == "as_listed"
        dialog._loop_order.set("shuffle_once"); dialog._ok()
        assert result[0].order is LoopOrder.SHUFFLE_ONCE and result[0].values == FLOWS
    finally:
        root.destroy()
