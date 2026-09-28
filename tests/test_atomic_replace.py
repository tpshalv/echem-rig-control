"""A held destination file must not lose a save.

On Windows the rename that completes every atomic write intermittently
raises PermissionError while antivirus or the indexer holds the existing
file. These tests simulate that, since it cannot be provoked reliably.
"""

from pathlib import Path

import pytest

from rig_control.atomic_replace import replace_atomically


class HeldPath(type(Path())):
    """A path whose rename is refused a fixed number of times first."""

    refusals = 0
    attempts = 0

    def replace(self, target):
        type(self).attempts += 1
        if type(self).attempts <= type(self).refusals:
            raise PermissionError(13, "Access is denied")
        return super().replace(target)


def held_path(path: Path, refusals: int) -> HeldPath:
    held = HeldPath(path)
    type(held).refusals = refusals
    type(held).attempts = 0
    return held


def test_a_briefly_held_file_is_replaced_once_it_is_released(tmp_path) -> None:
    destination = tmp_path / "rig-profile.toml"
    destination.write_text("old", encoding="utf-8")
    temporary = tmp_path / "rig-profile.toml.tmp"
    temporary.write_text("new", encoding="utf-8")

    replace_atomically(held_path(temporary, 3), destination, delay_seconds=0)

    assert destination.read_text(encoding="utf-8") == "new"
    assert HeldPath.attempts == 4


def test_a_file_held_throughout_still_reports_the_failure(tmp_path) -> None:
    destination = tmp_path / "rig-profile.toml"
    destination.write_text("old", encoding="utf-8")
    temporary = tmp_path / "rig-profile.toml.tmp"
    temporary.write_text("new", encoding="utf-8")

    # A lock that never clears is a real failure, not one to hide: the
    # caller must be able to tell the operator the save did not happen.
    with pytest.raises(PermissionError):
        replace_atomically(
            held_path(temporary, 99), destination, attempts=4, delay_seconds=0
        )

    assert destination.read_text(encoding="utf-8") == "old"
    assert HeldPath.attempts == 4


def test_a_free_file_is_replaced_without_waiting(tmp_path) -> None:
    destination = tmp_path / "settings.toml"
    temporary = tmp_path / "settings.toml.tmp"
    temporary.write_text("new", encoding="utf-8")

    replace_atomically(held_path(temporary, 0), destination)

    assert destination.read_text(encoding="utf-8") == "new"
    assert HeldPath.attempts == 1
    assert not temporary.exists()


def test_at_least_one_attempt_is_required(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least one attempt"):
        replace_atomically(tmp_path / "a", tmp_path / "b", attempts=0)


def test_saving_a_profile_survives_a_briefly_held_file(tmp_path, monkeypatch) -> None:
    """The retry is reached through a real save, not only in isolation."""

    from dataclasses import replace

    import rig_control.atomic_replace as module
    from rig_control.rig_profile_loading import load_rig_profile
    from rig_control.rig_profile_writing import write_rig_profile

    profile = load_rig_profile("rig-profile.example.toml")
    destination = tmp_path / "rig-profile.toml"
    write_rig_profile(profile, destination)

    refusals = {"remaining": 2}
    original = module.Path.replace

    def flaky(self, target):
        if refusals["remaining"]:
            refusals["remaining"] -= 1
            raise PermissionError(13, "Access is denied")
        return original(self, target)

    monkeypatch.setattr(module.Path, "replace", flaky)
    renamed = replace(profile, friendly_name="Renamed rig")
    write_rig_profile(renamed, destination)

    assert refusals["remaining"] == 0
    assert load_rig_profile(destination).friendly_name == "Renamed rig"
