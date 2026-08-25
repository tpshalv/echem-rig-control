import threading

import pytest

from rig_control.devices.manager import (
    DeviceManager,
    DeviceOperationError,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.models import DeviceStatus


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


class FailingConnectSupply(SimulatedPowerSupply):
    def connect(self) -> None:
        raise OSError("simulated connection failure")


class FailingDisconnectSupply(SimulatedPowerSupply):
    def disconnect(self) -> None:
        raise OSError("simulated disconnection failure")


class HangingDisconnectSupply(SimulatedPowerSupply):
    """A device whose disconnect() blocks until the test releases it."""

    def __init__(self, device_id: str, release_event: threading.Event) -> None:
        super().__init__(
            device_id=device_id,
            limits=PowerSupplyLimits(
                maximum_voltage=30.0,
                maximum_current=108.0,
                maximum_power=1080.0,
            ),
        )
        self._release_event = release_event

    def disconnect(self) -> None:
        self._release_event.wait()
        super().disconnect()


class RecordingDisconnectSupply(SimulatedPowerSupply):
    def __init__(
        self,
        device_id: str,
        disconnection_order: list[str],
        *,
        fail: bool = False,
    ) -> None:
        super().__init__(
            device_id=device_id,
            limits=PowerSupplyLimits(
                maximum_voltage=30.0,
                maximum_current=108.0,
                maximum_power=1080.0,
            ),
        )
        self._disconnection_order = disconnection_order
        self._fail = fail

    def disconnect(self) -> None:
        self._disconnection_order.append(self.device_id)

        if self._fail:
            raise OSError("simulated disconnection failure")

        super().disconnect()


def test_manager_starts_empty() -> None:
    manager = DeviceManager()

    assert manager.device_ids == ()
    assert manager.summaries() == ()


def test_device_can_be_registered_and_retrieved() -> None:
    manager = DeviceManager()
    supply = make_supply("main_supply")

    manager.register(supply)

    assert manager.device_ids == ("main_supply",)
    assert manager.get("main_supply") is supply


def test_device_ids_preserve_registration_order() -> None:
    manager = DeviceManager()
    manager.register(make_supply("first"))
    manager.register(make_supply("second"))
    manager.register(make_supply("third"))

    assert manager.device_ids == (
        "first",
        "second",
        "third",
    )


def test_duplicate_device_id_is_rejected() -> None:
    manager = DeviceManager()
    manager.register(make_supply("main_supply"))

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        manager.register(make_supply("main_supply"))


def test_non_device_object_is_rejected() -> None:
    manager = DeviceManager()

    with pytest.raises(
        TypeError,
        match="Device interface",
    ):
        manager.register(object())  # type: ignore[arg-type]


def test_unknown_device_error_lists_available_ids() -> None:
    manager = DeviceManager()
    manager.register(make_supply("main_supply"))
    manager.register(make_supply("backup_supply"))

    with pytest.raises(
        KeyError,
        match="main_supply",
    ) as captured_error:
        manager.get("missing_supply")

    assert "backup_supply" in str(captured_error.value)
    assert "missing_supply" in str(captured_error.value)


def test_summaries_report_type_and_current_status() -> None:
    manager = DeviceManager()
    first = make_supply("first")
    second = make_supply("second")
    second.connect()

    manager.register(first)
    manager.register(second)

    summaries = manager.summaries()

    assert summaries[0].device_id == "first"
    assert summaries[0].device_type == "SimulatedPowerSupply"
    assert summaries[0].status is DeviceStatus.DISCONNECTED

    assert summaries[1].device_id == "second"
    assert summaries[1].status is DeviceStatus.READY


def test_manager_can_connect_named_device() -> None:
    manager = DeviceManager()
    supply = make_supply("main_supply")
    manager.register(supply)

    manager.connect("main_supply")

    assert supply.status is DeviceStatus.READY


def test_manager_publishes_connection_and_disconnection_events() -> None:
    recorded = []
    manager = DeviceManager(
        event_sink=lambda event, details: recorded.append((event, details))
    )
    supply = make_supply("main_supply")
    manager.register(supply)

    manager.connect("main_supply")
    manager.disconnect("main_supply")

    assert [item[0].message for item in recorded] == [
        "Device connected successfully.",
        "Device disconnected successfully.",
    ]
    assert all(item[1] is None for item in recorded)


def test_manager_publishes_connection_failure_with_details() -> None:
    recorded = []
    manager = DeviceManager(
        event_sink=lambda event, details: recorded.append((event, details))
    )
    manager.register(
        FailingConnectSupply(
            "failing_supply",
            PowerSupplyLimits(30.0, 108.0, 1080.0),
        )
    )

    with pytest.raises(DeviceOperationError):
        manager.connect("failing_supply")

    assert len(recorded) == 1
    assert recorded[0][0].severity.value == "error"
    assert "simulated connection failure" in recorded[0][0].message
    assert recorded[0][1] is not None
    assert "Traceback" in recorded[0][1]


def test_logging_failure_does_not_mask_successful_device_operation() -> None:
    def fail_to_log(_event: object, _details: object) -> None:
        raise OSError("disk unavailable")

    manager = DeviceManager(event_sink=fail_to_log)
    supply = make_supply("main_supply")
    manager.register(supply)

    manager.connect("main_supply")

    assert supply.status is DeviceStatus.READY
    assert len(manager.event_sink_failures) == 1
    assert "disk unavailable" in manager.event_sink_failures[0]


def test_device_operation_returns_registered_device() -> None:
    manager = DeviceManager()
    supply = make_supply("main_supply")
    manager.register(supply)

    with manager.operation("main_supply") as selected:
        assert selected is supply


def test_connection_failure_has_device_context() -> None:
    manager = DeviceManager()
    supply = FailingConnectSupply(
        device_id="failing_supply",
        limits=PowerSupplyLimits(30.0, 108.0, 1080.0),
    )
    manager.register(supply)

    with pytest.raises(
        DeviceOperationError,
        match="failing_supply",
    ) as captured_error:
        manager.connect("failing_supply")

    message = str(captured_error.value)

    assert "FailingConnectSupply" in message
    assert "OSError" in message
    assert "simulated connection failure" in message
    assert isinstance(captured_error.value.__cause__, OSError)


def test_manager_can_disconnect_named_device() -> None:
    manager = DeviceManager()
    supply = make_supply("main_supply")
    supply.connect()
    manager.register(supply)

    manager.disconnect("main_supply")

    assert supply.status is DeviceStatus.DISCONNECTED


def test_disconnection_failure_has_device_context() -> None:
    manager = DeviceManager()
    supply = FailingDisconnectSupply(
        device_id="failing_supply",
        limits=PowerSupplyLimits(30.0, 108.0, 1080.0),
    )
    supply.connect()
    manager.register(supply)

    with pytest.raises(
        DeviceOperationError,
        match="failing_supply",
    ) as captured_error:
        manager.disconnect("failing_supply")

    message = str(captured_error.value)

    assert "FailingDisconnectSupply" in message
    assert "OSError" in message
    assert "simulated disconnection failure" in message
    assert isinstance(captured_error.value.__cause__, OSError)


def test_disconnect_all_uses_reverse_order_and_continues() -> None:
    order: list[str] = []
    manager = DeviceManager()

    first = RecordingDisconnectSupply("first", order)
    second = RecordingDisconnectSupply(
        "second",
        order,
        fail=True,
    )
    third = RecordingDisconnectSupply("third", order)

    first.connect()
    second.connect()
    third.connect()

    manager.register(first)
    manager.register(second)
    manager.register(third)

    failures = manager.disconnect_all()

    assert order == ["third", "second", "first"]
    assert len(failures) == 1
    assert "second" in str(failures[0])
    assert third.status is DeviceStatus.DISCONNECTED
    assert first.status is DeviceStatus.DISCONNECTED


def test_disconnect_all_times_out_a_hung_device_and_continues() -> None:
    manager = DeviceManager()
    release_event = threading.Event()
    hung = HangingDisconnectSupply("hung_supply", release_event)
    healthy = make_supply("healthy_supply")

    hung.connect()
    healthy.connect()
    manager.register(hung)
    manager.register(healthy)

    try:
        failures = manager.disconnect_all(per_device_timeout=0.2)

        assert len(failures) == 1
        assert "hung_supply" in str(failures[0])
        assert "did not disconnect within" in str(failures[0])
        # The other device isn't blocked by the hung one.
        assert healthy.status is DeviceStatus.DISCONNECTED
    finally:
        release_event.set()
