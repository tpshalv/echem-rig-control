import json
import os
import shutil
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from rig_control.rig_profile import RigProfile


@dataclass(frozen=True, slots=True)
class ExperimentDevice:
    """One hardware-library device selected for an experiment."""

    device_id: str
    purpose_label: str = ""
    required: bool = True
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Experiment device ID cannot be empty")
        if not isinstance(self.purpose_label, str):
            raise TypeError("Experiment purpose label must be text")
        if not isinstance(self.required, bool):
            raise TypeError("Experiment required setting must be Boolean")
        if not isinstance(self.enabled, bool):
            raise TypeError("Experiment enabled setting must be Boolean")


@dataclass(frozen=True, slots=True)
class ExperimentProfile:
    """An experiment-specific selection of physical library devices."""

    profile_id: str
    friendly_name: str
    devices: tuple[ExperimentDevice, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("Experiment profile ID cannot be empty")
        if not isinstance(self.friendly_name, str) or not self.friendly_name.strip():
            raise ValueError("Experiment profile name cannot be empty")
        if not isinstance(self.devices, tuple):
            raise TypeError("Experiment devices must be supplied as a tuple")
        seen: set[str] = set()
        for device in self.devices:
            if not isinstance(device, ExperimentDevice):
                raise TypeError(
                    "Every experiment device must be an ExperimentDevice"
                )
            if device.device_id in seen:
                raise ValueError(
                    f"Duplicate experiment device ID: {device.device_id!r}"
                )
            seen.add(device.device_id)


def resolve_experiment_profile(
    hardware_library: RigProfile,
    experiment: ExperimentProfile,
) -> RigProfile:
    """Combine library truth with experiment-specific selection and labels."""

    if not isinstance(hardware_library, RigProfile):
        raise TypeError("hardware_library must be a RigProfile")
    if not isinstance(experiment, ExperimentProfile):
        raise TypeError("experiment must be an ExperimentProfile")

    roles = []
    connection_ids: set[str] = set()
    for selection in experiment.devices:
        library_role = hardware_library.get_role(selection.device_id)
        settings = dict(library_role.settings)
        settings.setdefault("hardware_label", library_role.friendly_name)
        if selection.purpose_label.strip():
            settings["purpose_label"] = selection.purpose_label.strip()
        else:
            settings.pop("purpose_label", None)
        roles.append(
            replace(
                library_role,
                friendly_name=(
                    selection.purpose_label.strip()
                    or library_role.friendly_name
                ),
                required=selection.required,
                enabled=selection.enabled,
                settings=settings,
            )
        )
        if library_role.connection_id is not None:
            connection_ids.add(library_role.connection_id)

    connections = tuple(
        connection
        for connection in hardware_library.connections
        if connection.connection_id in connection_ids
    )
    return RigProfile(
        profile_id=experiment.profile_id,
        friendly_name=experiment.friendly_name,
        device_roles=tuple(roles),
        connections=connections,
    )


def load_experiment_profile(path: str | Path) -> ExperimentProfile:
    source = Path(path)
    try:
        with source.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Experiment profile was not found: {source}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            f"Experiment profile contains invalid TOML: {source}: {error}"
        ) from error

    header = data.get("experiment")
    if not isinstance(header, dict):
        raise ValueError("Experiment profile requires an [experiment] table")
    device_values = data.get("devices", [])
    if not isinstance(device_values, list):
        raise ValueError("Experiment devices must be an array of tables")
    return ExperimentProfile(
        profile_id=_required_text(header, "profile_id"),
        friendly_name=_required_text(header, "friendly_name"),
        devices=tuple(
            ExperimentDevice(
                device_id=_required_text(item, "device_id"),
                purpose_label=_optional_text(item, "purpose_label"),
                required=_optional_bool(item, "required", True),
                enabled=_optional_bool(item, "enabled", True),
            )
            for item in device_values
            if isinstance(item, dict)
        ),
    )


def write_experiment_profile(
    profile: ExperimentProfile,
    path: str | Path,
) -> Path | None:
    if not isinstance(profile, ExperimentProfile):
        raise TypeError("profile must be an ExperimentProfile")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if destination.exists():
        backup = destination.with_suffix(destination.suffix + ".bak")
        shutil.copy2(destination, backup)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    lines = [
        "[experiment]",
        f"profile_id = {json.dumps(profile.profile_id)}",
        f"friendly_name = {json.dumps(profile.friendly_name)}",
    ]
    for device in profile.devices:
        lines.extend(
            [
                "",
                "[[devices]]",
                f"device_id = {json.dumps(device.device_id)}",
                f"purpose_label = {json.dumps(device.purpose_label)}",
                f"required = {'true' if device.required else 'false'}",
                f"enabled = {'true' if device.enabled else 'false'}",
            ]
        )
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


def _required_text(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Experiment setting {key!r} must be non-empty text")
    return value


def _optional_text(values: dict[str, object], key: str) -> str:
    value = values.get(key, "")
    if not isinstance(value, str):
        raise TypeError(f"Experiment setting {key!r} must be text")
    return value


def _optional_bool(
    values: dict[str, object],
    key: str,
    default: bool,
) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"Experiment setting {key!r} must be Boolean")
    return value
