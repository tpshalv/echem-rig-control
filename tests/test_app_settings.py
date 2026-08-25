from pathlib import Path

import pytest

from rig_control.app_paths import experiments_directory, logs_directory
from rig_control.app_settings import (
    AppSettings,
    default_app_settings,
    parse_setting_text,
)
from rig_control.app_settings_loading import load_app_settings
from rig_control.app_settings_writing import write_app_settings


def test_defaults_are_safe_and_explicit() -> None:
    settings = default_app_settings()

    assert settings.publish_interval_seconds == 1.0
    # Both default paths sit under the per-machine data home, not next to
    # the source checkout (see app_paths.py).
    assert settings.technical_log_path == str(logs_directory() / "rig-control.log")
    assert settings.trend_history_readings == 500
    assert settings.default_output_directory == str(experiments_directory())
    assert settings.power_supply_default_current_amps == 20.0
    assert settings.power_supply_default_voltage_volts == 10.0
    assert settings.power_supply_high_current_mode is False
    assert settings.power_supply_wiring_current_ceiling_amps == 45.0


def test_boolean_setting_text_is_parsed_as_boolean_not_int() -> None:
    # Regression: bool is a subclass of int in Python, so a naive
    # isinstance(default, int) check placed before the bool check would
    # make every boolean setting unreachable and fail with a confusing
    # "must be an integer" error.
    assert parse_setting_text("power_supply_high_current_mode", "true") is True
    assert parse_setting_text("power_supply_high_current_mode", "False") is False

    with pytest.raises(ValueError, match="must be true or false"):
        parse_setting_text("power_supply_high_current_mode", "1")


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
            "default_output_directory": "recordings",
        },
    )
    path = tmp_path / "app-settings.toml"

    assert write_app_settings(settings, path) is None
    backup = write_app_settings(settings, path)

    assert load_app_settings(path) == settings
    assert backup == tmp_path / "app-settings.toml.bak"
