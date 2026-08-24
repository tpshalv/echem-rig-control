from pathlib import Path

import pytest

from rig_control.app_settings import AppSettings, default_app_settings
from rig_control.app_settings_loading import load_app_settings
from rig_control.app_settings_writing import write_app_settings


def test_defaults_are_safe_and_explicit() -> None:
    settings = default_app_settings()

    assert settings.publish_interval_seconds == 1.0
    assert settings.technical_log_path == "logs/rig-control.log"
    assert settings.trend_history_readings == 120


def test_default_settings_file_loads() -> None:
    settings = load_app_settings("app-settings.default.toml")

    assert settings.settings_id == "default"
    assert settings.publish_interval_seconds == 1.0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_publish_interval_is_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="publish_interval_seconds"):
        AppSettings("bad", "Bad", {"publish_interval_seconds": value})


def test_unknown_setting_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown application"):
        AppSettings("bad", "Bad", {"typo_setting": True})


def test_settings_round_trip_and_backup(tmp_path: Path) -> None:
    settings = AppSettings(
        "fast",
        "Fast display",
        {
            "publish_interval_seconds": 0.2,
            "technical_log_path": "other/app.log",
        },
    )
    path = tmp_path / "app-settings.toml"

    assert write_app_settings(settings, path) is None
    backup = write_app_settings(settings, path)

    assert load_app_settings(path) == settings
    assert backup == tmp_path / "app-settings.toml.bak"
