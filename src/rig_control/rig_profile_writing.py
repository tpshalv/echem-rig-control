import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from rig_control.rig_profile import ConfigurationValue, RigProfile


def write_rig_profile(profile: RigProfile, path: str | Path) -> Path | None:
    """Atomically write a profile, backing up an existing file first."""

    if not isinstance(profile, RigProfile):
        raise TypeError("profile must be a RigProfile")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None

    if destination.exists():
        backup = destination.with_suffix(destination.suffix + ".bak")
        shutil.copy2(destination, backup)

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(
            serialize_rig_profile(profile),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    return backup


def serialize_rig_profile(profile: RigProfile) -> str:
    """Return deterministic TOML for the supported profile schema."""

    lines = [
        "[profile]",
        f"profile_id = {_toml_value(profile.profile_id)}",
        f"friendly_name = {_toml_value(profile.friendly_name)}",
    ]

    for connection in profile.connections:
        lines.extend(
            [
                "",
                "",
                "[[connections]]",
                f"connection_id = {_toml_value(connection.connection_id)}",
                "connection_type = "
                + _toml_value(connection.connection_type),
            ]
        )
        if connection.parameters:
            lines.extend(["", "[connections.parameters]"])
            _append_mapping(lines, connection.parameters)

    for role in profile.device_roles:
        lines.extend(
            [
                "",
                "",
                "[[devices]]",
                f"device_id = {_toml_value(role.device_id)}",
                f"friendly_name = {_toml_value(role.friendly_name)}",
                f"capability = {_toml_value(role.capability.value)}",
                f"driver = {_toml_value(role.driver)}",
                f"backend = {_toml_value(role.backend.value)}",
                f"required = {_toml_value(role.required)}",
                f"enabled = {_toml_value(role.enabled)}",
            ]
        )
        if role.connection_id is not None:
            lines.append(
                f"connection_id = {_toml_value(role.connection_id)}"
            )
        if role.poll_interval_seconds is not None:
            lines.append(
                "poll_interval_seconds = "
                + _toml_value(role.poll_interval_seconds)
            )
        if role.expected_identity is not None:
            identity = role.expected_identity
            values = {
                "manufacturer": identity.manufacturer,
                "model": identity.model,
                "serial_number": identity.serial_number,
            }
            present = {
                key: value for key, value in values.items() if value is not None
            }
            if present:
                lines.extend(["", "[devices.identity]"])
                _append_mapping(lines, present)
        if role.connection_parameters:
            lines.extend(["", "[devices.connection]"])
            _append_mapping(lines, role.connection_parameters)
        if role.settings:
            lines.extend(["", "[devices.settings]"])
            _append_mapping(lines, role.settings)

    return "\n".join(lines) + "\n"


def _append_mapping(
    lines: list[str],
    values: Mapping[str, ConfigurationValue],
) -> None:
    for key, value in values.items():
        lines.append(f"{key} = {_toml_value(value)}")


def _toml_value(value: ConfigurationValue | None) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    raise TypeError(f"Unsupported rig-profile value: {value!r}")
