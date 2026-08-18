from dataclasses import replace
from datetime import UTC, datetime

import pytest

from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    AlicatSerialConfiguration,
)
from rig_control.devices.alicat.driver import AlicatMassFlowController
from rig_control.devices.alicat.protocol import (
    AlicatEngineeringUnits,
    AlicatFrameField,
    AlicatInstrumentState,
    AlicatProtocolClient,
)
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.models import DeviceStatus, Quality


class FakeAlicatProtocol(AlicatProtocolClient):
    def __init__(self, states: list[AlicatInstrumentState]) -> None:
        self.states = states
        self.read_addresses: list[str] = []
        self.set_requests: list[tuple[str, float]] = []
        self.read_error: Exception | None = None
        self.set_error: Exception | None = None

    def read_state(self, unit_address: str) -> AlicatInstrumentState:
        self.read_addresses.append(unit_address)
        if self.read_error is not None:
            raise self.read_error
        return self.states.pop(0)

    def set_flow_setpoint(self, unit_address: str, flow: float) -> None:
        self.set_requests.append((unit_address, flow))
        if self.set_error is not None:
            raise self.set_error


def state(
    *,
    mass_flow: float = 12.3,
    setpoint: float = 15.0,
    quality: Quality = Quality.GOOD,
) -> AlicatInstrumentState:
    return AlicatInstrumentState(
        mass_flow=mass_flow,
        mass_flow_unit="sccm",
        volumetric_flow=11.8,
        volumetric_flow_unit="sccm",
        absolute_pressure=14.7,
        pressure_unit="psia",
        gas_temperature=22.5,
        temperature_unit="degC",
        setpoint=setpoint,
        setpoint_unit="sccm",
        gas="N2",
        timestamp=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        quality=quality,
    )


def make_driver(
    protocol: FakeAlicatProtocol,
) -> AlicatMassFlowController:
    return AlicatMassFlowController(
        AlicatMfcConfiguration(
            device_id="mfc_a",
            friendly_name="MFC A",
            unit_address="A",
            connection=AlicatSerialConfiguration(
                connection_id="alicat_bus",
                port="COM5",
                baud_rate=19200,
                timeout_seconds=1.0,
            ),
            limits=MassFlowControllerLimits(200.0, "sccm"),
            frame_fields=(
                AlicatFrameField.ABSOLUTE_PRESSURE,
                AlicatFrameField.GAS_TEMPERATURE,
                AlicatFrameField.VOLUMETRIC_FLOW,
                AlicatFrameField.MASS_FLOW,
                AlicatFrameField.SETPOINT,
                AlicatFrameField.GAS,
            ),
            engineering_units=AlicatEngineeringUnits(
                mass_flow="sccm",
                volumetric_flow="sccm",
                absolute_pressure="psia",
                gas_temperature="degC",
                setpoint="sccm",
            ),
        ),
        protocol,
    )


def test_connect_queries_and_stores_actual_instrument_state() -> None:
    protocol = FakeAlicatProtocol([state(setpoint=37.0)])
    driver = make_driver(protocol)

    driver.connect()

    assert driver.status is DeviceStatus.READY
    assert driver.flow_setpoint == 37.0
    assert driver.current_state is not None
    assert driver.current_state.gas == "N2"
    assert protocol.read_addresses == ["A"]


def test_measurement_preserves_instrument_time_quality_and_units() -> None:
    initial = state()
    measured = state(mass_flow=42.5, quality=Quality.UNCERTAIN)
    driver = make_driver(FakeAlicatProtocol([initial, measured]))
    driver.connect()

    measurement = driver.measure_flow()

    assert measurement.value == 42.5
    assert measurement.unit == "sccm"
    assert measurement.timestamp == measured.timestamp
    assert measurement.quality is Quality.UNCERTAIN


def test_current_state_exposes_all_supported_status_values() -> None:
    instrument_state = state()
    driver = make_driver(FakeAlicatProtocol([instrument_state]))

    driver.connect()

    assert driver.current_state == instrument_state
    assert driver.current_state.volumetric_flow == 11.8
    assert driver.current_state.absolute_pressure == 14.7
    assert driver.current_state.gas_temperature == 22.5
    assert driver.current_state.gas == "N2"


