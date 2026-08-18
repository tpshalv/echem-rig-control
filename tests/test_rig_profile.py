import pytest

from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    ExpectedDeviceIdentity,
    RigProfile,
)


def make_mfc_role(
    device_id: str = "wet_co2_mfc",
    *,
    enabled: bool = True,
    required: bool = True,
) -> DeviceRole:
    return DeviceRole(
        device_id=device_id,
        friendly_name="Wet CO2",
        capability=DeviceCapability.MASS_FLOW_CONTROLLER,
        driver="alicat",
        backend=DeviceBackend.REAL,
        required=required,
        enabled=enabled,
        expected_identity=ExpectedDeviceIdentity(
            manufacturer="Alicat",
            model="unknown_until_connected",
            serial_number="12345",
        ),
    )


def test_device_role_keeps_logical_and_hardware_details_separate() -> None:
    role = make_mfc_role()

    assert role.device_id == "wet_co2_mfc"
    assert role.friendly_name == "Wet CO2"
    assert role.capability is DeviceCapability.MASS_FLOW_CONTROLLER
    assert role.driver == "alicat"
    assert role.expected_identity is not None
    assert role.expected_identity.serial_number == "12345"


def test_profile_can_contain_different_device_capabilities() -> None:
    mfc = make_mfc_role()
    power_supply = DeviceRole(
        device_id="main_power_supply",
        friendly_name="Main power supply",
        capability=DeviceCapability.DC_POWER_SUPPLY,
        driver="keithley_2260b",
    )
    sensor = DeviceRole(
    device_id="cell_temperature",
    friendly_name="Cell temperature",
    capability=DeviceCapability.TEMPERATURE_SENSOR,
    driver="esp32_thermocouple",
    )

    profile = RigProfile(
        profile_id="standard_electrolysis",
        friendly_name="Standard electrolysis rig",
        device_roles=(mfc, power_supply, sensor),
    )

    assert len(profile.device_roles) == 3
    assert profile.get_role("main_power_supply") is power_supply


def test_profile_can_use_potentiostat_instead_of_power_supply() -> None:
    potentiostat = DeviceRole(
        device_id="main_potentiostat",
        friendly_name="Main potentiostat",
        capability=DeviceCapability.POTENTIOSTAT,
        driver="future_potentiostat",
    )

    profile = RigProfile(
        profile_id="potentiostat_experiment",
        friendly_name="Potentiostat experiment",
        device_roles=(potentiostat,),
    )

    assert (
        profile.required_roles[0].capability
        is DeviceCapability.POTENTIOSTAT
    )


def test_profile_can_contain_any_number_of_mfcs() -> None:
    profile = RigProfile(
        profile_id="four_gas_rig",
        friendly_name="Four gas rig",
        device_roles=(
            make_mfc_role("nitrogen_mfc"),
            make_mfc_role("wet_co2_mfc"),
            make_mfc_role("dry_co2_mfc"),
            make_mfc_role("calibration_gas_mfc"),
        ),
    )

    assert len(profile.required_roles) == 4


def test_disabled_role_is_not_returned_as_enabled_or_required() -> None:
    disabled = make_mfc_role(
        "spare_mfc",
        enabled=False,
        required=True,
    )

    profile = RigProfile(
        profile_id="standard_rig",
        friendly_name="Standard rig",
        device_roles=(disabled,),
    )

    assert profile.enabled_roles == ()
    assert profile.required_roles == ()


def test_enabled_optional_role_is_not_required() -> None:
    optional = make_mfc_role(
        "optional_mfc",
        enabled=True,
        required=False,
    )

    profile = RigProfile(
        profile_id="standard_rig",
        friendly_name="Standard rig",
        device_roles=(optional,),
    )

    assert profile.enabled_roles == (optional,)
    assert profile.required_roles == ()


