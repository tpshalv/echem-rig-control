from dataclasses import dataclass
from math import isfinite

from rig_control.devices.tasi_ta612c.protocol import CHANNELS
from rig_control.rig_profile import DeviceBackend, DeviceCapability, RigProfile


@dataclass(frozen=True, slots=True)
class Ta612cConfiguration:
    device_id: str
    port: str
    timeout_seconds: float = 2.0
    channels: tuple[str, ...] = CHANNELS

    def __post_init__(self) -> None:
        for text in (self.device_id, self.port):
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Device ID and serial port must be nonempty text")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("Timeout must be finite and positive")
        if (not isinstance(self.channels, tuple) or not self.channels
                or any(channel not in CHANNELS for channel in self.channels)
                or len(set(self.channels)) != len(self.channels)):
            raise ValueError("Channels must be unique IDs selected from tc1,tc2,tc3,tc4")


def configuration_from_profile(profile: RigProfile, device_id: str) -> Ta612cConfiguration:
    role = profile.get_role(device_id)
    if (role.driver != "tasi_ta612c" or role.backend is not DeviceBackend.REAL
            or role.capability is not DeviceCapability.TEMPERATURE_SENSOR):
        raise ValueError("TA612C protocol requires a real temperature_sensor role")
    if role.connection_id is None:
        raise ValueError("Temperature probe requires a serial_binary connection")
    connection = profile.get_connection(role.connection_id)
    if connection.connection_type != "serial_binary":
        raise ValueError("Temperature probe connection must use serial_binary")
    if sum(r.connection_id == role.connection_id for r in profile.enabled_roles) > 1:
        raise ValueError("One temperature-probe role must own the physical serial connection")
    parameters = dict(connection.parameters) | dict(role.connection_parameters)
    if parameters.get("baud_rate", 9600) != 9600:
        raise ValueError("TA612C protocol requires 9600 baud")
    channels = role.settings.get("channels", ",".join(CHANNELS))
    if not isinstance(channels, str):
        raise ValueError("channels must be comma-separated channel IDs")
    return Ta612cConfiguration(
        device_id, parameters.get("port", ""), parameters.get("timeout_seconds", 2.0),
        tuple(channel.strip() for channel in channels.split(",")),
    )
