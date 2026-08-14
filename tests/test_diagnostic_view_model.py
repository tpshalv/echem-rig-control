from rig_control.devices.manager import DeviceManager
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.models import DeviceStatus
from rig_control.ui.diagnostic_model import DiagnosticViewModel


def make_supply(
    device_id: str,
) -> SimulatedPowerSupply:
    return SimulatedPowerSupply(
        device_id=device_id,
        limits=PowerSupplyLimits(
            maximum_voltage=30.0,
            maximum_current=108.0,
            maximum_power=1080.0,
        ),
    )


class FailingSupply(SimulatedPowerSupply):
    def connect(self) -> None:
        raise OSError("simulated hardware unavailable")


def test_empty_manager_produces_no_rows() -> None:
    manager = DeviceManager()
    model = DiagnosticViewModel(manager)

    assert model.device_rows() == ()


def test_rows_show_device_type_and_status() -> None:
    manager = DeviceManager()
    first = make_supply("first_supply")
    second = make_supply("second_supply")
    second.connect()

    manager.register(first)
    manager.register(second)

    rows = DiagnosticViewModel(manager).device_rows()

    assert rows[0].device_id == "first_supply"
    assert rows[0].device_type == "SimulatedPowerSupply"
    assert rows[0].status == "disconnected"

    assert rows[1].device_id == "second_supply"
    assert rows[1].status == "ready"


def test_successful_connection_returns_readable_result() -> None:
    manager = DeviceManager()
    supply = make_supply("main_supply")
    manager.register(supply)
    model = DiagnosticViewModel(manager)

    result = model.connect_device("main_supply")

    assert result.succeeded is True
    assert "connected successfully" in result.summary
    assert result.technical_details is None
    assert supply.status is DeviceStatus.READY


def test_successful_disconnection_returns_readable_result() -> None:
    manager = DeviceManager()
    supply = make_supply("main_supply")
    supply.connect()
    manager.register(supply)
    model = DiagnosticViewModel(manager)

    result = model.disconnect_device("main_supply")

    assert result.succeeded is True
    assert "disconnected successfully" in result.summary
    assert result.technical_details is None
    assert supply.status is DeviceStatus.DISCONNECTED


def test_unknown_device_failure_contains_copyable_details() -> None:
    manager = DeviceManager()
    model = DiagnosticViewModel(manager)

    result = model.connect_device("missing_device")

    assert result.succeeded is False
    assert "missing_device" in result.summary
    assert "KeyError" in result.summary
    assert result.technical_details is not None
    assert "Traceback" in result.technical_details
    assert "Unknown device ID" in result.technical_details


def test_hardware_failure_contains_original_error_details() -> None:
    manager = DeviceManager()
    supply = FailingSupply(
        device_id="failing_supply",
        limits=PowerSupplyLimits(
            maximum_voltage=30.0,
            maximum_current=108.0,
            maximum_power=1080.0,
        ),
    )
    manager.register(supply)
    model = DiagnosticViewModel(manager)

    result = model.connect_device("failing_supply")

    assert result.succeeded is False
    assert "failing_supply" in result.summary
    assert "DeviceOperationError" in result.summary
    assert result.technical_details is not None
    assert "simulated hardware unavailable" in (
        result.technical_details
    )
    assert "OSError" in result.technical_details