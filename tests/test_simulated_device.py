from rig_control.devices.simulated import SimulatedDevice
from rig_control.models import DeviceStatus


def test_simulated_device_starts_disconnected() -> None:
    device = SimulatedDevice("test_device")

    assert device.device_id == "test_device"
    assert device.status is DeviceStatus.DISCONNECTED


def test_simulated_device_can_connect() -> None:
    device = SimulatedDevice("test_device")

    device.connect()

    assert device.status is DeviceStatus.READY


def test_simulated_device_can_disconnect() -> None:
    device = SimulatedDevice("test_device")
    device.connect()

    device.disconnect()

    assert device.status is DeviceStatus.DISCONNECTED