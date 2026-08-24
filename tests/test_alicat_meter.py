from datetime import UTC, datetime

from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    AlicatSerialConfiguration,
)
from rig_control.devices.alicat.meter import AlicatMassFlowMeter
from rig_control.devices.alicat.protocol import (
    AlicatEngineeringUnits,
    AlicatFrameField,
    AlicatInstrumentState,
    AlicatProtocolClient,
)
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.models import DeviceStatus


class FakeMeterProtocol(AlicatProtocolClient):
    def __init__(self, states: list[AlicatInstrumentState]) -> None:
        self.states = states
        self.read_addresses = []
        self.set_requests = []

    def read_state(self, unit_address: str) -> AlicatInstrumentState:
        self.read_addresses.append(unit_address)
        return self.states.pop(0)

    def set_flow_setpoint(self, unit_address: str, flow: float) -> None:
        self.set_requests.append((unit_address, flow))


def _state(mass_flow: float) -> AlicatInstrumentState:
    return AlicatInstrumentState(
        mass_flow=mass_flow,
        mass_flow_unit="SLPM",
        volumetric_flow=1.8,
        volumetric_flow_unit="LPM",
        absolute_pressure=14.7,
        pressure_unit="psia",
        gas_temperature=22.5,
        temperature_unit="degC",
        setpoint=0.0,
        setpoint_unit="SLPM",
        gas="Air",
        timestamp=datetime(2026, 8, 24, tzinfo=UTC),
    )


def _configuration() -> AlicatMfcConfiguration:
    return AlicatMfcConfiguration(
        device_id="flow_meter_b",
        friendly_name="Flow meter B",
        unit_address="B",
        connection=AlicatSerialConfiguration(
            "alicat_bus_com5", "COM5", 19200, 1.0
        ),
        limits=MassFlowControllerLimits(2.0, "SLPM"),
        frame_fields=(
            AlicatFrameField.ABSOLUTE_PRESSURE,
            AlicatFrameField.GAS_TEMPERATURE,
            AlicatFrameField.VOLUMETRIC_FLOW,
            AlicatFrameField.MASS_FLOW,
            AlicatFrameField.GAS,
        ),
        engineering_units=AlicatEngineeringUnits(
            "SLPM", "LPM", "psia", "degC", "SLPM"
        ),
        is_controller=False,
    )


def test_meter_connects_and_exposes_measurements_without_setpoint() -> None:
    protocol = FakeMeterProtocol([_state(1.2), _state(1.3)])
    meter = AlicatMassFlowMeter(_configuration(), protocol)

    meter.connect()
    readings = meter.read_measurements()

    assert meter.status is DeviceStatus.READY
    assert protocol.read_addresses == ["B", "B"]
    assert protocol.set_requests == []
    assert {reading.channel for reading in readings} == {
        "mass_flow",
        "volumetric_flow",
        "absolute_pressure",
        "gas_temperature",
    }
    assert "setpoint" not in {reading.channel for reading in readings}


def test_meter_disconnect_never_sends_a_setpoint() -> None:
    protocol = FakeMeterProtocol([_state(1.2)])
    meter = AlicatMassFlowMeter(_configuration(), protocol)
    meter.connect()

    meter.disconnect()

    assert meter.status is DeviceStatus.DISCONNECTED
    assert protocol.set_requests == []
