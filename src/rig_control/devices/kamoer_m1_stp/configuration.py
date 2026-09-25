from dataclasses import dataclass
from math import isfinite

from rig_control.devices.kamoer_m1_stp.protocol import DEFAULT_BAUD_RATE
from rig_control.devices.pump import PumpLimits
from rig_control.rig_profile import DeviceBackend, DeviceCapability, RigProfile


# Observed default slave address in captured Modbus Poll sessions against a
# real M1-STP; not stated in Kamoer's protocol document. Confirm on the bench.
DEFAULT_SLAVE_ADDRESS = 1

# Rated motor speed from the M1-STP product manual (Appendix, "Pump flow").
HARDWARE_MAXIMUM_SPEED_RPM = 350.0


@dataclass(frozen=True, slots=True)
class KamoerM1StpConfiguration:
    device_id: str
    port: str
    slave: int
    timeout_seconds: float
    limits: PumpLimits

    def __post_init__(self) -> None:
        for text in (self.device_id, self.port):
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Device ID and serial port must be nonempty text")
        if isinstance(self.slave, bool) or not isinstance(self.slave, int) or not 1 <= self.slave <= 247:
            raise ValueError("Modbus slave address must be between 1 and 247")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Timeout must be finite and positive")
        if not isinstance(self.limits, PumpLimits):
            raise TypeError("Limits must be PumpLimits")
        if self.limits.maximum_speed_rpm > HARDWARE_MAXIMUM_SPEED_RPM:
            raise ValueError(
                f"Configured maximum speed exceeds the M1-STP's rated {HARDWARE_MAXIMUM_SPEED_RPM} rpm"
            )


def configuration_from_profile(profile: RigProfile, device_id: str) -> KamoerM1StpConfiguration:
    role = profile.get_role(device_id)
    if (
        role.driver != "kamoer_m1_stp"
        or role.backend is not DeviceBackend.REAL
        or role.capability is not DeviceCapability.PERISTALTIC_PUMP
    ):
        raise ValueError("Kamoer M1-STP requires a real kamoer_m1_stp peristaltic_pump role")
    if role.connection_id is None:
        raise ValueError("Kamoer M1-STP requires a serial_binary connection")
    connection = profile.get_connection(role.connection_id)
    if connection.connection_type != "serial_binary":
        raise ValueError("Kamoer M1-STP connection must use serial_binary")
    parameters = dict(connection.parameters) | dict(role.connection_parameters)
    if parameters.get("baud_rate", DEFAULT_BAUD_RATE) != DEFAULT_BAUD_RATE:
        raise ValueError(f"Kamoer M1-STP requires {DEFAULT_BAUD_RATE} baud")
    port = parameters.get("port", "")
    slave = role.settings.get("slave", DEFAULT_SLAVE_ADDRESS)
    maximum_speed = role.settings.get("maximum_speed_rpm", HARDWARE_MAXIMUM_SPEED_RPM)
    return KamoerM1StpConfiguration(
        device_id,
        port,
        slave,
        parameters.get("timeout_seconds", 2.0),
        PumpLimits(maximum_speed),
    )
