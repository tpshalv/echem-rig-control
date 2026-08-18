import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppSelection:
    settings_file: str
    rig_profile_file: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.settings_file, "Settings file"),
            (self.rig_profile_file, "Rig profile file"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")


def load_app_selection(path: str | Path) -> AppSelection:
    source = Path(path)
    with source.open("rb") as file:
        data = tomllib.load(file)
    selection = data.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("App selection requires a [selection] table")
    return AppSelection(
        settings_file=_required_text(selection, "settings_file"),
        rig_profile_file=_required_text(selection, "rig_profile_file"),
    )


def write_app_selection(selection: AppSelection, path: str | Path) -> None:
    if not isinstance(selection, AppSelection):
        raise TypeError("selection must be AppSelection")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    text = (
        "[selection]\n"
        f"settings_file = {json.dumps(selection.settings_file)}\n"
        f"rig_profile_file = {json.dumps(selection.rig_profile_file)}\n"
    )
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _required_text(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"App selection {key!r} cannot be empty")
    return value
