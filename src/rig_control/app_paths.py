import os
from pathlib import Path

from rig_control.build_stamp import build_directory


def home_directory() -> Path:
    """Return the per-machine folder for user data (settings, rig profiles,
    logs, experiment recordings) - kept separate from the source checkout.

    Resolution order: the ECHEM_RIG_CONTROL_HOME environment variable (an
    explicit override, e.g. for a portable/USB-drive deployment), then
    %LOCALAPPDATA%\\EchemRigControl on Windows, then ~/.echem-rig-control
    as a last resort.
    """

    override = os.environ.get("ECHEM_RIG_CONTROL_HOME")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "EchemRigControl"
    return Path.home() / ".echem-rig-control"


def settings_directory() -> Path:
    return home_directory() / "settings"


def profiles_directory() -> Path:
    return home_directory() / "profiles"


def logs_directory() -> Path:
    return home_directory() / "logs"


def experiments_directory() -> Path:
    return home_directory() / "experiments"


def device_dumps_directory() -> Path:
    """Read-only settings dumps taken from instruments, kept per machine.

    These record what a device was set to on a date. They are deliberately
    separate from application logs, and are not part of the source checkout.
    """

    return home_directory() / "device-dumps"


def calibrations_directory() -> Path:
    return home_directory() / "calibrations"


def default_selection_path() -> Path:
    return home_directory() / "app-selection.toml"


# Rig profiles shipped with the app, next to the source checkout or the .exe.
# They are templates: device setup never writes to them, so a bench's real
# configuration cannot silently overwrite them (or end up in git).
SHIPPED_RIG_PROFILE_NAMES = frozenset({
    "rig-profile.example.toml",
    "rig-profile.esp32.toml",
    "rig-profile.simulation.toml",
})


def is_shipped_rig_profile(path: str | Path) -> bool:
    selected = Path(path).resolve()
    return (
        selected.name in SHIPPED_RIG_PROFILE_NAMES
        and selected.parent == build_directory().resolve()
    )


def editable_profile_copy_path(path: str | Path) -> Path:
    """Return an unused path in the profiles folder for a copy of `path`."""

    name = Path(path)
    candidate = profiles_directory() / name.name
    number = 2
    while candidate.exists():
        candidate = profiles_directory() / f"{name.stem}-{number}{name.suffix}"
        number += 1
    return candidate
