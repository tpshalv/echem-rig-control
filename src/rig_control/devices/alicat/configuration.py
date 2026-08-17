from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

from rig_control.devices.mass_flow_controller import (
    MassFlowControllerLimits,
)
from rig_control.devices.alicat.protocol import (
    AlicatEngineeringUnits,
    AlicatFrameField,
)
from rig_control.rig_profile import (
    ConfigurationValue,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)


@dataclass(frozen=True, slots=True)
class AlicatSerialConfiguration:
    """Validated settings for one PC-connected BB3 serial bus."""

    connection_id: str
    port: str
    baud_rate: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        _validate_text(self.connection_id, "Alicat connection ID")
        _validate_text(self.port, "Alicat COM port")

        if (
            not isinstance(self.baud_rate, int)
            or isinstance(self.baud_rate, bool)
        ):
            raise TypeError("Alicat baud rate must be an integer")

        if self.baud_rate <= 0:
            raise ValueError("Alicat baud rate must be greater than zero")

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
        ):
            raise TypeError("Alicat timeout must be an int or float")

        if not isfinite(float(self.timeout_seconds)):
            raise ValueError("Alicat timeout must be finite")

        if self.timeout_seconds <= 0:
            raise ValueError("Alicat timeout must be greater than zero")


@dataclass(frozen=True, slots=True)
class AlicatMfcConfiguration:
    """Validated settings needed to construct one addressed Alicat MFC."""

    device_id: str
    friendly_name: str
    unit_address: str
    connection: AlicatSerialConfiguration
    limits: MassFlowControllerLimits
    frame_fields: tuple[AlicatFrameField, ...]
    engineering_units: AlicatEngineeringUnits

    def __post_init__(self) -> None:
        _validate_text(self.device_id, "Alicat device ID")
        _validate_text(self.friendly_name, "Alicat friendly name")

        normalized_address = _normalize_unit_address(
            self.unit_address,
            "Alicat unit address",
        )
        object.__setattr__(
            self,
            "unit_address",
            normalized_address,
        )

        if not isinstance(self.connection, AlicatSerialConfiguration):
            raise TypeError(
                "Alicat connection must be an "
                "AlicatSerialConfiguration"
            )

        if not isinstance(self.limits, MassFlowControllerLimits):
            raise TypeError("Alicat limits must be MassFlowControllerLimits")

        if not isinstance(self.frame_fields, tuple) or any(
            not isinstance(field, AlicatFrameField)
            for field in self.frame_fields
        ):
            raise TypeError(
                "Alicat frame fields must be AlicatFrameField values"
            )
        if not isinstance(self.engineering_units, AlicatEngineeringUnits):
            raise TypeError(
                "Alicat engineering units must be AlicatEngineeringUnits"
            )


def configuration_from_profile(
    profile: RigProfile,
    device_id: str,
) -> AlicatMfcConfiguration:
    """Create validated BB3 and MFC settings for one profile role."""

    role = profile.get_role(device_id)
    _validate_alicat_role(role)

    if role.connection_id is None:
        raise ValueError(
            f"Alicat device role {device_id!r} has no connection"
        )

    connection = profile.get_connection(role.connection_id)

    if connection.connection_type != "serial_text":
        raise ValueError(
            f"Alicat connection {connection.connection_id!r} "
            "must use connection type 'serial_text'"
        )

    unit_address = _normalize_unit_address(
        _require_text(
            role.connection_parameters,
            "address",
            f"devices.{role.device_id}.connection.address",
        ),
        f"devices.{role.device_id}.connection.address",
    )
    _reject_duplicate_unit_address(
        profile,
        role,
        unit_address,
    )

    flow_unit = _require_text(
        role.settings,
        "flow_unit",
        f"devices.{role.device_id}.settings.flow_unit",
    )

    return AlicatMfcConfiguration(
        device_id=role.device_id,
        friendly_name=role.friendly_name,
        unit_address=unit_address,
        connection=AlicatSerialConfiguration(
            connection_id=connection.connection_id,
            port=_require_text(
                connection.parameters,
                "port",
                f"connections.{connection.connection_id}.parameters.port",
            ),
            baud_rate=_require_integer(
                connection.parameters,
                "baud_rate",
                f"connections.{connection.connection_id}.parameters."
                "baud_rate",
            ),
            timeout_seconds=_require_number(
                connection.parameters,
                "timeout_seconds",
                f"connections.{connection.connection_id}.parameters."
                "timeout_seconds",
            ),
        ),
        limits=MassFlowControllerLimits(
            maximum_flow=_require_number(
                role.settings,
                "maximum_flow",
                f"devices.{role.device_id}.settings.maximum_flow",
            ),
            flow_unit=flow_unit,
        ),
        frame_fields=_parse_frame_fields(
            _require_text(
                role.settings,
                "frame_fields",
                f"devices.{role.device_id}.settings.frame_fields",
            ),
            role.device_id,
        ),
        engineering_units=AlicatEngineeringUnits(
            mass_flow=flow_unit,
            volumetric_flow=_require_text(
                role.settings,
                "volumetric_flow_unit",
                f"devices.{role.device_id}.settings.volumetric_flow_unit",
            ),
            absolute_pressure=_require_text(
                role.settings,
                "pressure_unit",
                f"devices.{role.device_id}.settings.pressure_unit",
            ),
            gas_temperature=_require_text(
                role.settings,
                "temperature_unit",
                f"devices.{role.device_id}.settings.temperature_unit",
            ),
            setpoint=flow_unit,
            totalized_flow=_optional_text(
                role.settings,
                "totalized_flow_unit",
                f"devices.{role.device_id}.settings.totalized_flow_unit",
            ),
        ),
    )


