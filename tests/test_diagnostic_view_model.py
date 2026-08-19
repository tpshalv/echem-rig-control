from rig_control.devices.manager import DeviceManager
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.models import DeviceStatus
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.devices.base import Device
from rig_control.models import Measurement
from rig_control.ui.diagnostics.model import (
    DiagnosticViewModel,
)

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


class RealTestSensor(Device, MeasurementSource):
    def __init__(self, *, fail: bool = False) -> None:
        self._status = DeviceStatus.DISCONNECTED
        self._fail = fail
        self.read_count = 0

    @property
    def device_id(self) -> str:
        return "real_sensor"

    @property
    def status(self) -> DeviceStatus:
        return self._status

    def connect(self) -> None:
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        self._status = DeviceStatus.DISCONNECTED

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        self.read_count += 1
        if self._fail:
            raise TimeoutError("probe timed out")
        return (DeviceMeasurement("value", Measurement(1.0, "V")),)


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


def test_communication_test_reports_real_device_round_trip_statistics() -> None:
    manager = DeviceManager()
    sensor = RealTestSensor()
    sensor.connect()
    manager.register(sensor)

    result = DiagnosticViewModel(manager).test_communication(
        sensor.device_id,
        attempts=4,
    )

    assert result.succeeded is True
    assert "4/4 requests succeeded" in result.summary
    assert "average" in result.summary
    assert "p95" in result.summary
    assert "read_measurements" in result.summary
    assert sensor.read_count == 4


def test_communication_test_stops_after_three_consecutive_failures() -> None:
    manager = DeviceManager()
    sensor = RealTestSensor(fail=True)
    sensor.connect()
    manager.register(sensor)

    result = DiagnosticViewModel(manager).test_communication(sensor.device_id)

    assert result.succeeded is False
    assert "0/3 requests succeeded" in result.summary
    assert "Stopped after 3 consecutive failures" in result.summary
    assert result.technical_details is not None
    assert result.technical_details.count("probe timed out") == 3


def test_communication_test_rejects_simulated_device() -> None:
    manager = DeviceManager()
    supply = make_supply("simulated_supply")
    supply.connect()
    manager.register(supply)

    result = DiagnosticViewModel(manager).test_communication(supply.device_id)

    assert result.succeeded is False
    assert "only available for real devices" in result.summary
