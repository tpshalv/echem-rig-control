from rig_control.control.commands.common import (
    CommandSource,
    EnterDeviceSafeState,
)
from rig_control.control.commands.mfc import SetMfcFlow
from rig_control.control.commands.power_supply import (
    SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput,
    SetPowerSupplyVoltage,
)
from rig_control.control.commands.esp32 import RearmController, SetControllerOutput


ControlCommand = (
    SetMfcFlow
    | SetPowerSupplyVoltage
    | SetPowerSupplyCurrentLimit
    | SetPowerSupplyOutput
    | EnterDeviceSafeState
    | SetControllerOutput
    | RearmController
)


__all__ = [
    "CommandSource",
    "ControlCommand",
    "EnterDeviceSafeState",
    "SetMfcFlow",
    "SetPowerSupplyCurrentLimit",
    "SetPowerSupplyOutput",
    "SetPowerSupplyVoltage",
    "SetControllerOutput",
    "RearmController",
]
