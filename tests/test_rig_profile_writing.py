from pathlib import Path

from rig_control.rig_profile_loading import load_rig_profile
from rig_control.rig_profile_writing import write_rig_profile


def test_written_example_profile_round_trips(tmp_path: Path) -> None:
    original = load_rig_profile("rig-profile.example.toml")
    destination = tmp_path / "rig-profile.toml"

    backup = write_rig_profile(original, destination)
    loaded = load_rig_profile(destination)

    assert backup is None
    assert loaded == original


def test_existing_profile_is_backed_up_before_replacement(
    tmp_path: Path,
) -> None:
    profile = load_rig_profile("rig-profile.simulation.toml")
    destination = tmp_path / "rig-profile.toml"
    destination.write_text("original contents", encoding="utf-8")

    backup = write_rig_profile(profile, destination)

    assert backup == tmp_path / "rig-profile.toml.bak"
    assert backup.read_text(encoding="utf-8") == "original contents"
    assert load_rig_profile(destination) == profile
    assert not (tmp_path / "rig-profile.toml.tmp").exists()
