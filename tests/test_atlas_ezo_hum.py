from dataclasses import replace

import pytest

from rig_control.device_factory import create_device_manager
from rig_control.devices.atlas_ezo_hum.configuration import (
    EzoHumConfiguration,
    configuration_from_profile,
)
from rig_control.devices.atlas_ezo_hum.driver import AtlasEzoHum
from rig_control.devices.atlas_ezo_hum.protocol import EzoHumProtocol, EzoHumProtocolError
from rig_control.models import DeviceStatus, Quality
from rig_control.rig_profile import ConnectionDefinition, DeviceCapability, DeviceRole, RigProfile
from rig_control.transports.serial_text import SerialTextTransport


class FakeTransport(SerialTextTransport):
    def __init__(self, responses=None):
        self._is_open = False
        self.requests: list[str] = []
        self.responses = list(responses or ["*OK", "*OK", "*OK", "?i,HUM,1.01", "48.2,22.4,10.5", "48.2,22.4,10.5"])

    @property
    def is_open(self):
        return self._is_open

    def open(self):
        self._is_open = True

    def close(self):
        self._is_open = False

    def request(self, message):
        self.requests.append(message)
        return self.responses.pop(0)


def make_device(*, dew_point=True, responses=None):
    transport = FakeTransport(responses)
    device = AtlasEzoHum(
        EzoHumConfiguration("inlet_humidity", "COM8", include_dew_point=dew_point),
        EzoHumProtocol(transport),
    )
    return device, transport


def make_profile(**changes):
    role = DeviceRole(
        "inlet_humidity", "Inlet humidity", DeviceCapability.HUMIDITY_SENSOR,
        "atlas_ezo_hum", connection_id="humidity_serial",
        settings={"include_dew_point": True},
    )
    return RigProfile(
        "test", "Test", (replace(role, **changes),),
        (ConnectionDefinition("humidity_serial", "serial_text", {
            "port": "COM8", "baud_rate": 9600, "timeout_seconds": 2.0,
        }),),
    )


def test_connect_configures_on_demand_temperature_and_reads_measurements():
    device, transport = make_device()
    device.connect()

    assert device.identity is not None
    assert device.identity.firmware_version == "1.01"
    assert transport.requests == ["C,0", "O,T,1", "O,Dew,1", "i", "R"]
    readings = device.read_measurements()

    assert [(item.channel, item.measurement.value, item.measurement.unit) for item in readings] == [
        ("humidity", 48.2, "%RH"), ("temperature", 22.4, "degC"),
        ("dew_point", 10.5, "degC"),
    ]
    assert all(item.measurement.timestamp.tzinfo is not None for item in readings)
    assert device.status is DeviceStatus.READY


def test_one_final_continuous_reading_is_discarded_when_disabling_continuous_mode():
    device, transport = make_device(responses=[
        "48.2", "*OK", "*OK", "*OK", "?i,HUM,1.01", "48.2,22.4,10.5",
    ])
    device.connect()

    assert transport.requests[:2] == ["C,0", "C,0"]


def test_optional_dew_point_is_configured_and_returned():
    device, transport = make_device(dew_point=True, responses=[
        "*OK", "*OK", "*OK", "?i,HUM,1.01", "48.2,22.4,10.5", "48.3,22.5,10.6",
    ])
    device.connect()
    readings = device.read_measurements()

    assert transport.requests[2] == "O,Dew,1"
    assert [(item.channel, item.measurement.value) for item in readings] == [
        ("humidity", 48.3), ("temperature", 22.5), ("dew_point", 10.6),
    ]


def test_invalid_or_short_reading_is_never_recorded_as_fresh_measurement():
    device, _ = make_device(responses=[
        "*OK", "*OK", "*OK", "?i,HUM,1.01", "48.2,22.4,10.5", "48.2",
    ])
    device.connect()
    with pytest.raises(EzoHumProtocolError):
        device.read_measurements()
    assert device.status is DeviceStatus.DEGRADED


def test_out_of_range_wet_sensor_reading_is_preserved_as_uncertain():
    device, _ = make_device(responses=[
        "*OK", "*OK", "*OK", "?i,HUM,1.01", "101.2,22.4,10.5", "101.2,22.4,10.5",
    ])
    device.connect()
    readings = device.read_measurements()
    assert readings[0].measurement.quality is Quality.UNCERTAIN


def test_profile_configuration_and_factory_construct_without_opening(monkeypatch):
    captured = []

    def factory(*args):
        captured.append(args)
        return FakeTransport()

    monkeypatch.setattr("rig_control.device_factory.PySerialTextTransport", factory)
    device = create_device_manager(make_profile()).get("inlet_humidity")

    assert isinstance(device, AtlasEzoHum)
    assert device.status is DeviceStatus.DISCONNECTED
    assert captured == [("COM8", 9600, 2.0)]
    assert configuration_from_profile(make_profile(), "inlet_humidity").port == "COM8"


@pytest.mark.parametrize("changes", [
    {"capability": DeviceCapability.TEMPERATURE_SENSOR},
    {"driver": "other"},
])
def test_profile_configuration_rejects_wrong_role(changes):
    with pytest.raises(ValueError):
        configuration_from_profile(make_profile(**changes), "inlet_humidity")
