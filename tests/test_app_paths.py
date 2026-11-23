from pathlib import Path

from rig_control.app_paths import (
    default_selection_path,
    experiments_directory,
    home_directory,
    logs_directory,
    profiles_directory,
    resolve_output_directory,
    settings_directory,
)


def test_home_directory_honours_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("ECHEM_RIG_CONTROL_HOME", r"D:\portable-echem")

    assert home_directory() == Path(r"D:\portable-echem")


def test_home_directory_falls_back_to_local_app_data(monkeypatch) -> None:
    monkeypatch.delenv("ECHEM_RIG_CONTROL_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    assert home_directory() == Path(
        r"C:\Users\someone\AppData\Local\EchemRigControl"
    )


def test_home_directory_falls_back_to_user_home(monkeypatch) -> None:
    monkeypatch.delenv("ECHEM_RIG_CONTROL_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert home_directory() == Path.home() / ".echem-rig-control"


def test_subdirectories_and_selection_path_sit_under_home(monkeypatch) -> None:
    monkeypatch.setenv("ECHEM_RIG_CONTROL_HOME", r"D:\portable-echem")

    home = home_directory()
    assert settings_directory() == home / "settings"
    assert profiles_directory() == home / "profiles"
    assert logs_directory() == home / "logs"
    assert experiments_directory() == home / "experiments"
    assert default_selection_path() == home / "app-selection.toml"


def test_recordings_default_to_documents_rig_data(monkeypatch) -> None:
    monkeypatch.delenv("ECHEM_RIG_CONTROL_HOME", raising=False)

    assert experiments_directory() == Path.home() / "Documents" / "Rig Data"


def test_output_folder_never_depends_on_where_the_app_started(
    monkeypatch, tmp_path
) -> None:
    """A blank box or a bare name once saved runs into whatever folder the
    app was launched from, so they were hard to find afterwards."""

    monkeypatch.delenv("ECHEM_RIG_CONTROL_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    default = experiments_directory()

    assert resolve_output_directory("") == default
    assert resolve_output_directory("   ") == default
    assert resolve_output_directory("experiments") == default / "experiments"
    assert resolve_output_directory(r"D:\Data\Runs") == Path(r"D:\Data\Runs")
    assert resolve_output_directory(str(tmp_path)) == tmp_path


def test_build_stamp_describes_a_build_and_never_fails_without_one(tmp_path, monkeypatch):
    """Two builds of one commit on one day share a folder name, so the app
    shows which one it is. A missing file must not stop it starting."""

    import json

    from rig_control import build_stamp as module

    monkeypatch.setattr(module, "build_directory", lambda: tmp_path)
    assert module.build_stamp() == ""

    (tmp_path / "build-info.json").write_text(
        json.dumps({
            "version": "0.1.0",
            "built_at": "2026-09-28T09:15:00",
            "git_commit": "0351c81",
            "uncommitted_changes": True,
        }),
        encoding="utf-8",
    )
    assert module.build_stamp() == "0.1.0 2026-09-28 09:15:00 (0351c81-dirty)"

    (tmp_path / "build-info.json").write_text("not json", encoding="utf-8")
    assert module.build_stamp() == ""
