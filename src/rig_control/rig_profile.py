from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


type ConfigurationValue = str | int | float | bool


class DeviceCapability(StrEnum):
    """General jobs that hardware can perform within the rig."""

    MASS_FLOW_CONTROLLER = "mass_flow_controller"
    MASS_FLOW_METER = "mass_flow_meter"

    DC_POWER_SUPPLY = "dc_power_supply"
    POTENTIOSTAT = "potentiostat"

    TEMPERATURE_SENSOR = "temperature_sensor"
    PRESSURE_SENSOR_ABSOLUTE = "pressure_sensor_absolute"
    PRESSURE_SENSOR_RELATIVE = "pressure_sensor_relative"
    PRESSURE_SENSOR_DIFFERENTIAL = "pressure_sensor_differential"
    HUMIDITY_SENSOR = "humidity_sensor"

    TEMPERATURE_CONTROLLER = "temperature_controller"
    REMOTE_CONTROLLER = "remote_controller"


class DeviceBackend(StrEnum):
    """Whether a configured device is real or simulated."""

    REAL = "real"
    SIMULATED = "simulated"


@dataclass(frozen=True, slots=True)
class ConnectionDefinition:
    """One physical communication route shared by devices."""

    connection_id: str
    connection_type: str
    parameters: Mapping[str, ConfigurationValue] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        _require_non_empty_text(
            self.connection_id,
            "Connection ID",
        )
        _require_non_empty_text(
            self.connection_type,
            "Connection type",
        )

        object.__setattr__(
            self,
            "parameters",
            _validated_mapping(
                self.parameters,
                "Connection parameters",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExpectedDeviceIdentity:
    """Hardware identity expected for one configured device role."""

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None

    def __post_init__(self) -> None:
        self._reject_blank_text("manufacturer", self.manufacturer)
        self._reject_blank_text("model", self.model)
        self._reject_blank_text(
            "serial_number",
            self.serial_number,
        )

    @staticmethod
    def _reject_blank_text(
        field_name: str,
        value: str | None,
    ) -> None:
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(
                f"Expected device {field_name} cannot be blank"
            )


@dataclass(frozen=True, slots=True)
class DeviceRole:
    """One logical equipment role required by a rig profile."""

    device_id: str
    friendly_name: str
    capability: DeviceCapability
    driver: str
    backend: DeviceBackend = DeviceBackend.REAL
    required: bool = True
    enabled: bool = True
    expected_identity: ExpectedDeviceIdentity | None = None
    connection_id: str | None = None
    connection_parameters: Mapping[
        str,
        ConfigurationValue,
    ] = field(default_factory=dict)
    settings: Mapping[str, ConfigurationValue] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        _require_non_empty_text(
            self.device_id,
            "Device role ID",
        )
        _require_non_empty_text(
            self.friendly_name,
            "Device friendly name",
        )
        _require_non_empty_text(
            self.driver,
            "Device driver",
        )

        if not isinstance(self.capability, DeviceCapability):
            raise TypeError(
                "Device capability must be a DeviceCapability"
            )

        if not isinstance(self.backend, DeviceBackend):
            raise TypeError(
                "Device backend must be a DeviceBackend"
            )

        if not isinstance(self.required, bool):
            raise TypeError("Device required setting must be Boolean")

        if not isinstance(self.enabled, bool):
            raise TypeError("Device enabled setting must be Boolean")

        if (
            self.expected_identity is not None
            and not isinstance(
                self.expected_identity,
                ExpectedDeviceIdentity,
            )
        ):
            raise TypeError(
                "Expected identity must be an "
                "ExpectedDeviceIdentity"
            )

        if self.connection_id is not None:
            _require_non_empty_text(
                self.connection_id,
                "Device connection ID",
            )

        object.__setattr__(
            self,
            "connection_parameters",
            _validated_mapping(
                self.connection_parameters,
                "Device connection parameters",
            ),
        )
        object.__setattr__(
            self,
            "settings",
            _validated_mapping(
                self.settings,
                "Device settings",
            ),
        )


@dataclass(frozen=True, slots=True)
class RigProfile:
    """Description of the hardware expected in one rig arrangement."""

    profile_id: str
    friendly_name: str
    device_roles: tuple[DeviceRole, ...]
    connections: tuple[ConnectionDefinition, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_text(
            self.profile_id,
            "Rig profile ID",
        )
        _require_non_empty_text(
            self.friendly_name,
            "Rig profile friendly name",
        )

        if not isinstance(self.device_roles, tuple):
            raise TypeError("Device roles must be supplied as a tuple")

        if not isinstance(self.connections, tuple):
            raise TypeError("Connections must be supplied as a tuple")

        connection_ids: set[str] = set()

        for connection in self.connections:
            if not isinstance(connection, ConnectionDefinition):
                raise TypeError(
                    "Every connection must be a "
                    "ConnectionDefinition"
                )

            if connection.connection_id in connection_ids:
                raise ValueError(
                    "Duplicate connection ID: "
                    f"{connection.connection_id!r}"
                )

            connection_ids.add(connection.connection_id)

        device_ids: set[str] = set()

        for role in self.device_roles:
            if not isinstance(role, DeviceRole):
                raise TypeError(
                    "Every rig profile entry must be a DeviceRole"
                )

            if role.device_id in device_ids:
                raise ValueError(
                    f"Duplicate device role ID: {role.device_id!r}"
                )

            device_ids.add(role.device_id)

            if (
                role.connection_id is not None
                and role.connection_id not in connection_ids
            ):
                raise ValueError(
                    f"Device role {role.device_id!r} refers to "
                    f"unknown connection "
                    f"{role.connection_id!r}"
                )

    @property
    def enabled_roles(self) -> tuple[DeviceRole, ...]:
        """Return all device roles enabled in this profile."""

        return tuple(
            role for role in self.device_roles if role.enabled
        )

    @property
    def required_roles(self) -> tuple[DeviceRole, ...]:
        """Return enabled device roles required for operation."""

        return tuple(
            role
            for role in self.device_roles
            if role.enabled and role.required
        )

    def get_role(self, device_id: str) -> DeviceRole:
        """Find one configured device role by its stable ID."""

        for role in self.device_roles:
            if role.device_id == device_id:
                return role

        available = ", ".join(
            role.device_id for role in self.device_roles
        ) or "none"

        raise KeyError(
            f"Unknown device role {device_id!r}. "
            f"Configured role IDs: {available}"
        )

    def get_connection(
        self,
        connection_id: str,
    ) -> ConnectionDefinition:
        """Find one physical connection by its stable ID."""

        for connection in self.connections:
            if connection.connection_id == connection_id:
                return connection

        available = ", ".join(
            connection.connection_id
            for connection in self.connections
        ) or "none"

        raise KeyError(
            f"Unknown connection {connection_id!r}. "
            f"Configured connection IDs: {available}"
        )


def _require_non_empty_text(
    value: object,
    field_name: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


def _validated_mapping(
    values: Mapping[str, ConfigurationValue],
    field_name: str,
) -> Mapping[str, ConfigurationValue]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a mapping")

    validated: dict[str, ConfigurationValue] = {}

    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(
                f"{field_name} keys must be non-empty text"
            )

        if isinstance(value, bool):
            validated[key] = value
            continue

        if not isinstance(value, (str, int, float)):
            raise TypeError(
                f"{field_name} value {key!r} must be "
                "text, an integer, a number, or Boolean"
            )

        validated[key] = value

    return MappingProxyType(validated)