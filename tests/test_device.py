import pytest

from rig_control.devices.base import Device


def test_device_cannot_be_created_directly() -> None:
    with pytest.raises(TypeError):
        Device()


def test_device_interface_defines_the_expected_contract() -> None:
    assert Device.__abstractmethods__ == {
        "device_id",
        "status",
        "connect",
        "disconnect",
    }