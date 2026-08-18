from pathlib import Path

from rig_control.app_selection import (
    AppSelection,
    load_app_selection,
    write_app_selection,
)


def test_app_selection_round_trips(tmp_path: Path) -> None:
    selection = AppSelection(
        settings_file="app-settings.lab.toml",
        rig_profile_file="rig-profile.lab.toml",
    )
    path = tmp_path / "app-selection.toml"

    write_app_selection(selection, path)

    assert load_app_selection(path) == selection
