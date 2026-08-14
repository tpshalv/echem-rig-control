from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from rig_control.devices.power_supply import PowerSupplyLimits


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

        if not isinstance(self.port, int) or isinstance(self.port, bool):
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
class Keithley2260BConfiguration:
    """All settings needed to construct one Keithley 2260B."""

    device_id: str
    connection: SocketScpiConfiguration
    limits: PowerSupplyLimits

    def __post_init__(self) -> None:
        if (
            not isinstance(self.device_id, str)
            or not self.device_id.strip()
        ):
            raise ValueError("Keithley device ID cannot be empty")


def load_keithley_configuration(
    path: str | Path,
) -> Keithley2260BConfiguration:
    """Load and validate Keithley settings from a TOML file."""

    configuration_path = Path(path)

    try:
        with configuration_path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Rig configuration file was not found: "
            f"{configuration_path}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            f"Rig configuration file contains invalid TOML: "
            f"{configuration_path}: {error}"
        ) from error

    keithley = _require_table(
        data,
        "keithley_2260b",
        "keithley_2260b",
    )
    connection = _require_table(
        keithley,
        "connection",
        "keithley_2260b.connection",
    )
    limits = _require_table(
        keithley,
        "limits",
        "keithley_2260b.limits",
    )

    device_id = _require_text(
        keithley,
        "device_id",
        "keithley_2260b.device_id",
    )
    host = _require_text(
        connection,
        "host",
        "keithley_2260b.connection.host",
    )
    port = _require_integer(
        connection,
        "port",
        "keithley_2260b.connection.port",
    )
    timeout_seconds = _optional_number(
        connection,
        "timeout_seconds",
        5.0,
        "keithley_2260b.connection.timeout_seconds",
    )

    maximum_voltage = _require_number(
        limits,
        "maximum_voltage",
        "keithley_2260b.limits.maximum_voltage",
    )
    maximum_current = _require_number(
        limits,
        "maximum_current",
        "keithley_2260b.limits.maximum_current",
    )
    maximum_power = _require_number(
        limits,
        "maximum_power",
        "keithley_2260b.limits.maximum_power",
    )

    return Keithley2260BConfiguration(
        device_id=device_id,
        connection=SocketScpiConfiguration(
            host=host,
            port=port,
            timeout_seconds=timeout_seconds,
        ),
        limits=PowerSupplyLimits(
            maximum_voltage=maximum_voltage,
            maximum_current=maximum_current,
            maximum_power=maximum_power,
        ),
    )


def _require_table(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> dict[str, Any]:
    if key not in parent:
        raise ValueError(
            f"Missing required configuration section: "
            f"{setting_name}"
        )

    value = parent[key]

    if not isinstance(value, dict):
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be a table"
        )

    return value


def _require_text(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> str:
    if key not in parent:
        raise ValueError(
            f"Missing required configuration setting: "
            f"{setting_name}"
        )

    value = parent[key]

    if not isinstance(value, str):
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be text"
        )

    if not value.strip():
        raise ValueError(
            f"Configuration setting {setting_name} "
            "cannot be empty"
        )

    return value


def _require_integer(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> int:
    if key not in parent:
        raise ValueError(
            f"Missing required configuration setting: "
            f"{setting_name}"
        )

    value = parent[key]

    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be an integer"
        )

    return value


def _require_number(
    parent: dict[str, Any],
    key: str,
    setting_name: str,
) -> float:
    if key not in parent:
        raise ValueError(
            f"Missing required configuration setting: "
            f"{setting_name}"
        )

    return _validate_number(parent[key], setting_name)


def _optional_number(
    parent: dict[str, Any],
    key: str,
    default: float,
    setting_name: str,
) -> float:
    if key not in parent:
        return default

    return _validate_number(parent[key], setting_name)


def _validate_number(
    value: object,
    setting_name: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Configuration setting {setting_name} "
            "must be a number"
        )

    return float(value)