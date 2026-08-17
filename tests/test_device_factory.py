from dataclasses import replace

import pytest

from rig_control.device_factory import (
    DeviceFactoryError,
    create_device_manager,
)
from rig_control.devices.simulated_mfc import (
    SimulatedMassFlowController,
)
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.rig_profile import (
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)
from rig_control.rig_profile_loading import load_rig_profile


def make_role(
    *,
    device_id: str = "test_mfc",
    capability: DeviceCapability = (
        DeviceCapability.MASS_FLOW_CONTROLLER
    ),
    settings: dict[str, str | int | float | bool] | None = None,
) -> DeviceRole:
    return DeviceRole(
        device_id=device_id,
        friendly_name="Test device",
        capability=capability,
        driver="test_driver",
        backend=DeviceBackend.SIMULATED,
        settings=(
            settings
            if settings is not None
            else {
                "maximum_flow": 100.0,
                "flow_unit": "sccm",
            }
        ),
    )


def make_profile(*roles: DeviceRole) -> RigProfile:
    return RigProfile(
        profile_id="test_profile",
        friendly_name="Test profile",
        device_roles=roles,
    )


def test_simulation_profile_constructs_all_enabled_devices() -> None:
    profile = load_rig_profile(
        "rig-profile.simulation.toml"
    )

    manager = create_device_manager(profile)

    assert manager.device_ids == (
        "nitrogen_mfc",
        "wet_co2_mfc",
        "dry_co2_mfc",
        "main_power_supply",
        "cell_temperature",
        "inlet_humidity",
        "cell_differential_pressure",
    )


def test_simulated_mfc_is_constructed_with_limits() -> None:
    manager = create_device_manager(make_profile(make_role()))

    device = manager.get("test_mfc")

    assert isinstance(device, SimulatedMassFlowController)
    assert device.limits.maximum_flow == 100.0
    assert device.limits.flow_unit == "sccm"


def test_simulated_power_supply_is_constructed() -> None:
    role = make_role(
        device_id="power_supply",
        capability=DeviceCapability.DC_POWER_SUPPLY,
        settings={
            "maximum_voltage": 30.0,
            "maximum_current": 108.0,
            "maximum_power": 1080.0,
        },
    )

    device = create_device_manager(
        make_profile(role)
    ).get("power_supply")

    assert isinstance(device, SimulatedPowerSupply)
    assert device.limits.maximum_power == 1080.0


def test_simulated_sensor_is_constructed() -> None:
    role = make_role(
        device_id="temperature",
        capability=DeviceCapability.TEMPERATURE_SENSOR,
        settings={
            "initial_value": 25.0,
            "unit": "degC",
        },
    )

    device = create_device_manager(
        make_profile(role)
    ).get("temperature")

    assert isinstance(device, SimulatedSensor)

    device.connect()

    assert device.read_measurement().value == 25.0
    assert device.read_measurement().unit == "degC"


def test_disabled_device_is_not_constructed() -> None:
    role = replace(make_role(), enabled=False)

    manager = create_device_manager(make_profile(role))

    assert manager.device_ids == ()


def test_real_device_is_rejected_without_connection_attempt() -> None:
    role = replace(
        make_role(),
        backend=DeviceBackend.REAL,
    )

    with pytest.raises(
        DeviceFactoryError,
        match="No connection was attempted",
    ):
        create_device_manager(make_profile(role))


def test_missing_required_setting_names_device() -> None:
    role = make_role(settings={"flow_unit": "sccm"})

    with pytest.raises(
        DeviceFactoryError,
        match=r"test_mfc.*maximum_flow",
    ):
        create_device_manager(make_profile(role))


def test_unsupported_simulated_capability_is_rejected() -> None:
    role = make_role(
        capability=DeviceCapability.POTENTIOSTAT,
        settings={},
    )

    with pytest.raises(
        DeviceFactoryError,
        match="No simulated device factory",
    ):
        create_device_manager(make_profile(role))