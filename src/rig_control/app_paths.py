import os
from pathlib import Path


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


def calibrations_directory() -> Path:
    return home_directory() / "calibrations"


def default_selection_path() -> Path:
    return home_directory() / "app-selection.toml"
