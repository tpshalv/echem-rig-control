from dataclasses import replace

import pytest

from rig_control.devices.keithley_2260b.configuration import (
    configuration_from_profile,
)
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)


def make_role() -> DeviceRole:
    return DeviceRole(
        device_id="main_power_supply",
        friendly_name="Main power supply",
        capability=DeviceCapability.DC_POWER_SUPPLY,
        driver="keithley_2260b",
        backend=DeviceBackend.REAL,
        connection_id="keithley_ethernet",
        settings={
            "maximum_voltage": 30.0,
            "maximum_current": 108.0,
            "maximum_power": 1080.0,
        },
    )


def make_connection() -> ConnectionDefinition:
    return ConnectionDefinition(
        connection_id="keithley_ethernet",
        connection_type="socket_scpi",
        parameters={
            "host": "192.168.1.50",
            "port": 2268,
            "timeout_seconds": 4.0,
        },
    )


def make_profile(
    role: DeviceRole | None = None,
    connection: ConnectionDefinition | None = None,
) -> RigProfile:
    return RigProfile(
        profile_id="test_rig",
        friendly_name="Test rig",
        device_roles=(role or make_role(),),
        connections=(connection or make_connection(),),
    )


def test_keithley_configuration_is_created_from_profile() -> None:
    configuration = configuration_from_profile(
        make_profile(),
        "main_power_supply",
    )

    assert configuration.device_id == "main_power_supply"
    assert configuration.connection.host == "192.168.1.50"
    assert configuration.connection.port == 2268
    assert configuration.connection.timeout_seconds == 4.0
    assert configuration.limits.maximum_voltage == 30.0
    assert configuration.limits.maximum_current == 108.0
    assert configuration.limits.maximum_power == 1080.0


def test_default_timeout_is_used_when_omitted() -> None:
    connection = ConnectionDefinition(
        connection_id="keithley_ethernet",
        connection_type="socket_scpi",
        parameters={
            "host": "192.168.1.50",
            "port": 2268,
        },
    )

    configuration = configuration_from_profile(
        make_profile(connection=connection),
        "main_power_supply",
    )

    assert configuration.connection.timeout_seconds == 5.0


def test_disabled_keithley_is_rejected() -> None:
    role = replace(make_role(), enabled=False)

    with pytest.raises(ValueError, match="is disabled"):
        configuration_from_profile(
            make_profile(role=role),
            "main_power_supply",
        )


def test_simulated_keithley_is_rejected() -> None:
    role = replace(
        make_role(),
        backend=DeviceBackend.SIMULATED,
    )

    with pytest.raises(ValueError, match="is simulated"):
        configuration_from_profile(
            make_profile(role=role),
            "main_power_supply",
        )


def test_wrong_driver_is_rejected() -> None:
    role = replace(make_role(), driver="different_driver")

    with pytest.raises(
        ValueError,
        match="does not use the keithley_2260b driver",
    ):
        configuration_from_profile(
            make_profile(role=role),
            "main_power_supply",
        )


def test_wrong_connection_type_is_rejected() -> None:
    connection = replace(
        make_connection(),
        connection_type="serial_text",
    )

    with pytest.raises(
        ValueError,
        match="must use connection type 'socket_scpi'",
    ):
        configuration_from_profile(
            make_profile(connection=connection),
            "main_power_supply",
        )


def test_missing_limit_names_the_setting() -> None:
    role = replace(
        make_role(),
        settings={
            "maximum_voltage": 30.0,
            "maximum_current": 108.0,
        },
    )

    with pytest.raises(
        ValueError,
        match="maximum_power",
    ):
        configuration_from_profile(
            make_profile(role=role),
            "main_power_supply",
        )