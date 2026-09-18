from dataclasses import dataclass
from math import isfinite

from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
    SocketScpiConfiguration,
    VisaScpiConfiguration,
    _configuration_from_profile,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.rig_profile import RigProfile


def _validate_rig_limits(limits: PowerSupplyLimits) -> None:
    for name in ("maximum_voltage", "maximum_current", "maximum_power"):
        value = getattr(limits, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"Configured {name} must be a number")
        if not isfinite(value) or value <= 0:
            raise ValueError(f"Configured {name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class Keithley2280SConfiguration(Keithley2260BConfiguration):
    """Profile connection and rig limits, independent of the model ratings."""

    def __post_init__(self) -> None:
        Keithley2260BConfiguration.__post_init__(self)
        _validate_rig_limits(self.limits)


def configuration_from_profile(
    profile: RigProfile,
    device_id: str,
) -> Keithley2280SConfiguration:
    return _configuration_from_profile(
        profile, device_id, driver="keithley_2280s",
        configuration_type=Keithley2280SConfiguration,
    )