def test_polling_measurements_come_from_one_complete_state_read() -> None:
    initial = state()
    measured = state(mass_flow=42.5, quality=Quality.UNCERTAIN)
    protocol = FakeAlicatProtocol([initial, measured])
    driver = make_driver(protocol)
    driver.connect()

    readings = driver.read_measurements()

    assert protocol.read_addresses == ["A", "A"]
    assert {
        reading.channel: (
            reading.measurement.value,
            reading.measurement.unit,
        )
        for reading in readings
    } == {
        "mass_flow": (42.5, "sccm"),
        "volumetric_flow": (11.8, "sccm"),
        "absolute_pressure": (14.7, "psia"),
        "gas_temperature": (22.5, "degC"),
        "setpoint": (15.0, "sccm"),
    }
    assert all(
        reading.measurement.timestamp == measured.timestamp
        and reading.measurement.quality is Quality.UNCERTAIN
        for reading in readings
    )


def test_setting_flow_is_addressed_and_then_verified() -> None:
    protocol = FakeAlicatProtocol([state(), state(setpoint=25.0)])
    driver = make_driver(protocol)
    driver.connect()

    driver.set_flow_setpoint(25.0)

    assert protocol.set_requests == [("A", 25.0)]
    assert protocol.read_addresses == ["A", "A"]
    assert driver.flow_setpoint == 25.0


@pytest.mark.parametrize("flow", [-0.1, 200.1, float("inf"), float("nan")])
def test_invalid_flow_is_rejected_without_sending(flow: float) -> None:
    protocol = FakeAlicatProtocol([state()])
    driver = make_driver(protocol)
    driver.connect()

    with pytest.raises(ValueError):
        driver.set_flow_setpoint(flow)

    assert protocol.set_requests == []


def test_operations_require_a_confirmed_connection() -> None:
    driver = make_driver(FakeAlicatProtocol([state()]))

    with pytest.raises(RuntimeError, match="not ready"):
        driver.measure_flow()


def test_connection_error_contains_port_address_operation_and_cause() -> None:
    protocol = FakeAlicatProtocol([state()])
    protocol.read_error = TimeoutError("no response")
    driver = make_driver(protocol)

    with pytest.raises(RuntimeError) as captured:
        driver.connect()

    message = str(captured.value)
    assert "COM5" in message
    assert "address 'A'" in message
    assert "connect" in message
    assert "TimeoutError: no response" in message
    assert driver.status is DeviceStatus.FAULTED


def test_reported_flow_unit_must_match_configured_unit() -> None:
    mismatched = replace(state(), mass_flow_unit="slpm")
    protocol = FakeAlicatProtocol([mismatched])
    driver = make_driver(protocol)

    with pytest.raises(RuntimeError, match="do not match configured unit"):
        driver.connect()

    assert driver.status is DeviceStatus.FAULTED


def test_read_failure_faults_previously_ready_device() -> None:
    protocol = FakeAlicatProtocol([state()])
    driver = make_driver(protocol)
    driver.connect()
    protocol.read_error = TimeoutError("lost response")

    with pytest.raises(RuntimeError, match="read state"):
        driver.measure_flow()

    assert driver.status is DeviceStatus.FAULTED


def test_set_error_faults_device_and_contains_context() -> None:
    protocol = FakeAlicatProtocol([state()])
    driver = make_driver(protocol)
    driver.connect()
    protocol.set_error = OSError("write failed")

    with pytest.raises(RuntimeError) as captured:
        driver.set_flow_setpoint(20.0)

    assert "COM5" in str(captured.value)
    assert "address 'A'" in str(captured.value)
    assert "set flow" in str(captured.value)
    assert "OSError: write failed" in str(captured.value)
    assert driver.status is DeviceStatus.FAULTED


def test_disconnect_does_not_own_or_close_shared_protocol() -> None:
    protocol = FakeAlicatProtocol([state()])
    driver = make_driver(protocol)
    driver.connect()

    driver.disconnect()

    assert driver.status is DeviceStatus.DISCONNECTED
    assert driver.current_state is None


def test_safe_state_commands_zero_and_verifies_it() -> None:
    protocol = FakeAlicatProtocol([state(), state(setpoint=0.0)])
    driver = make_driver(protocol)
    driver.connect()

    driver.enter_safe_state()

    assert protocol.set_requests == [("A", 0.0)]
    assert driver.flow_setpoint == 0.0
