from collections.abc import Mapping
from dataclasses import dataclass
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.rig_profile import (
    ConfigurationValue,
    DeviceBackend,
    DeviceCapability,
    RigProfile,
)

@dataclass(frozen=True, slots=True)
class SocketScpiConfiguration:
    """Settings for communicating with an instrument over Ethernet."""

    host: str
    port: int
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError(
                "SCPI host name or IP address cannot be empty"
            )

        if not isinstance(self.port, int) or isinstance(
            self.port,
            bool,
        ):
            raise TypeError("SCPI port must be an integer")

        if not 1 <= self.port <= 65535:
            raise ValueError(
                "SCPI port must be between 1 and 65535"
            )

        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds,
            (int, float),
        ):
            raise TypeError(
                "SCPI timeout must be an int or float"
            )

        if self.timeout_seconds <= 0:
            raise ValueError(
                "SCPI timeout must be greater than zero"
            )


@dataclass(frozen=True, slots=True)
class VisaScpiConfiguration:
    """Settings for a VISA message-based SCPI instrument."""

    resource_name: str
    timeout_seconds: float = 5.0
    baud_rate: int = 9600

    def __post_init__(self) -> None:
        if not isinstance(self.resource_name, str) or not self.resource_name.strip():
            raise ValueError("VISA resource name cannot be empty")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise TypeError("VISA timeout must be an int or float")
        if self.timeout_seconds <= 0:
            raise ValueError("VISA timeout must be greater than zero")
        if not isinstance(self.baud_rate, int) or isinstance(self.baud_rate, bool):
            raise TypeError("VISA baud rate must be an integer")
        if self.baud_rate <= 0:
            raise ValueError("VISA baud rate must be greater than zero")


@dataclass(frozen=True, slots=True)
class Keithley2260BConfiguration:
    """Validated settings needed to construct one Keithley 2260B."""

    device_id: str
    connection: SocketScpiConfiguration | VisaScpiConfiguration
    limits: PowerSupplyLimits

    def __post_init__(self) -> None:
        if (
            not isinstance(self.device_id, str)
            or not self.device_id.strip()
        ):
            raise ValueError("Keithley device ID cannot be empty")

        if not isinstance(
            self.connection,
            (SocketScpiConfiguration, VisaScpiConfiguration),
        ):
            raise TypeError(
                "Keithley connection must be a "
                "SocketScpiConfiguration or VisaScpiConfiguration"
            )

        if not isinstance(self.limits, PowerSupplyLimits):
            raise TypeError(
                "Keithley limits must be PowerSupplyLimits"
            )

def configuration_from_profile(
    profile: RigProfile,
    device_id: str,
) -> Keithley2260BConfiguration:
    """Create validated Keithley settings from one profile role."""

    role = profile.get_role(device_id)

    if not role.enabled:
        raise ValueError(
            f"Keithley device role {device_id!r} is disabled"
        )

    if role.backend is not DeviceBackend.REAL:
        raise ValueError(
            f"Keithley device role {device_id!r} is simulated"
        )

    if role.capability is not DeviceCapability.DC_POWER_SUPPLY:
        raise ValueError(
            f"Device role {device_id!r} is not configured as a "
            "DC power supply"
        )

    if role.driver != "keithley_2260b":
        raise ValueError(
            f"Device role {device_id!r} does not use the "
            "keithley_2260b driver"
        )

    if role.connection_id is None:
        raise ValueError(
            f"Keithley device role {device_id!r} has no connection"
        )

    connection = profile.get_connection(role.connection_id)

    if connection.connection_type not in {"socket_scpi", "visa_scpi"}:
        raise ValueError(
            f"Keithley connection {connection.connection_id!r} "
            "must use connection type 'socket_scpi' or 'visa_scpi'"
        )

    return Keithley2260BConfiguration(
        device_id=role.device_id,
        connection=(VisaScpiConfiguration(
            resource_name=_require_text(
                connection.parameters,
                "resource_name",
                f"connections.{connection.connection_id}.parameters.resource_name",
            ),
            timeout_seconds=_optional_number(
                connection.parameters, "timeout_seconds", 5.0,
                f"connections.{connection.connection_id}.parameters.timeout_seconds",
            ),
            baud_rate=_require_integer(
                connection.parameters,
                "baud_rate",
                f"connections.{connection.connection_id}.parameters.baud_rate",
            ),
        ) if connection.connection_type == "visa_scpi" else SocketScpiConfiguration(
            host=_require_text(
                connection.parameters,
                "host",
                f"connections."
                f"{connection.connection_id}.parameters.host",
            ),
            port=_require_integer(
                connection.parameters,
                "port",
                f"connections."
                f"{connection.connection_id}.parameters.port",
            ),
            timeout_seconds=_optional_number(
                connection.parameters,
                "timeout_seconds",
                5.0,
                f"connections."
                f"{connection.connection_id}."
                "parameters.timeout_seconds",
            ),
        )),
        limits=PowerSupplyLimits(
            maximum_voltage=_require_number(
                role.settings,
                "maximum_voltage",
                f"devices.{role.device_id}."
                "settings.maximum_voltage",
            ),
            maximum_current=_require_number(
                role.settings,
                "maximum_current",
                f"devices.{role.device_id}."
                "settings.maximum_current",
            ),
            maximum_power=_require_number(
                role.settings,
                "maximum_power",
                f"devices.{role.device_id}."
                "settings.maximum_power",
            ),
        ),
    )


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

    if not isinstance(value, str) or not value.strip():
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be non-empty text"
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
            f"Configuration setting {setting_name} "
            "must be an integer"
        )

    return value


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

    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be a number"
        )

    return float(value)


def _optional_number(
    values: Mapping[str, ConfigurationValue],
    key: str,
    default: float,
    setting_name: str,
) -> float:
    if key not in values:
        return default

    return _require_number(values, key, setting_name)
