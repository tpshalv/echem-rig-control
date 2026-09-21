from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ManualActionResult:
    """Result displayed after a manual control action."""

    succeeded: bool
    summary: str
    technical_details: str | None = None


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
