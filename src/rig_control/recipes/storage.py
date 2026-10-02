"""Portable JSON storage for recipes."""
import json
from pathlib import Path
from rig_control.recipes.model import Recipe, recipe_from_dict, recipe_to_dict


def save_recipe(recipe: Recipe, path: str | Path) -> None:
    Path(path).write_text(json.dumps(recipe_to_dict(recipe), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_recipe(path: str | Path) -> Recipe:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Recipe file is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("Recipe file root must be an object")
    return recipe_from_dict(raw)
