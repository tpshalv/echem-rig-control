from dataclasses import dataclass
from datetime import datetime

from rig_control.devices.power_supply import (
    PowerSupplyOperatingMode,
)
from rig_control.models import Event


@dataclass(frozen=True, slots=True)
class ManualActionResult:
    """Result displayed after a manual control action."""

    succeeded: bool
    summary: str
    technical_details: str | None = None


@dataclass(frozen=True, slots=True)
class MeasurementReadFailure:
    """Technical details retained after a measurement read fails."""

    device_id: str
    measurement_name: str
    summary: str
    technical_details: str
    event: Event


@dataclass(frozen=True, slots=True)
class MfcControlRow:
    """Current manual-control information for one MFC."""

    device_id: str
    status: str
    is_available: bool
    flow_setpoint: float
    measured_flow: float | None
    measurement_time: datetime | None
    measurement_quality: str | None
    maximum_flow: float
    flow_unit: str


@dataclass(frozen=True, slots=True)
class PowerSupplyControlRow:
    """Current manual-control information for one supply."""

    device_id: str
    status: str
    is_available: bool
    operating_mode: PowerSupplyOperatingMode
    constant_current_target: float
    constant_voltage_target: float
    voltage_setpoint: float
    current_setting: float
    measured_voltage: float | None
    measured_current: float | None
    voltage_measurement_time: datetime | None
    current_measurement_time: datetime | None
    voltage_quality: str | None
    current_quality: str | None
    output_enabled: bool
    maximum_voltage: float
    maximum_current: float
    maximum_power: float


@dataclass(frozen=True, slots=True)
class PowerSupplyManualSafety:
    """Manual power-supply current/voltage safety settings.

    See decisions/0014 for the three-tier model this implements: the
    instrument's own absolute maximum is unaffected by this and comes from
    each device's own PowerSupplyLimits. This dataclass covers tiers 2-4.
    """

    high_current_mode: bool
    wiring_current_ceiling_amps: float
    default_current_amps: float
    default_voltage_volts: float


DEFAULT_WIRING_CURRENT_CEILING_AMPS = 45.0


def default_power_supply_manual_safety() -> PowerSupplyManualSafety:
    return PowerSupplyManualSafety(
        high_current_mode=False,
        wiring_current_ceiling_amps=DEFAULT_WIRING_CURRENT_CEILING_AMPS,
        default_current_amps=20.0,
        default_voltage_volts=10.0,
    )


@dataclass(frozen=True, slots=True)
class ControllerControlRow:
    device_id: str
    status: str
    is_available: bool
    safe_state_active: bool
    watchdog_tripped: bool
