"""Alicat data-frame field names, shared by the protocol and its parsers."""

from enum import StrEnum


class AlicatFrameField(StrEnum):
    """Supported values in their instrument-configured frame order."""

    ABSOLUTE_PRESSURE = "absolute_pressure"
    GAS_TEMPERATURE = "gas_temperature"
    VOLUMETRIC_FLOW = "volumetric_flow"
    MASS_FLOW = "mass_flow"
    SETPOINT = "setpoint"
    TOTALIZED_FLOW = "totalized_flow"
    GAS = "gas"
    VALVE_DRIVE = "valve_drive_percent"
