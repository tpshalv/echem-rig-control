from pathlib import Path
from typing import Any
import tomllib

from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    ExpectedDeviceIdentity,
    RigProfile,
)


def load_rig_profile(path: str | Path) -> RigProfile:
    """Load and validate one rig hardware profile from TOML."""

    profile_path = Path(path)

    try:
        with profile_path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Rig profile file was not found: {profile_path}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            f"Rig profile contains invalid TOML: "
            f"{profile_path}: {error}"
        ) from error

    profile_data = _require_table(
        data,
        "profile",
        "profile",
    )

    # A newly created rig is valid before its first device is added. TOML has
    # no useful empty-array-of-tables representation, so the writer omits the
    # section and the loader treats that omission as an empty device list.
    devices_data = data.get("devices", [])

    if not isinstance(devices_data, list):
        raise TypeError(
            "Configuration setting devices must be an array of tables"
        )

    connections_data = data.get("connections", [])

    if not isinstance(connections_data, list):
        raise TypeError(
            "Configuration setting connections must be "
            "an array of tables"
        )

    connections = tuple(
        _load_connection(connection_data, index)
        for index, connection_data in enumerate(connections_data)
    )

    roles = tuple(
        _load_device_role(device_data, index)
        for index, device_data in enumerate(devices_data)
    )

    return RigProfile(
        profile_id=_require_text(
            profile_data,
            "profile_id",
            "profile.profile_id",
        ),
        friendly_name=_require_text(
            profile_data,
            "friendly_name",
            "profile.friendly_name",
        ),
        device_roles=roles,
        connections=connections,
    )

def _load_connection(
    data: object,
    index: int,
) -> ConnectionDefinition:
    setting_prefix = f"connections[{index}]"

    if not isinstance(data, dict):
        raise TypeError(
            f"Configuration setting {setting_prefix} "
            "must be a table"
        )

    return ConnectionDefinition(
        connection_id=_require_text(
            data,
            "connection_id",
            f"{setting_prefix}.connection_id",
        ),
        connection_type=_require_text(
            data,
            "connection_type",
            f"{setting_prefix}.connection_type",
        ),
        parameters=_optional_mapping(
            data,
            "parameters",
            f"{setting_prefix}.parameters",
        ),
    )


def _optional_mapping(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> dict[str, str | int | float | bool]:
    if key not in parent:
        return {}

    value = parent[key]

    if not isinstance(value, dict):
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be a table"
        )

    return value


def _load_device_role(
    data: object,
    index: int,
) -> DeviceRole:
    setting_prefix = f"devices[{index}]"

    if not isinstance(data, dict):
        raise TypeError(
            f"Configuration setting {setting_prefix} "
            "must be a table"
        )

    capability_text = _require_text(
        data,
        "capability",
        f"{setting_prefix}.capability",
    )
    backend_text = _optional_text(
        data,
        "backend",
        DeviceBackend.REAL.value,
        f"{setting_prefix}.backend",
    )

    try:
        capability = DeviceCapability(capability_text)
    except ValueError as error:
        available = ", ".join(
            capability.value for capability in DeviceCapability
        )
        raise ValueError(
            f"Unknown device capability {capability_text!r} in "
            f"{setting_prefix}.capability. "
            f"Available capabilities: {available}"
        ) from error

    try:
        backend = DeviceBackend(backend_text)
    except ValueError as error:
        available = ", ".join(
            backend.value for backend in DeviceBackend
        )
        raise ValueError(
            f"Unknown device backend {backend_text!r} in "
            f"{setting_prefix}.backend. "
            f"Available backends: {available}"
        ) from error

    identity = _load_expected_identity(
        data.get("identity"),
        setting_prefix,
    )

    return DeviceRole(
        device_id=_require_text(
            data,
            "device_id",
            f"{setting_prefix}.device_id",
        ),
        friendly_name=_require_text(
            data,
            "friendly_name",
            f"{setting_prefix}.friendly_name",
        ),
        capability=capability,
        driver=_require_text(
            data,
            "driver",
            f"{setting_prefix}.driver",
        ),
        backend=backend,
        required=_optional_boolean(
            data,
            "required",
            True,
            f"{setting_prefix}.required",
        ),
        enabled=_optional_boolean(
            data,
            "enabled",
            True,
            f"{setting_prefix}.enabled",
        ),
        poll_interval_seconds=_optional_number(
            data,
            "poll_interval_seconds",
            None,
            f"{setting_prefix}.poll_interval_seconds",
        ),
        expected_identity=identity,
        connection_id=_optional_nullable_text(
            data,
            "connection_id",
            f"{setting_prefix}.connection_id",
        ),
        connection_parameters=_optional_mapping(
            data,
            "connection",
            f"{setting_prefix}.connection",
        ),
        settings=_optional_mapping(
            data,
            "settings",
            f"{setting_prefix}.settings",
        ),
        system=_optional_nullable_text(
            data,
            "system",
            f"{setting_prefix}.system",
        ),
        channel_labels=_optional_mapping(
            data, "channel_labels", f"{setting_prefix}.channel_labels",
        ),
    )

def _load_expected_identity(
    data: object,
    setting_prefix: str,
) -> ExpectedDeviceIdentity | None:
    if data is None:
        return None

    if not isinstance(data, dict):
        raise TypeError(
            f"Configuration setting {setting_prefix}.identity "
            "must be a table"
        )

    return ExpectedDeviceIdentity(
        manufacturer=_optional_nullable_text(
            data,
            "manufacturer",
            f"{setting_prefix}.identity.manufacturer",
        ),
        model=_optional_nullable_text(
            data,
            "model",
            f"{setting_prefix}.identity.model",
        ),
        serial_number=_optional_nullable_text(
            data,
            "serial_number",
            f"{setting_prefix}.identity.serial_number",
        ),
    )


def _require_table(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> dict[str, Any]:
    if key not in parent:
        raise ValueError(
            f"Missing required configuration section: {setting_name}"
        )

    value = parent[key]

    if not isinstance(value, dict):
        raise TypeError(
            f"Configuration setting {setting_name} must be a table"
        )

    return value


def _require_text(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> str:
    if key not in parent:
        raise ValueError(
            f"Missing required configuration setting: {setting_name}"
        )

    value = parent[key]

    if not isinstance(value, str):
        raise TypeError(
            f"Configuration setting {setting_name} must be text"
        )

    if not value.strip():
        raise ValueError(
            f"Configuration setting {setting_name} cannot be empty"
        )

    return value


def _optional_text(
    parent: dict[str, Any],
    key: str,
    default: str,
    setting_name: str,
) -> str:
    if key not in parent:
        return default

    return _require_text(parent, key, setting_name)


def _optional_nullable_text(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> str | None:
    if key not in parent:
        return None

    return _require_text(parent, key, setting_name)


def _optional_boolean(
    parent: dict[str, Any],
    key: str,
    default: bool,
    setting_name: str,
) -> bool:
    if key not in parent:
        return default

    value = parent[key]

    if not isinstance(value, bool):
        raise TypeError(
            f"Configuration setting {setting_name} must be Boolean"
        )

    return value


def _optional_number(
    parent: dict[str, Any],
    key: str,
    default: float | None,
    setting_name: str,
) -> float | None:
    if key not in parent:
        return default

    value = parent[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Configuration setting {setting_name} must be numeric"
        )
    return float(value)
