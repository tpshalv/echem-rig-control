from dataclasses import dataclass
from math import isfinite

from rig_control.rig_profile import DeviceBackend, DeviceCapability, RigProfile


@dataclass(frozen=True, slots=True)
class EzoHumConfiguration:
    device_id: str
    port: str
    timeout_seconds: float = 2.0
    include_dew_point: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device ID cannot be empty")
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("EZO-HUM serial port cannot be empty")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ) or not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("EZO-HUM timeout must be finite and positive")
        if not isinstance(self.include_dew_point, bool):
            raise TypeError("include_dew_point must be Boolean")


def configuration_from_profile(profile: RigProfile, device_id: str) -> EzoHumConfiguration:
    role = profile.get_role(device_id)
    if (
        role.driver != "atlas_ezo_hum"
        or role.backend is not DeviceBackend.REAL
        or role.capability is not DeviceCapability.HUMIDITY_SENSOR
    ):
        raise ValueError("EZO-HUM requires a real atlas_ezo_hum humidity_sensor role")
    if role.connection_id is None:
        raise ValueError("EZO-HUM requires a serial_text connection")
    connection = profile.get_connection(role.connection_id)
    if connection.connection_type != "serial_text":
        raise ValueError("EZO-HUM connection must use serial_text")
    parameters = dict(connection.parameters) | dict(role.connection_parameters)
    port = parameters.get("port")
    if not isinstance(port, str) or not port.strip():
        raise ValueError("EZO-HUM requires a serial port")
    if parameters.get("baud_rate", 9600) != 9600:
        raise ValueError("EZO-HUM UART requires 9600 baud")
    timeout = parameters.get("timeout_seconds", 2.0)
    return EzoHumConfiguration(
        device_id=device_id,
        port=port.strip(),
        timeout_seconds=timeout,
        include_dew_point=role.settings.get("include_dew_point", True),
    )
