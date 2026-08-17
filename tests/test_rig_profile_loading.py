from pathlib import Path

import pytest

from rig_control.rig_profile import (
    DeviceBackend,
    DeviceCapability,
)
from rig_control.rig_profile_loading import load_rig_profile


VALID_PROFILE = """
[profile]
profile_id = "standard_electrolysis"
friendly_name = "Standard electrolysis rig"

[[devices]]
device_id = "wet_co2_mfc"
friendly_name = "Wet CO2"
capability = "mass_flow_controller"
driver = "alicat"
backend = "real"
required = true
enabled = true

[devices.identity]
manufacturer = "Alicat"
model = "unknown_until_connected"
serial_number = "12345"

[[devices]]
device_id = "cell_temperature"
friendly_name = "Cell temperature"
capability = "temperature_sensor"
driver = "esp32_thermocouple"
backend = "simulated"
required = false
enabled = true
"""


def write_profile(
    temporary_path: Path,
    contents: str,
) -> Path:
    path = temporary_path / "rig-profile.toml"
    path.write_text(contents, encoding="utf-8")
    return path


def test_valid_profile_is_loaded(tmp_path: Path) -> None:
    path = write_profile(tmp_path, VALID_PROFILE)

    profile = load_rig_profile(path)

    assert profile.profile_id == "standard_electrolysis"
    assert profile.friendly_name == "Standard electrolysis rig"
    assert len(profile.device_roles) == 2


def test_device_details_are_loaded(tmp_path: Path) -> None:
    path = write_profile(tmp_path, VALID_PROFILE)

    profile = load_rig_profile(path)
    mfc = profile.get_role("wet_co2_mfc")

    assert mfc.friendly_name == "Wet CO2"
    assert (
        mfc.capability
        is DeviceCapability.MASS_FLOW_CONTROLLER
    )
    assert mfc.driver == "alicat"
    assert mfc.backend is DeviceBackend.REAL
    assert mfc.required is True
    assert mfc.enabled is True


def test_expected_identity_is_loaded(tmp_path: Path) -> None:
    path = write_profile(tmp_path, VALID_PROFILE)

    profile = load_rig_profile(path)
    identity = profile.get_role(
        "wet_co2_mfc"
    ).expected_identity

    assert identity is not None
    assert identity.manufacturer == "Alicat"
    assert identity.model == "unknown_until_connected"
    assert identity.serial_number == "12345"


def test_optional_identity_can_be_omitted(tmp_path: Path) -> None:
    path = write_profile(tmp_path, VALID_PROFILE)

    profile = load_rig_profile(path)

    assert (
        profile.get_role(
            "cell_temperature"
        ).expected_identity
        is None
    )


def test_optional_settings_have_safe_defaults(
    tmp_path: Path,
) -> None:
    contents = """
[profile]
profile_id = "minimal"
friendly_name = "Minimal rig"

[[devices]]
device_id = "temperature"
friendly_name = "Temperature"
capability = "temperature_sensor"
driver = "esp32_thermocouple"
"""
    path = write_profile(tmp_path, contents)

    role = load_rig_profile(path).get_role("temperature")

    assert role.backend is DeviceBackend.REAL
    assert role.required is True
    assert role.enabled is True


def test_missing_file_has_informative_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.toml"

    with pytest.raises(
        FileNotFoundError,
        match="Rig profile file was not found",
    ):
        load_rig_profile(path)


def test_invalid_toml_has_informative_error(
    tmp_path: Path,
) -> None:
    path = write_profile(
        tmp_path,
        "[profile\ninvalid",
    )

    with pytest.raises(
        ValueError,
        match="contains invalid TOML",
    ):
        load_rig_profile(path)


def test_unknown_capability_is_rejected(
    tmp_path: Path,
) -> None:
    contents = VALID_PROFILE.replace(
        'capability = "mass_flow_controller"',
        'capability = "mystery_device"',
        1,
    )
    path = write_profile(tmp_path, contents)

    with pytest.raises(
        ValueError,
        match="Unknown device capability",
    ):
        load_rig_profile(path)


def test_unknown_backend_is_rejected(
    tmp_path: Path,
) -> None:
    contents = VALID_PROFILE.replace(
        'backend = "real"',
        'backend = "imaginary"',
        1,
    )
    path = write_profile(tmp_path, contents)

    with pytest.raises(
        ValueError,
        match="Unknown device backend",
    ):
        load_rig_profile(path)


def test_non_boolean_required_setting_is_rejected(
    tmp_path: Path,
) -> None:
    contents = VALID_PROFILE.replace(
        "required = true",
        'required = "yes"',
        1,
    )
    path = write_profile(tmp_path, contents)

    with pytest.raises(
        TypeError,
        match=r"devices\[0\]\.required must be Boolean",
    ):
        load_rig_profile(path)


def test_duplicate_device_ids_are_rejected(
    tmp_path: Path,
) -> None:
    contents = VALID_PROFILE.replace(
        'device_id = "cell_temperature"',
        'device_id = "wet_co2_mfc"',
    )
    path = write_profile(tmp_path, contents)

    with pytest.raises(
        ValueError,
        match="Duplicate device role ID",
    ):
        load_rig_profile(path)

def test_example_profile_connections_are_loaded() -> None:
    profile = load_rig_profile("rig-profile.example.toml")

    keithley = profile.get_role("main_power_supply")
    connection = profile.get_connection(
        keithley.connection_id or ""
    )

    assert connection.connection_type == "socket_scpi"
    assert connection.parameters["port"] == 2268
    assert keithley.settings["maximum_voltage"] == 30.0


def test_example_profile_supports_shared_alicat_bus() -> None:
    profile = load_rig_profile("rig-profile.example.toml")

    nitrogen = profile.get_role("nitrogen_mfc")
    wet_co2 = profile.get_role("wet_co2_mfc")

    assert nitrogen.connection_id == "main_alicat_bus"
    assert wet_co2.connection_id == "main_alicat_bus"
    assert nitrogen.connection_parameters["address"] == "A"
    assert wet_co2.connection_parameters["address"] == "B"