from rig_control.control.commands.pressure import SetPressureSetpoint, ResumePressureControl
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
from rig_control.control.commands.esp32 import (
    RearmController,
    SetControllerOutput,
    SetTemperatureSetpoint,
)
from rig_control.control.commands.pump import (
    SetPumpDirection,
    SetPumpRunning,
    SetPumpSpeed,
)


ControlCommand = (
    SetPressureSetpoint
    | ResumePressureControl
    | SetMfcFlow
    | SetPowerSupplyVoltage
    | SetPowerSupplyCurrentLimit
    | SetPowerSupplyOutput
    | EnterDeviceSafeState
    | SetControllerOutput
    | RearmController
    | SetTemperatureSetpoint
    | SetPumpSpeed
    | SetPumpDirection
    | SetPumpRunning
)


__all__ = [
    "CommandSource",
    "ControlCommand",
    "EnterDeviceSafeState",
    "SetMfcFlow",
    "SetPressureSetpoint",
    "ResumePressureControl",
    "SetPowerSupplyCurrentLimit",
    "SetPowerSupplyOutput",
    "SetPowerSupplyVoltage",
    "SetControllerOutput",
    "RearmController",
    "SetTemperatureSetpoint",
    "SetPumpDirection",
    "SetPumpRunning",
    "SetPumpSpeed",
]
