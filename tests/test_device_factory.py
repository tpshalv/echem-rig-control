from dataclasses import replace

import pytest

from rig_control.device_factory import (
    DeviceFactoryError,
    create_device_manager,
)
from rig_control.devices.keithley_2260b.driver import Keithley2260B
from rig_control.devices.alicat.driver import AlicatMassFlowController
from rig_control.devices.alicat.configuration import AlicatSerialConfiguration
from rig_control.devices.simulated_mfc import (
    SimulatedMassFlowController,
)
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)
from rig_control.models import DeviceStatus
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.transports.simulated_serial_text import (
    SimulatedSerialTextTransport,
)


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


def make_profile(
    *roles: DeviceRole,
    connections: tuple[ConnectionDefinition, ...] = (),
) -> RigProfile:
    return RigProfile(
        profile_id="test_profile",
        friendly_name="Test profile",
        device_roles=roles,
        connections=connections,
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


def test_unsupported_real_device_is_rejected_without_connection() -> None:
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


def test_real_keithley_is_constructed_without_connecting() -> None:
    connection = ConnectionDefinition(
        connection_id="keithley_ethernet",
        connection_type="socket_scpi",
        parameters={
            "host": "192.168.1.29",
            "port": 2268,
            "timeout_seconds": 4.0,
        },
    )
    role = DeviceRole(
        device_id="main_power_supply",
        friendly_name="Main power supply",
        capability=DeviceCapability.DC_POWER_SUPPLY,
        driver="keithley_2260b",
        backend=DeviceBackend.REAL,
        connection_id=connection.connection_id,
        settings={
            "maximum_voltage": 30.0,
            "maximum_current": 108.0,
            "maximum_power": 1080.0,
        },
    )

    manager = create_device_manager(
        make_profile(role, connections=(connection,))
    )
    device = manager.get("main_power_supply")

    assert isinstance(device, Keithley2260B)
    assert device.status is DeviceStatus.DISCONNECTED
    assert device.identity is None
    assert device.limits.maximum_voltage == 30.0
    assert device.limits.maximum_current == 108.0
    assert device.limits.maximum_power == 1080.0


def make_real_alicat_role(device_id: str, address: str) -> DeviceRole:
    return DeviceRole(
        device_id=device_id,
        friendly_name=device_id.upper(),
        capability=DeviceCapability.MASS_FLOW_CONTROLLER,
        driver="alicat",
        backend=DeviceBackend.REAL,
        connection_id="alicat_bus",
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


def test_real_alicats_share_one_transport_without_connecting() -> None:
    connection = ConnectionDefinition(
        connection_id="alicat_bus",
        connection_type="serial_text",
        parameters={
            "port": "COM5",
            "baud_rate": 19200,
            "timeout_seconds": 1.0,
        },
    )
    transports: list[SimulatedSerialTextTransport] = []

    def create_transport(
        configuration: AlicatSerialConfiguration,
    ) -> SimulatedSerialTextTransport:
        assert configuration.port == "COM5"
        transport = SimulatedSerialTextTransport()
        transports.append(transport)
        return transport

    manager = create_device_manager(
        make_profile(
            make_real_alicat_role("mfc_a", "A"),
            make_real_alicat_role("mfc_b", "B"),
            connections=(connection,),
        ),
        alicat_transport_factory=create_transport,
    )

    assert isinstance(manager.get("mfc_a"), AlicatMassFlowController)
    assert isinstance(manager.get("mfc_b"), AlicatMassFlowController)
    assert len(transports) == 1
    assert transports[0].is_open is False


def test_factory_created_alicats_keep_shared_bus_open_until_last_disconnect(
) -> None:
    connection = ConnectionDefinition(
        connection_id="alicat_bus",
        connection_type="serial_text",
        parameters={
            "port": "COM5",
            "baud_rate": 19200,
            "timeout_seconds": 1.0,
        },
    )
    transport = SimulatedSerialTextTransport()
    response_a = "A 14.7 22.5 0.0 0.0 0.0 N2"
    response_b = "B 14.7 22.5 0.0 0.0 0.0 CO2"
    transport.queue_response("A", response_a)
    transport.queue_response("B", response_b)
    manager = create_device_manager(
        make_profile(
            make_real_alicat_role("mfc_a", "A"),
            make_real_alicat_role("mfc_b", "B"),
            connections=(connection,),
        ),
        alicat_transport_factory=lambda _: transport,
    )

    manager.connect("mfc_a")
    manager.connect("mfc_b")
    manager.disconnect("mfc_a")

    assert transport.is_open is True

    manager.disconnect("mfc_b")

    assert transport.is_open is False
