"""Stable step IDs: a step keeps its ID through edits, saves and the outline."""
from dataclasses import replace

from rig_control.recipes import LoopStep, Recipe, RepeatStep, SetStep, WaitPurpose, WaitStep, recipe_from_dict, recipe_to_dict
from rig_control.recipes.model import iter_steps
from rig_control.recipes.outline import duplicate, from_outline, to_outline

WAIT = WaitStep(60.0, WaitPurpose.MEASURE)


def ids(recipe: Recipe) -> list[str]:
    return [s.id for s in iter_steps(recipe.steps)]


def nested() -> Recipe:
    loop = LoopStep("supply", "current_setpoint", (1.0, 2.0), steps=(SetStep.one("mfc", "flow", 10.0), WAIT))
    return Recipe("r", (SetStep.one("supply", "voltage_limit", 5.0), RepeatStep(2, (loop,))))


def test_every_step_gets_an_id_that_does_not_change_what_it_does():
    a, b = WaitStep(60.0), WaitStep(60.0)
    assert a.id and b.id and a.id != b.id
    assert a == b  # The ID is identity, not behaviour.


def test_ids_are_unique_within_a_recipe_even_for_a_reused_step_object():
    recipe = Recipe("r", (WAIT, WAIT, RepeatStep(2, (WAIT,))))
    assert len(set(ids(recipe))) == 4
    assert recipe.steps[0].id == WAIT.id  # The first keeps its ID; later copies get new ones.


def test_ids_survive_save_and_load():
    recipe = nested()
    again = recipe_from_dict(recipe_to_dict(recipe))
    assert ids(again) == ids(recipe)
    assert again == recipe


def test_files_without_ids_still_load_and_get_ids():
    data = recipe_to_dict(nested())

    def strip(steps):
        for step in steps:
            step.pop("id")
            strip(step.get("steps", []))
    strip(data["steps"])
    recipe = recipe_from_dict(data)
    assert all(ids(recipe)) and len(set(ids(recipe))) == len(ids(recipe))


def test_edits_and_the_outline_keep_ids():
    recipe = nested()
    assert ids(Recipe("r", from_outline(to_outline(recipe.steps)))) == ids(recipe)
    renamed = replace(recipe.steps[0], name="limit")
    assert renamed.id == recipe.steps[0].id


def test_duplicating_gives_the_copy_new_ids():
    rows = to_outline(nested().steps)
    # Row 1 is the Repeat: copying it copies the loop and its contents too.
    copied, _ = duplicate(rows, 1)
    recipe = Recipe("r", from_outline(copied))
    original, copy = recipe.steps[1], recipe.steps[2]
    assert original == copy
    assert not set(s.id for s in iter_steps((original,))) & set(s.id for s in iter_steps((copy,)))
