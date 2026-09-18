from dataclasses import replace

import pytest

from rig_control.device_factory import create_device_manager
from rig_control.devices.ametek_asterion.configuration import (
    AmetekAsterionConfiguration,
    SocketScpiConfiguration,
    VisaScpiConfiguration,
    configuration_from_profile,
)
from rig_control.devices.ametek_asterion.driver import AmetekAsterion
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.models import DeviceStatus
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)
from test_keithley_2260b_driver import FakeScpiTransport


def make_profile(connection_type="socket_scpi"):
    parameters = (
        {"host": "192.168.1.60", "port": 5025, "timeout_seconds": 4.0}
        if connection_type == "socket_scpi"
        else {
            "resource_name": "USB0::0x0000::0x0000::1234567::INSTR",
            "timeout_seconds": 3.0,
            "baud_rate": 19200,
        }
    )
    connection = ConnectionDefinition("supply_link", connection_type, parameters)
    role = DeviceRole(
        device_id="supply",
        friendly_name="Asterion supply",
        capability=DeviceCapability.DC_POWER_SUPPLY,
        driver="ametek_asterion",
        backend=DeviceBackend.REAL,
        connection_id=connection.connection_id,
        settings={"maximum_voltage": 30, "maximum_current": 5, "maximum_power": 100},
    )
    return RigProfile("test", "Test rig", (role,), (connection,))


@pytest.mark.parametrize("connection_type", ["socket_scpi", "visa_scpi"])
def test_profile_loads_connection_and_rig_limits(connection_type):
    configuration = configuration_from_profile(make_profile(connection_type), "supply")

    assert isinstance(configuration, AmetekAsterionConfiguration)
    assert configuration.limits == PowerSupplyLimits(30, 5, 100)
    if connection_type == "socket_scpi":
        assert configuration.connection == SocketScpiConfiguration(
            "192.168.1.60",
            5025,
            4,
        )
    else:
        assert configuration.connection == VisaScpiConfiguration(
            "USB0::0x0000::0x0000::1234567::INSTR",
            3,
            19200,
        )


@pytest.mark.parametrize("changes,message", [
    ({"driver": "keithley_2260b"}, "does not use the ametek_asterion driver"),
    ({"enabled": False}, "disabled"),
    ({"backend": DeviceBackend.SIMULATED}, "simulated"),
    ({"capability": DeviceCapability.TEMPERATURE_SENSOR}, "not configured as a DC"),
    ({"connection_id": None}, "has no connection"),
    ({"settings": {"maximum_voltage": 32, "maximum_current": 6}}, "maximum_power"),
])
def test_invalid_role_is_rejected(changes, message):
    profile = make_profile()
    profile = replace(profile, device_roles=(replace(profile.device_roles[0], **changes),))

    with pytest.raises(ValueError, match=message):
        configuration_from_profile(profile, "supply")


@pytest.mark.parametrize("name", ["maximum_voltage", "maximum_current", "maximum_power"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_rig_limits_are_rejected(name, value):
    values = dict(maximum_voltage=32, maximum_current=6, maximum_power=192)
    values[name] = value

    with pytest.raises(ValueError, match="finite"):
        AmetekAsterionConfiguration(
            "supply",
            SocketScpiConfiguration("localhost", 5025),
            PowerSupplyLimits(**values),
        )


@pytest.mark.parametrize("connection_type", ["socket_scpi", "visa_scpi"])
def test_factory_constructs_correct_driver_without_connecting(
    monkeypatch,
    connection_type,
):
    captured = []
    transport = FakeScpiTransport()

    def transport_factory(*args, **kwargs):
        captured.append((args, kwargs))
        return transport

    monkeypatch.setattr("rig_control.device_factory.SocketScpiTransport", transport_factory)
    monkeypatch.setattr("rig_control.device_factory.PyVisaScpiTransport", transport_factory)

    supply = create_device_manager(make_profile(connection_type)).get("supply")

    assert type(supply) is AmetekAsterion
    assert supply.status is DeviceStatus.DISCONNECTED
    assert not transport.is_open
    assert transport.queries == transport.writes == []
    assert supply.limits == PowerSupplyLimits(30, 5, 100)
    if connection_type == "socket_scpi":
        assert captured == [
            ((), {"host": "192.168.1.60", "port": 5025, "timeout_seconds": 4})
        ]
    else:
        assert captured == [
            (
                ("USB0::0x0000::0x0000::1234567::INSTR",),
                {"timeout_seconds": 3, "baud_rate": 19200},
            )
        ]
