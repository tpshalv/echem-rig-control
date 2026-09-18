"""Guardian G52 ratings: OHAUS manual 30910709, revision A, EN-25/26."""

from dataclasses import dataclass
from math import isfinite
import re
from types import MappingProxyType

from rig_control.rig_profile import DeviceBackend, DeviceCapability, RigProfile


@dataclass(frozen=True, slots=True)
class ModelSpec:
    heating: bool
    stirring: bool
    maximum_temperature: float | None
    minimum_speed: int | None
    maximum_speed: int | None


MODEL_SPECS = MappingProxyType({
    "e-G52HSRDA": ModelSpec(True, True, 360, 50, 1800),
    "e-G52HS10C": ModelSpec(True, True, 500, 50, 1800),
    "e-G52HS07C": ModelSpec(True, True, 550, 50, 1800),
    "e-G52HP07C": ModelSpec(True, False, 550, None, None),
    "e-G52ST07C": ModelSpec(False, True, None, 50, 1800),
})


def normalize_model(model: str) -> str:
    # Accept documented K1 kits and voltage/region descriptions, not arbitrary
    # prefixes (in particular, older G51 instruments are not G52 instruments).
    match = re.fullmatch(
        r"(e-G52(?:HSRDA|HS10C|HS07C|HP07C|ST07C))(?:-K1)?"
        r"(?:\s+(?:120|230)V(?:\s+(?:EU|UK|US|AU))?)?",
        model.strip(), re.IGNORECASE,
    )
    if match is None:
        raise ValueError(f"Unsupported Guardian 5000 model: {model!r}")
    return next(key for key in MODEL_SPECS if key.upper() == match[1].upper())


def finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True, slots=True)
class GuardianLimits:
    """Optional rig or run ceilings, separate from immutable model ratings."""

    maximum_temperature: float | None = None
    maximum_speed: float | None = None

    def __post_init__(self) -> None:
        for name in ("maximum_temperature", "maximum_speed"):
            value = getattr(self, name)
            if value is not None and finite_number(value, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class GuardianConfiguration:
    device_id: str
    port: str
    timeout_seconds: float
    limits: GuardianLimits


def configuration_from_profile(profile: RigProfile, device_id: str) -> GuardianConfiguration:
    role = profile.get_role(device_id)
    if (role.driver != "ohaus_guardian_5000" or role.backend is not DeviceBackend.REAL
            or role.capability is not DeviceCapability.HOTPLATE_STIRRER):
        raise ValueError("Guardian requires a real ohaus_guardian_5000 hotplate_stirrer role")
    if role.connection_id is None:
        raise ValueError("Guardian requires a serial_text connection")
    connection = profile.get_connection(role.connection_id)
    if connection.connection_type != "serial_text":
        raise ValueError("Guardian connection must use serial_text")
    parameters = dict(connection.parameters) | dict(role.connection_parameters)
    port = parameters.get("port")
    if not isinstance(port, str) or not port.strip():
        raise ValueError("Guardian requires a serial port")
    if parameters.get("baud_rate", 9600) != 9600:
        raise ValueError("Guardian requires 9600 baud")
    timeout = finite_number(parameters.get("timeout_seconds", 2.0), "Timeout")
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    return GuardianConfiguration(device_id, port.strip(), timeout, GuardianLimits(
        role.settings.get("maximum_temperature"), role.settings.get("maximum_speed"),
    ))