def _parse_frame_fields(
    value: str,
    device_id: str,
) -> tuple[AlicatFrameField, ...]:
    names = tuple(item.strip() for item in value.split(","))
    if not names or any(not name for name in names):
        raise ValueError(
            f"Alicat device {device_id!r} frame_fields must be a "
            "comma-separated list"
        )
    try:
        return tuple(AlicatFrameField(name) for name in names)
    except ValueError as error:
        allowed = ", ".join(field.value for field in AlicatFrameField)
        raise ValueError(
            f"Alicat device {device_id!r} has an unknown frame field. "
            f"Allowed fields: {allowed}"
        ) from error


def _validate_alicat_role(role: DeviceRole) -> None:
    if not role.enabled:
        raise ValueError(
            f"Alicat device role {role.device_id!r} is disabled"
        )

    if role.backend is not DeviceBackend.REAL:
        raise ValueError(
            f"Alicat device role {role.device_id!r} is simulated"
        )

    if role.capability is not DeviceCapability.MASS_FLOW_CONTROLLER:
        raise ValueError(
            f"Device role {role.device_id!r} is not configured as a "
            "mass flow controller"
        )

    if role.driver != "alicat":
        raise ValueError(
            f"Device role {role.device_id!r} does not use the "
            "alicat driver"
        )


def _reject_duplicate_unit_address(
    profile: RigProfile,
    selected_role: DeviceRole,
    selected_address: str,
) -> None:
    for role in profile.enabled_roles:
        if role.device_id == selected_role.device_id:
            continue

        if (
            role.backend is not DeviceBackend.REAL
            or role.driver != "alicat"
            or role.connection_id != selected_role.connection_id
        ):
            continue

        address = role.connection_parameters.get("address")

        if (
            isinstance(address, str)
            and address.strip().upper() == selected_address
        ):
            raise ValueError(
                f"Alicat devices {selected_role.device_id!r} and "
                f"{role.device_id!r} both use address "
                f"{selected_address!r} on connection "
                f"{selected_role.connection_id!r}"
            )


def _normalize_unit_address(value: object, setting_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"Configuration setting {setting_name} must be text"
        )

    normalized = value.strip().upper()

    if len(normalized) != 1 or not "A" <= normalized <= "Z":
        raise ValueError(
            f"Configuration setting {setting_name} must be one "
            "letter from A to Z"
        )

    return normalized


def _validate_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


def _require_text(
    values: Mapping[str, ConfigurationValue],
    key: str,
    setting_name: str,
) -> str:
    if key not in values:
        raise ValueError(
            f"Missing required configuration setting: {setting_name}"
        )

    value = values[key]

    if not isinstance(value, str):
        raise TypeError(
            f"Configuration setting {setting_name} must be text"
        )

    if not value.strip():
        raise ValueError(
            f"Configuration setting {setting_name} cannot be empty"
        )

    return value


def _require_integer(
    values: Mapping[str, ConfigurationValue],
    key: str,
    setting_name: str,
) -> int:
    if key not in values:
        raise ValueError(
            f"Missing required configuration setting: {setting_name}"
        )

    value = values[key]

    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(
            f"Configuration setting {setting_name} must be an integer"
        )

    return value


def _optional_text(
    values: Mapping[str, ConfigurationValue],
    key: str,
    setting_name: str,
) -> str | None:
    if key not in values:
        return None
    return _require_text(values, key, setting_name)


def _require_number(
    values: Mapping[str, ConfigurationValue],
    key: str,
    setting_name: str,
) -> float:
    if key not in values:
        raise ValueError(
            f"Missing required configuration setting: {setting_name}"
        )

    value = values[key]

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Configuration setting {setting_name} must be a number"
        )

    return float(value)
