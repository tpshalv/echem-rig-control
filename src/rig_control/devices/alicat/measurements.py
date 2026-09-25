from rig_control.devices.alicat.protocol import AlicatInstrumentState
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.models import Measurement


def state_measurements(state: AlicatInstrumentState, *, include_setpoint: bool = True) -> tuple[DeviceMeasurement, ...]:
    values = [("mass_flow", state.mass_flow, state.mass_flow_unit),
              ("volumetric_flow", state.volumetric_flow, state.volumetric_flow_unit),
              ("absolute_pressure", state.absolute_pressure, state.pressure_unit),
              ("gas_temperature", state.gas_temperature, state.temperature_unit)]
    if include_setpoint:
        values.append(("setpoint", state.setpoint, state.setpoint_unit))
    if state.totalized_flow is not None:
        values.append(("totalized_flow", state.totalized_flow, state.totalized_flow_unit or ""))
    if state.valve_drive_percent is not None:
        values.append(("valve_drive_percent", state.valve_drive_percent, "%"))
    return tuple(DeviceMeasurement(channel, Measurement(value, unit, state.timestamp, state.quality))
                 for channel, value, unit in values)
