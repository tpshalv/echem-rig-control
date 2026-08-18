import json
import os
import shutil
from pathlib import Path

from rig_control.app_settings import AppSettings, AppSettingValue


def write_app_settings(
    settings: AppSettings,
    path: str | Path,
) -> Path | None:
    if not isinstance(settings, AppSettings):
        raise TypeError("settings must be AppSettings")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if destination.exists():
        backup = destination.with_suffix(destination.suffix + ".bak")
        shutil.copy2(destination, backup)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    lines = [
        "[settings]",
        f"settings_id = {json.dumps(settings.settings_id)}",
        f"friendly_name = {json.dumps(settings.friendly_name)}",
        "",
        "[values]",
    ]
    for key, value in settings.values.items():
        lines.append(f"{key} = {_toml_value(value)}")
    try:
        temporary.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup


def _toml_value(value: AppSettingValue) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)
