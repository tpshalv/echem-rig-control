from typing import Any

import pytest

from rig_control.devices.esp32_dht11 import Esp32Dht11
from rig_control.models import DeviceStatus, Quality


class FakeBus:
    def __init__(self, channels: list[dict[str, Any]]) -> None:
        self.channels = channels
        self.clients = 0

    def acquire(self) -> None: self.clients += 1
    def release(self) -> None: self.clients -= 1
    def read_sensors(self) -> list[dict[str, Any]]: return self.channels


def valid_channels() -> list[dict[str, Any]]:
    return [
        {"name": "temperature", "value": 23.5, "unit": "degC", "quality": "good"},
        {"name": "humidity", "value": 47.0, "unit": "%RH", "quality": "uncertain"},
    ]


def test_dht11_maps_both_channels_to_measurements() -> None:
    bus = FakeBus(valid_channels())
    device = Esp32Dht11("environment", bus)  # type: ignore[arg-type]
    device.connect()

    measurements = device.read_measurements()

    assert [item.channel for item in measurements] == ["temperature", "humidity"]
    assert measurements[0].measurement.value == 23.5
    assert measurements[0].measurement.unit == "degC"
    assert measurements[0].measurement.quality is Quality.GOOD
    assert measurements[1].measurement.quality is Quality.UNCERTAIN
    assert device.status is DeviceStatus.READY
    device.disconnect()
    assert bus.clients == 0


def test_dht11_downgrades_out_of_range_good_reading_to_uncertain() -> None:
    channels = valid_channels()
    channels[1] = {
        "name": "humidity",
        "value": 95.0,
        "unit": "%RH",
        "quality": "good",
    }
    device = Esp32Dht11("environment", FakeBus(channels))  # type: ignore[arg-type]
    device.connect()

    measurements = device.read_measurements()

    assert measurements[1].measurement.value == 95.0
    assert measurements[1].measurement.quality is Quality.UNCERTAIN


@pytest.mark.parametrize(
    "channels, message",
    [
        ([{"name": "temperature", "value": None, "unit": "degC", "quality": "bad"}, valid_channels()[1]], "numeric"),
        ([valid_channels()[0]], "missing channels"),
        ([{**valid_channels()[0], "quality": "excellent"}, valid_channels()[1]], "invalid quality"),
        ([{**valid_channels()[0], "unit": "C"}, valid_channels()[1]], "expected unit"),
    ],
)
def test_dht11_rejects_unusable_channel_payloads(
    channels: list[dict[str, Any]], message: str
) -> None:
    device = Esp32Dht11("environment", FakeBus(channels))  # type: ignore[arg-type]
    device.connect()

    with pytest.raises(ValueError, match=message):
        device.read_measurements()

    assert device.status is DeviceStatus.DEGRADED
