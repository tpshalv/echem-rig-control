import math

import pytest

from rig_control.devices.mass_flow_controller import (
    MassFlowControllerLimits,
)
from rig_control.devices.simulated_mfc import (
    SimulatedMassFlowController,
)
from rig_control.models import DeviceStatus, Quality


def make_limits() -> MassFlowControllerLimits:
    return MassFlowControllerLimits(
        maximum_flow=100.0,
        flow_unit="sccm",
    )


def make_mfc() -> SimulatedMassFlowController:
    return SimulatedMassFlowController(
        device_id="dry_gas_mfc",
        limits=make_limits(),
    )


def test_limits_store_maximum_flow_and_unit() -> None:
    limits = make_limits()

    assert limits.maximum_flow == 100.0
    assert limits.flow_unit == "sccm"


@pytest.mark.parametrize("maximum_flow", ["100", True, None])
def test_non_numeric_maximum_flow_is_rejected(
    maximum_flow: object,
) -> None:
    with pytest.raises(TypeError, match="int or float"):
        MassFlowControllerLimits(
            maximum_flow=maximum_flow,  # type: ignore[arg-type]
            flow_unit="sccm",
        )


@pytest.mark.parametrize(
    "maximum_flow",
    [math.nan, math.inf, -math.inf],
)
def test_non_finite_maximum_flow_is_rejected(
    maximum_flow: float,
) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        MassFlowControllerLimits(
            maximum_flow=maximum_flow,
            flow_unit="sccm",
        )


@pytest.mark.parametrize("maximum_flow", [0.0, -1.0])
def test_non_positive_maximum_flow_is_rejected(
    maximum_flow: float,
) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        MassFlowControllerLimits(
            maximum_flow=maximum_flow,
            flow_unit="sccm",
        )


@pytest.mark.parametrize("flow_unit", ["", "   "])
def test_empty_flow_unit_is_rejected(flow_unit: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        MassFlowControllerLimits(
            maximum_flow=100.0,
            flow_unit=flow_unit,
        )


@pytest.mark.parametrize("device_id", ["", "   "])
def test_empty_device_id_is_rejected(device_id: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        SimulatedMassFlowController(
            device_id=device_id,
            limits=make_limits(),
        )


def test_mfc_starts_disconnected_and_safe() -> None:
    mfc = make_mfc()

    assert mfc.device_id == "dry_gas_mfc"
    assert mfc.status is DeviceStatus.DISCONNECTED
    assert mfc.flow_setpoint == 0.0


def test_connect_makes_mfc_ready() -> None:
    mfc = make_mfc()

    mfc.connect()

    assert mfc.status is DeviceStatus.READY


def test_flow_setpoint_can_be_changed_when_ready() -> None:
    mfc = make_mfc()
    mfc.connect()

    mfc.set_flow_setpoint(25.5)

    assert mfc.flow_setpoint == pytest.approx(25.5)


def test_simulated_measurement_has_value_unit_and_quality() -> None:
    mfc = make_mfc()
    mfc.connect()
    mfc.set_simulated_measurement(24.8)

    measurement = mfc.measure_flow()

    assert measurement.value == pytest.approx(24.8)
    assert measurement.unit == "sccm"
    assert measurement.quality is Quality.GOOD


@pytest.mark.parametrize("flow", ["25", True, None])
def test_non_numeric_flow_is_rejected(flow: object) -> None:
    mfc = make_mfc()
    mfc.connect()

    with pytest.raises(TypeError, match="int or float"):
        mfc.set_flow_setpoint(flow)  # type: ignore[arg-type]


@pytest.mark.parametrize("flow", [math.nan, math.inf, -math.inf])
def test_non_finite_flow_is_rejected(flow: float) -> None:
    mfc = make_mfc()
    mfc.connect()

    with pytest.raises(ValueError, match="must be finite"):
        mfc.set_flow_setpoint(flow)


def test_negative_flow_is_rejected() -> None:
    mfc = make_mfc()
    mfc.connect()

    with pytest.raises(ValueError, match="cannot be negative"):
        mfc.set_flow_setpoint(-0.1)


def test_flow_above_configured_maximum_is_rejected() -> None:
    mfc = make_mfc()
    mfc.connect()

    with pytest.raises(
        ValueError,
        match="exceeds configured maximum",
    ):
        mfc.set_flow_setpoint(100.1)


def test_commands_and_measurements_require_ready_state() -> None:
    mfc = make_mfc()

    with pytest.raises(RuntimeError, match="not ready"):
        mfc.set_flow_setpoint(10.0)

    with pytest.raises(RuntimeError, match="not ready"):
        mfc.set_simulated_measurement(10.0)

    with pytest.raises(RuntimeError, match="not ready"):
        mfc.measure_flow()


def test_enter_safe_state_clears_flow() -> None:
    mfc = make_mfc()
    mfc.connect()
    mfc.set_flow_setpoint(40.0)
    mfc.set_simulated_measurement(39.5)

    mfc.enter_safe_state()

    assert mfc.flow_setpoint == 0.0
    assert mfc.measure_flow().value == 0.0


def test_disconnect_enters_safe_state() -> None:
    mfc = make_mfc()
    mfc.connect()
    mfc.set_flow_setpoint(40.0)
    mfc.set_simulated_measurement(39.5)

    mfc.disconnect()

    assert mfc.status is DeviceStatus.DISCONNECTED
    assert mfc.flow_setpoint == 0.0