from pathlib import Path
import tomllib

from rig_control.app_settings import AppSettings


def load_app_settings(path: str | Path) -> AppSettings:
    source = Path(path)
    try:
        with source.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Application settings file was not found: {source}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            f"Application settings contain invalid TOML: {source}: {error}"
        ) from error

    header = data.get("settings")
    values = data.get("values", {})
    if not isinstance(header, dict):
        raise ValueError("Application settings require a [settings] table")
    if not isinstance(values, dict):
        raise TypeError("Application settings [values] must be a table")
    return AppSettings(
        settings_id=_required_text(header, "settings_id"),
        friendly_name=_required_text(header, "friendly_name"),
        values=values,
    )


def _required_text(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Application settings {key!r} cannot be empty")
    return value
