from dataclasses import replace

import pytest

from rig_control.devices.alicat.configuration import (
    configuration_from_profile,
)
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)
from rig_control.rig_profile_loading import load_rig_profile


def make_connection() -> ConnectionDefinition:
    return ConnectionDefinition(
        connection_id="main_alicat_bus",
        connection_type="serial_text",
        parameters={
            "port": "COM7",
            "baud_rate": 19200,
            "timeout_seconds": 1.0,
        },
    )


def make_role(
    *,
    device_id: str = "alicat_mfc_a",
    address: str = "A",
) -> DeviceRole:
    return DeviceRole(
        device_id=device_id,
        friendly_name="MFC A",
        capability=DeviceCapability.MASS_FLOW_CONTROLLER,
        driver="alicat",
        backend=DeviceBackend.REAL,
        connection_id="main_alicat_bus",
        connection_parameters={"address": address},
        settings={
            "maximum_flow": 200.0,
            "flow_unit": "sccm",
            "volumetric_flow_unit": "sccm",
            "pressure_unit": "psia",
            "temperature_unit": "degC",
            "frame_fields": (
                "absolute_pressure,gas_temperature,volumetric_flow,"
                "mass_flow,setpoint,gas"
            ),
        },
    )


def make_profile(
    *roles: DeviceRole,
    connection: ConnectionDefinition | None = None,
) -> RigProfile:
    return RigProfile(
        profile_id="test_rig",
        friendly_name="Test rig",
        device_roles=roles or (make_role(),),
        connections=(connection or make_connection(),),
    )


def test_configuration_is_created_from_profile() -> None:
    configuration = configuration_from_profile(
        make_profile(),
        "alicat_mfc_a",
    )

    assert configuration.device_id == "alicat_mfc_a"
    assert configuration.friendly_name == "MFC A"
    assert configuration.unit_address == "A"
    assert configuration.connection.connection_id == "main_alicat_bus"
    assert configuration.connection.port == "COM7"
    assert configuration.connection.baud_rate == 19200
    assert configuration.connection.timeout_seconds == 1.0
    assert configuration.limits.maximum_flow == 200.0
    assert configuration.limits.flow_unit == "sccm"


def test_lowercase_address_is_normalized() -> None:
    configuration = configuration_from_profile(
        make_profile(make_role(address="b")),
        "alicat_mfc_a",
    )

    assert configuration.unit_address == "B"


@pytest.mark.parametrize("address", ["", "AA", "1", "@"])
def test_invalid_unit_address_is_rejected(address: str) -> None:
    with pytest.raises(ValueError, match="A to Z|cannot be empty"):
        configuration_from_profile(
            make_profile(make_role(address=address)),
            "alicat_mfc_a",
        )


def test_duplicate_address_on_one_bus_is_rejected() -> None:
    first = make_role(device_id="alicat_mfc_a", address="A")
    second = make_role(device_id="alicat_mfc_b", address="a")

    with pytest.raises(
        ValueError,
        match=r"alicat_mfc_a.*alicat_mfc_b.*address 'A'",
    ):
        configuration_from_profile(
            make_profile(first, second),
            first.device_id,
        )


def test_same_address_on_different_buses_is_allowed() -> None:
    first_connection = make_connection()
    second_connection = replace(
        make_connection(),
        connection_id="second_alicat_bus",
    )
    first = make_role(device_id="alicat_mfc_a", address="A")
    second = replace(
        make_role(device_id="alicat_mfc_b", address="A"),
        connection_id=second_connection.connection_id,
    )
    profile = RigProfile(
        profile_id="test_rig",
        friendly_name="Test rig",
        device_roles=(first, second),
        connections=(first_connection, second_connection),
    )

    configuration = configuration_from_profile(
        profile,
        first.device_id,
    )

    assert configuration.unit_address == "A"


def test_simulated_role_is_rejected() -> None:
    role = replace(make_role(), backend=DeviceBackend.SIMULATED)

    with pytest.raises(ValueError, match="is simulated"):
        configuration_from_profile(make_profile(role), role.device_id)


def test_wrong_driver_is_rejected() -> None:
    role = replace(make_role(), driver="different_driver")

    with pytest.raises(ValueError, match="does not use the alicat driver"):
        configuration_from_profile(make_profile(role), role.device_id)


def test_wrong_connection_type_is_rejected() -> None:
    connection = replace(
        make_connection(),
        connection_type="socket_scpi",
    )

    with pytest.raises(ValueError, match="must use.*serial_text"):
        configuration_from_profile(
            make_profile(connection=connection),
            "alicat_mfc_a",
        )


@pytest.mark.parametrize("missing_key", ["port", "baud_rate", "timeout_seconds"])
def test_missing_connection_setting_is_named(missing_key: str) -> None:
    parameters = dict(make_connection().parameters)
    del parameters[missing_key]
    connection = replace(make_connection(), parameters=parameters)

    with pytest.raises(ValueError, match=missing_key):
        configuration_from_profile(
            make_profile(connection=connection),
            "alicat_mfc_a",
        )


def test_example_profile_uses_200_sccm_alicat_limits() -> None:
    profile = load_rig_profile("rig-profile.example.toml")

    alicat_roles = tuple(
        role
        for role in profile.enabled_roles
        if role.driver == "alicat"
    )

    assert alicat_roles
    assert all(
        role.settings["maximum_flow"] == 200.0
        and role.settings["flow_unit"] == "sccm"
        for role in alicat_roles
    )