def test_simulated_device_is_explicitly_identified() -> None:
    simulated = DeviceRole(
        device_id="simulated_mfc",
        friendly_name="Simulated MFC",
        capability=DeviceCapability.MASS_FLOW_CONTROLLER,
        driver="simulated_mfc",
        backend=DeviceBackend.SIMULATED,
    )

    assert simulated.backend is DeviceBackend.SIMULATED


def test_device_poll_interval_is_optional_and_validated() -> None:
    inherited = make_mfc_role()
    configured = DeviceRole(
        device_id="fast_supply",
        friendly_name="Fast supply",
        capability=DeviceCapability.DC_POWER_SUPPLY,
        driver="keithley_2260b",
        poll_interval_seconds=0.1,
    )

    assert inherited.poll_interval_seconds is None
    assert configured.poll_interval_seconds == 0.1


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_device_poll_interval_is_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="poll interval"):
        DeviceRole(
            device_id="sensor",
            friendly_name="Sensor",
            capability=DeviceCapability.TEMPERATURE_SENSOR,
            driver="sensor",
            poll_interval_seconds=value,
        )


def test_boolean_device_poll_interval_is_rejected() -> None:
    with pytest.raises(TypeError, match="poll interval"):
        DeviceRole(
            device_id="sensor",
            friendly_name="Sensor",
            capability=DeviceCapability.TEMPERATURE_SENSOR,
            driver="sensor",
            poll_interval_seconds=True,  # type: ignore[arg-type]
        )


def test_duplicate_device_role_ids_are_rejected() -> None:
    first = make_mfc_role("wet_co2_mfc")
    second = make_mfc_role("wet_co2_mfc")

    with pytest.raises(ValueError, match="Duplicate device role ID"):
        RigProfile(
            profile_id="invalid_profile",
            friendly_name="Invalid profile",
            device_roles=(first, second),
        )


@pytest.mark.parametrize(
    "device_id",
    ["", "   "],
)
def test_blank_device_role_id_is_rejected(
    device_id: str,
) -> None:
    with pytest.raises(ValueError, match="ID cannot be empty"):
        make_mfc_role(device_id)


def test_unknown_role_has_informative_error() -> None:
    profile = RigProfile(
        profile_id="standard_rig",
        friendly_name="Standard rig",
        device_roles=(make_mfc_role(),),
    )

    with pytest.raises(
        KeyError,
        match="Configured role IDs: wet_co2_mfc",
    ):
        profile.get_role("missing_device")

def test_connection_can_be_shared_by_multiple_devices() -> None:
    connection = ConnectionDefinition(
        connection_id="main_alicat_bus",
        connection_type="serial_text",
        parameters={
            "port": "COM4",
            "baud_rate": 19200,
        },
    )

    first = DeviceRole(
        device_id="mfc_a",
        friendly_name="MFC A",
        capability=DeviceCapability.MASS_FLOW_CONTROLLER,
        driver="alicat",
        connection_id="main_alicat_bus",
        connection_parameters={"address": "A"},
    )
    second = DeviceRole(
        device_id="mfc_b",
        friendly_name="MFC B",
        capability=DeviceCapability.MASS_FLOW_CONTROLLER,
        driver="alicat",
        connection_id="main_alicat_bus",
        connection_parameters={"address": "B"},
    )

    profile = RigProfile(
        profile_id="shared_bus",
        friendly_name="Shared bus",
        device_roles=(first, second),
        connections=(connection,),
    )

    assert (
        profile.get_connection(
            "main_alicat_bus"
        ).parameters["port"]
        == "COM4"
    )
    assert first.connection_parameters["address"] == "A"
    assert second.connection_parameters["address"] == "B"


def test_unknown_connection_reference_is_rejected() -> None:
    role = DeviceRole(
        device_id="temperature",
        friendly_name="Temperature",
        capability=DeviceCapability.TEMPERATURE_SENSOR,
        driver="esp32_thermocouple",
        connection_id="missing_esp32",
    )

    with pytest.raises(ValueError, match="unknown connection"):
        RigProfile(
            profile_id="invalid",
            friendly_name="Invalid",
            device_roles=(role,),
        )
