from dataclasses import replace

from rig_control.devices.alicat.configuration import AlicatMfcConfiguration
from rig_control.devices.alicat.protocol import (
    AlicatFrameField,
    AlicatInstrumentState,
)
from rig_control.devices.ametek_asterion.configuration import (
    AmetekAsterionConfiguration,
)
from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
)
from rig_control.devices.keithley_2280s.configuration import (
    Keithley2280SConfiguration,
)
from rig_control.devices.keithley_2260b.protocol import KeithleyIdentity
from rig_control.devices.kamoer_m1_stp.configuration import KamoerM1StpConfiguration
from rig_control.devices.ohaus_guardian_5000.configuration import GuardianConfiguration
from rig_control.devices.ohaus_guardian_5000.protocol import GuardianIdentity, OperatingMode
from rig_control.devices.pump import PumpDirection
from rig_control.diagnostics.alicat import AlicatDiagnosticResult, DiscoveredAlicat
from rig_control.diagnostics.kamoer_m1_stp import KamoerM1StpDiagnosticResult
from rig_control.diagnostics.ohaus_guardian import GuardianDiagnosticResult
from rig_control.diagnostics.esp32 import Esp32ReadinessResult
from rig_control.diagnostics.esp32 import Esp32DiscoveryResult
from rig_control.rig_profile import DeviceBackend, DeviceCapability
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.ui.device_setup.model import (
    AddAlicatRequest,
    AddGuardianRequest,
    AddKeithleyRequest,
    AddEsp32Request,
    AddKamoerM1StpRequest,
    AlicatScanRow,
    DeviceSetupViewModel,
    SerialPortInfo,
)


def make_model(
    *,
    serial_ports: tuple[SerialPortInfo, ...] = (),
    alicat_checker=None,
    keithley_checker=None,
    alicat_scanner=None,
) -> DeviceSetupViewModel:
    arguments = {
        "serial_port_provider": lambda: serial_ports,
    }
    if alicat_checker is not None:
        arguments["alicat_checker"] = alicat_checker
    if keithley_checker is not None:
        arguments["keithley_checker"] = keithley_checker
    if alicat_scanner is not None:
        arguments["alicat_scanner"] = alicat_scanner
    return DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        **arguments,
    )


def test_rows_show_configured_labels_types_and_remembered_targets() -> None:
    model = make_model()

    rows = {row.device_id: row for row in model.device_rows()}

    assert rows["nitrogen_mfc"].friendly_name == "Nitrogen"
    assert rows["nitrogen_mfc"].connection == "CHANGE_ME, address A"
    assert "alicat" in rows["nitrogen_mfc"].device_type
    assert rows["nitrogen_mfc"].measurement_interval == "App default"
    assert rows["main_power_supply"].connection == "CHANGE_ME:2268"
    assert rows["main_power_supply"].readiness == "not checked"


def test_disabled_devices_remain_visible_for_editing() -> None:
    profile = load_rig_profile("rig-profile.example.toml")
    disabled = replace(profile.get_role("nitrogen_mfc"), enabled=False)
    profile = replace(
        profile,
        device_roles=tuple(
            disabled if role.device_id == disabled.device_id else role
            for role in profile.device_roles
        ),
    )
    model = DeviceSetupViewModel(profile)

    rows = {row.device_id: row for row in model.device_rows()}

    assert rows["nitrogen_mfc"].enabled is False


def test_device_editor_updates_common_connection_and_driver_values() -> None:
    saved = []
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        profile_writer=lambda profile, _path: saved.append(profile) or None,
    )
    original = model.device_edit_values("nitrogen_mfc")
    connection = dict(original.connection_parameters)
    connection["port"] = "COM5"
    settings = dict(original.settings)
    settings["maximum_flow"] = 1500.0

    result = model.update_device(
        replace(
            original,
            friendly_name="Nitrogen inlet",
            enabled=False,
            poll_interval_seconds=2.0,
            connection_parameters=connection,
            settings=settings,
        )
    )

    assert result.succeeded is True
    role = saved[-1].get_role("nitrogen_mfc")
    assert role.friendly_name == "Nitrogen inlet"
    assert role.enabled is False
    assert role.poll_interval_seconds == 2.0
    assert role.settings["maximum_flow"] == 1500.0
    assert saved[-1].get_connection(role.connection_id).parameters["port"] == "COM5"


def test_removing_device_also_removes_its_unused_connection() -> None:
    saved = []
    profile = load_rig_profile("rig-profile.example.toml")
    model = DeviceSetupViewModel(
        profile,
        profile_writer=lambda candidate, _path: saved.append(candidate) or None,
    )
    removed_connection = profile.get_role("main_power_supply").connection_id

    result = model.remove_device("main_power_supply")

    assert result.succeeded is True
    assert all(role.device_id != "main_power_supply" for role in saved[-1].device_roles)
    assert all(
        connection.connection_id != removed_connection
        for connection in saved[-1].connections
    )


def test_profile_can_be_duplicated_renamed_and_reloaded(tmp_path) -> None:
    destination = tmp_path / "copied-rig.toml"
    model = make_model()

    saved = model.save_profile_as(destination)
    renamed = model.update_profile_identity("copied_rig", "Copied rig")

    assert saved.succeeded is True
    assert renamed.succeeded is True
    assert model.profile_path == destination
    assert load_rig_profile(destination).friendly_name == "Copied rig"

    switched = model.switch_profile("rig-profile.esp32.toml")
    assert switched.succeeded is True
    assert model.profile.profile_id == "esp32_hardware_poc"


def test_new_profile_is_blank_saved_and_selected(tmp_path) -> None:
    model = make_model()
    destination = tmp_path / "rig-profile.new-cell.toml"

    result = model.create_new_profile("new_cell", "New Cell", destination)

    assert result.succeeded is True
    assert model.profile_path == destination
    assert model.profile.profile_id == "new_cell"
    assert model.profile.friendly_name == "New Cell"
    assert model.profile.device_roles == ()
    assert model.profile.connections == ()
    assert load_rig_profile(destination) == model.profile


def test_new_profile_adds_toml_extension_when_omitted(tmp_path) -> None:
    model = make_model()
    destination = tmp_path / "rig-profile.new-cell"

    result = model.create_new_profile("new_cell", "New Cell", destination)

    expected = tmp_path / "rig-profile.new-cell.toml"
    assert result.succeeded is True
    assert model.profile_path == expected
    assert expected.exists()


def test_new_profile_rejects_invalid_id_without_changing_selection(tmp_path) -> None:
    model = make_model()
    original_profile = model.profile
    original_path = model.profile_path

    result = model.create_new_profile(
        "Invalid ID",
        "Invalid rig",
        tmp_path / "invalid.toml",
    )

    assert result.succeeded is False
    assert model.profile is original_profile
    assert model.profile_path == original_path


def test_discovered_esp32_adds_controller_connection_and_supported_sensors(
    tmp_path,
) -> None:
    profile_path = tmp_path / "blank.toml"
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        profile_path=profile_path,
        serial_port_provider=lambda: (),
    )
    discovery = Esp32DiscoveryResult(
        identity={
            "controller_id": "esp32_main_controller",
            "firmware_version": "0.1.0",
            "protocol_version": 1,
        },
        capabilities={
            "devices": [
                {
                    "id": "esp32_dht11",
                    "kind": "dht11",
                    "label": "DHT11 temperature and humidity",
                    "channels": [
                        {"name": "temperature", "unit": "degC"},
                        {"name": "humidity", "unit": "%RH"},
                    ],
                },
                {"id": "future_sensor", "kind": "future_type"},
            ],
            "outputs": [],
        },
    )

    result = model.add_discovered_esp32(
        AddEsp32Request(port="COM7"),
        discovery,
        controller_friendly_name="Reactor safety controller",
        selected_device_names={"esp32_dht11": "Outlet humidity"},
        selected_device_intervals={"esp32_dht11": 3.0},
    )

    assert result.succeeded is True
    assert "future_type" in result.summary
    controller = model.profile.get_role("esp32_main_controller")
    sensor = model.profile.get_role("esp32_dht11")
    assert controller.driver == "esp32_json"
    assert controller.friendly_name == "Reactor safety controller"
    assert sensor.driver == "esp32_dht11"
    assert sensor.friendly_name == "Outlet humidity"
    assert sensor.poll_interval_seconds == 3.0
    assert sensor.connection_id == controller.connection_id
    connection = model.profile.get_connection(controller.connection_id)
    assert connection.parameters["port"] == "COM7"
    assert connection.parameters["baud_rate"] == 115200
    assert load_rig_profile(profile_path) == model.profile


def test_discovered_esp32_only_adds_selected_sensor_devices(tmp_path) -> None:
    profile_path = tmp_path / "controller-only.toml"
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        profile_path=profile_path,
        serial_port_provider=lambda: (),
    )
    discovery = Esp32DiscoveryResult(
        identity={"controller_id": "controller_only", "protocol_version": 1},
        capabilities={
            "devices": [
                {
                    "id": "optional_dht11",
                    "kind": "dht11",
                    "label": "Optional sensor",
                }
            ],
            "outputs": [],
        },
    )

    result = model.add_discovered_esp32(
        AddEsp32Request(port="COM8"),
        discovery,
        selected_device_names={},
    )

    assert result.succeeded is True
    assert model.profile.get_role("controller_only").driver == "esp32_json"
    assert all(
        role.device_id != "optional_dht11"
        for role in model.profile.device_roles
    )


def test_rediscovery_reuses_existing_esp32_and_adds_new_re72() -> None:
    saved = []
    original = load_rig_profile("rig-profile.esp32.toml")
    # The example now includes this controller; rediscovery must start without it.
    original = replace(original, device_roles=tuple(
        role for role in original.device_roles if role.device_id != "re72_1"
    ))
    model = DeviceSetupViewModel(
        original,
        profile_writer=lambda profile, _path: saved.append(profile) or None,
    )
    discovery = Esp32DiscoveryResult(
        identity={"controller_id": "esp32_main_controller", "protocol_version": 1},
        capabilities={
            "devices": [
                {"id": "esp32_dht11", "kind": "dht11", "label": "DHT11"},
                {
                    "id": "re72_1",
                    "kind": "lumel_re72",
                    "label": "Lumel RE72 controller 1",
                    "slave": 1,
                },
            ],
            "outputs": [],
        },
    )

    result = model.add_discovered_esp32(
        AddEsp32Request(port="COM5"),
        discovery,
        selected_device_names={
            "esp32_dht11": "DHT11",
            "re72_1": "Heater controller 1",
        },
    )

    assert result.succeeded is True
    assert len(saved) == 1
    assert len(model.profile.connections) == len(original.connections)
    assert sum(
        role.device_id == "esp32_main_controller"
        for role in model.profile.device_roles
    ) == 1
    re72 = model.profile.get_role("re72_1")
    assert re72.driver == "lumel_re72"
    assert re72.settings["slave"] == 1
    assert re72.connection_id == original.get_role(
        "esp32_main_controller"
    ).connection_id


def test_measurement_interval_can_be_edited_without_hardware_check() -> None:
    saved_profiles = []
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        profile_writer=lambda profile, _path: (
            saved_profiles.append(profile) or None
        ),
    )

    result = model.update_measurement_interval("main_power_supply", 0.1)

    assert result.succeeded is True
    assert model.device_poll_interval("main_power_supply") == 0.1
    rows = {row.device_id: row for row in model.device_rows()}
    assert rows["main_power_supply"].measurement_interval == "0.1 s"
    assert saved_profiles[0].get_role(
        "main_power_supply"
    ).poll_interval_seconds == 0.1


def test_invalid_measurement_interval_is_not_saved() -> None:
    saved_profiles = []
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        profile_writer=lambda profile, _path: saved_profiles.append(profile),
    )

    result = model.update_measurement_interval("main_power_supply", 0)

    assert result.succeeded is False
    assert "greater than zero" in result.technical_details
    assert saved_profiles == []


def test_serial_port_provider_is_exposed_without_opening_ports() -> None:
    expected = (
        SerialPortInfo("COM5", "USB Serial Port", "USB VID:PID=1234:5678"),
    )
    model = make_model(serial_ports=expected)

    assert model.serial_ports() == expected
    assert model.serial_port_error == ""


def test_serial_port_discovery_error_is_reported_without_crashing() -> None:
    def fail():
        raise PermissionError("enumeration blocked")

    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        serial_port_provider=fail,
    )

    assert model.serial_ports() == ()
    assert "PermissionError: enumeration blocked" == model.serial_port_error


def test_alicat_scan_passes_selected_port_and_baud_rate() -> None:
    calls = []
    discovered = (DiscoveredAlicat("B", "B 14.7 22.5 0 0 Air"),)
    model = make_model(
        alicat_scanner=lambda port, baud: (
            calls.append((port, baud)) or discovered
        )
    )

    found = model.scan_alicats(" COM5 ", 19200)

    assert found == (
        AlicatScanRow("B", "B 14.7 22.5 0 0 Air"),
    )
    assert calls == [("COM5", 19200)]


def test_alicat_scan_marks_matching_profile_device_as_already_added() -> None:
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.example.toml"),
        alicat_scanner=lambda port, baud: (
            DiscoveredAlicat("A", "A 14.7 22.5 0 0 0 N2"),
        ),
    )

    found = model.scan_alicats("CHANGE_ME", 19200)

    assert found[0].configuration_status == "Already added"
    assert found[0].configured_device_id == "nitrogen_mfc"
    assert found[0].configured_kind == "Controller"


def test_alicat_poll_alone_does_not_authorize_controller_operation() -> None:
    captured: list[AlicatMfcConfiguration] = []

    def check(configuration: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        captured.append(configuration)
        return AlicatDiagnosticResult(
            "A 14.7 22.5 0.0 0.0 0.0 N2",
            AlicatInstrumentState(
                mass_flow=0.0,
                mass_flow_unit="sccm",
                volumetric_flow=0.0,
                volumetric_flow_unit="sccm",
                absolute_pressure=14.7,
                pressure_unit="psia",
                gas_temperature=22.5,
                temperature_unit="degC",
                setpoint=0.0,
                setpoint_unit="sccm",
                gas="N2",
            ),
        )

    model = make_model(alicat_checker=check)

    result = model.check_device("nitrogen_mfc")

    assert result.succeeded is False
    assert captured[0].unit_address == "A"
    assert "unverified" in result.technical_details
    rows = {row.device_id: row for row in model.device_rows()}
    assert rows["nitrogen_mfc"].readiness != "ready"


def test_keithley_check_uses_identification_diagnostic() -> None:
    captured: list[Keithley2260BConfiguration] = []

    def check(configuration: Keithley2260BConfiguration) -> KeithleyIdentity:
        captured.append(configuration)
        return KeithleyIdentity(
            manufacturer="Keithley Instruments",
            model="2260B-30-108",
            serial_number="1234567",
            firmware_version="1.00",
        )

    model = make_model(keithley_checker=check)

    result = model.check_device("main_power_supply")

    assert result.succeeded is True
    assert captured[0].connection.host == "CHANGE_ME"
    assert "2260B-30-108" in result.summary


def test_check_failure_is_persistent_and_contains_technical_details() -> None:
    def fail(_: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        raise TimeoutError("no response from address A")

    model = make_model(alicat_checker=fail)

    result = model.check_device("nitrogen_mfc")

    assert result.succeeded is False
    assert "TimeoutError: no response" in result.technical_details
    rows = {row.device_id: row for row in model.device_rows()}
    assert rows["nitrogen_mfc"].readiness == "check failed"


def test_simulated_device_does_not_call_hardware_checker() -> None:
    profile = load_rig_profile("rig-profile.example.toml")
    simulated_role = replace(
        profile.get_role("nitrogen_mfc"),
        backend=DeviceBackend.SIMULATED,
    )
    simulated_profile = replace(
        profile,
        device_roles=(simulated_role,),
    )
    model = DeviceSetupViewModel(simulated_profile)

    result = model.check_device("nitrogen_mfc")

    assert result.succeeded is True
    assert "simulated" in result.summary


def test_esp32_controller_readiness_uses_read_only_checker() -> None:
    calls = []
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.esp32.toml"),
        esp32_checker=lambda profile, device_id: (
            calls.append((profile.profile_id, device_id))
            or Esp32ReadinessResult(
                {"controller_id": "esp32_main_controller"},
                {"watchdog_tripped": False},
            )
        ),
    )

    result = model.check_device("esp32_main_controller")

    assert result.succeeded is True
    assert calls == [("esp32_hardware_poc", "esp32_main_controller")]


def test_esp32_sensor_readiness_reports_live_values() -> None:
    model = DeviceSetupViewModel(
        load_rig_profile("rig-profile.esp32.toml"),
        esp32_checker=lambda _profile, _device_id: Esp32ReadinessResult(
            {"controller_id": "esp32_main_controller"},
            {},
            (("temperature", 24.2, "degC"), ("humidity", 47.0, "%RH")),
        ),
    )

    result = model.check_device("esp32_dht11")

    assert result.succeeded is True
    assert "temperature=24.2 degC" in result.summary
    assert "humidity=47 %RH" in result.summary


def test_checked_alicat_is_saved_to_new_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def check(configuration: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        assert configuration.connection.port == "COM5"
        assert configuration.unit_address == "A"
        return AlicatDiagnosticResult(
            "A 14.7 22.5 0.0 0.0 0.0 N2",
            AlicatInstrumentState(
                mass_flow=0.0,
                mass_flow_unit="sccm",
                volumetric_flow=0.0,
                volumetric_flow_unit="sccm",
                absolute_pressure=14.7,
                pressure_unit="psia",
                gas_temperature=22.5,
                temperature_unit="degC",
                setpoint=0.0,
                setpoint_unit="sccm",
                gas="N2",
            ),
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        alicat_checker=check,
    )

    result = model.add_alicat_and_check(
        AddAlicatRequest(
            device_id="mfc_a",
            hardware_label="MFC A",
            purpose_label="Nitrogen",
            port="COM5",
            unit_address="a",
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("mfc_a")
    assert role.friendly_name == "MFC A"
    assert role.settings["purpose_label"] == "Nitrogen"
    assert role.settings["maximum_flow"] == 2000.0
    assert role.settings["flow_unit"] == "SCCM"
    assert role.poll_interval_seconds == 1.0
    assert role.connection_parameters["address"] == "A"
    assert saved.get_connection(role.connection_id).parameters["port"] == "COM5"


def test_failed_check_does_not_write_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def fail(_: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        raise TimeoutError("no response")

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        alicat_checker=fail,
    )

    result = model.add_alicat_and_check(
        AddAlicatRequest("mfc_a", "MFC A", "", "COM5", "A")
    )

    assert result.succeeded is False
    assert not profile_path.exists()


def test_duplicate_address_on_shared_bus_is_rejected_without_check() -> None:
    calls = 0

    def check(_: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        nonlocal calls
        calls += 1
        raise AssertionError("check should not run")

    model = make_model(alicat_checker=check)

    result = model.add_alicat_and_check(
        AddAlicatRequest("another_mfc", "MFC D", "", "CHANGE_ME", "A")
    )

    assert result.succeeded is False
    assert "already used" in result.technical_details
    assert calls == 0


def test_second_saved_device_reuses_same_bb3_connection(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def check(configuration: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        address = configuration.unit_address
        return AlicatDiagnosticResult(
            f"{address} 14.7 22.5 0 0 0 N2",
            AlicatInstrumentState(
                0, "sccm", 0, "sccm", 14.7, "psia", 22.5, "degC",
                0, "sccm", "N2",
            ),
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        alicat_checker=check,
    )
    first = model.add_alicat_and_check(
        AddAlicatRequest("mfc_a", "MFC A", "N2", "COM5", "A")
    )
    second = model.add_alicat_and_check(
        AddAlicatRequest("mfc_b", "MFC B", "CO2", "COM5", "B")
    )

    assert first.succeeded and second.succeeded
    saved = load_rig_profile(profile_path)
    assert len(saved.connections) == 1
    assert saved.get_role("mfc_a").connection_id == saved.get_role(
        "mfc_b"
    ).connection_id
    assert profile_path.with_suffix(".toml.bak").exists()


def test_identified_keithley_is_saved_to_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def identify(
        configuration: Keithley2260BConfiguration,
    ) -> KeithleyIdentity:
        assert configuration.connection.host == "192.168.1.29"
        assert configuration.connection.port == 2268
        return KeithleyIdentity(
            manufacturer="Keithley Instruments",
            model="2260B-30-108",
            serial_number="7654321",
            firmware_version="1.00",
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        keithley_checker=identify,
    )

    result = model.add_keithley_and_check(
        AddKeithleyRequest(
            device_id="main_power_supply",
            hardware_label="Main power supply",
            purpose_label="Electrolysis supply",
            host="192.168.1.29",
        )
    )

    assert result.succeeded is True
    assert "2260B-30-108" in result.summary
    saved = load_rig_profile(profile_path)
    role = saved.get_role("main_power_supply")
    connection = saved.get_connection(role.connection_id)
    assert role.driver == "keithley_2260b"
    assert role.settings["purpose_label"] == "Electrolysis supply"
    assert role.settings["maximum_voltage"] == 30.0
    assert role.settings["maximum_current"] == 108.0
    assert role.settings["maximum_power"] == 1080.0
    assert role.poll_interval_seconds == 0.1
    assert connection.parameters["host"] == "192.168.1.29"
    assert connection.parameters["port"] == 2268


def test_failed_keithley_identity_does_not_write_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def fail(_: Keithley2260BConfiguration) -> KeithleyIdentity:
        raise ConnectionError("no instrument at address")

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        keithley_checker=fail,
    )
    result = model.add_keithley_and_check(
        AddKeithleyRequest(
            "main_power_supply",
            "Main power supply",
            "",
            "192.168.1.29",
        )
    )

    assert result.succeeded is False
    assert "ConnectionError" in result.technical_details
    assert not profile_path.exists()


def test_read_only_alicat_meter_is_saved_without_setpoint_field(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def check(configuration: AlicatMfcConfiguration) -> AlicatDiagnosticResult:
        assert configuration.is_controller is False
        assert AlicatFrameField.SETPOINT not in configuration.frame_fields
        return AlicatDiagnosticResult(
            "B 14.7 22.5 1.8 1.9 Air",
            AlicatInstrumentState(
                1.9, "SLPM", 1.8, "LPM", 14.7, "psia", 22.5, "degC",
                0.0, "SLPM", "Air",
            ),
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        alicat_checker=check,
    )
    result = model.add_alicat_and_check(
        AddAlicatRequest(
            "flow_meter_b",
            "Flow meter B",
            "Outlet measurement",
            "COM5",
            "B",
            maximum_flow=2.0,
            device_kind="meter",
            flow_unit="SLPM",
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("flow_meter_b")
    assert role.capability is DeviceCapability.MASS_FLOW_METER
    assert role.settings["maximum_flow"] == 2.0
    assert role.settings["flow_unit"] == "SLPM"
    assert "setpoint" not in role.settings["frame_fields"]


def test_identified_visa_keithley_is_saved_to_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def identify(configuration: Keithley2260BConfiguration) -> KeithleyIdentity:
        assert configuration.connection.resource_name == "ASRL4::INSTR"
        return KeithleyIdentity(
            "Keithley Instruments", "2260B-30-108", "7654321", "1.00"
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        keithley_checker=identify,
    )
    result = model.add_keithley_and_check(
        AddKeithleyRequest(
            "main_power_supply",
            "Main power supply",
            "Electrolysis supply",
            "",
            connection_method="visa",
            resource_name="ASRL4::INSTR",
        )
    )

    assert result.succeeded is True
    assert "ASRL4::INSTR" in result.summary
    saved = load_rig_profile(profile_path)
    role = saved.get_role("main_power_supply")
    connection = saved.get_connection(role.connection_id)
    assert connection.connection_type == "visa_scpi"
    assert connection.parameters["resource_name"] == "ASRL4::INSTR"
    assert connection.parameters["baud_rate"] == 9600


def test_identified_keithley_2280s_is_saved_to_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def identify(configuration) -> KeithleyIdentity:
        assert isinstance(configuration, Keithley2280SConfiguration)
        assert configuration.connection.host == "192.168.1.30"
        assert configuration.limits.maximum_voltage == 32.0
        return KeithleyIdentity(
            "Keithley Instruments",
            "2280S-32-6",
            "7654321",
            "1.00",
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        keithley_checker=identify,
    )

    result = model.add_keithley_and_check(
        AddKeithleyRequest(
            "precision_supply",
            "Precision supply",
            "Electrolysis supply",
            "192.168.1.30",
            port=5025,
            maximum_voltage=32.0,
            maximum_current=6.0,
            maximum_power=192.0,
            driver="keithley_2280s",
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("precision_supply")
    connection = saved.get_connection(role.connection_id)
    assert role.driver == "keithley_2280s"
    assert role.expected_identity.model == "2280S-32-6"
    assert connection.parameters["host"] == "192.168.1.30"
    assert connection.parameters["port"] == 5025


def test_identified_ametek_asterion_is_saved_to_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def identify(configuration):
        assert isinstance(configuration, AmetekAsterionConfiguration)
        assert configuration.connection.resource_name == "USB0::ASTERION::INSTR"
        return KeithleyIdentity(
            "AMETEK",
            "ASTERION DC",
            "A12345",
            "1.00",
        )

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        keithley_checker=identify,
    )

    result = model.add_keithley_and_check(
        AddKeithleyRequest(
            "asterion_supply",
            "Asterion supply",
            "",
            "",
            connection_method="visa",
            resource_name="USB0::ASTERION::INSTR",
            maximum_voltage=30.0,
            maximum_current=5.0,
            maximum_power=100.0,
            driver="ametek_asterion",
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("asterion_supply")
    connection = saved.get_connection(role.connection_id)
    assert role.driver == "ametek_asterion"
    assert role.expected_identity.manufacturer == "AMETEK"
    assert connection.connection_type == "visa_scpi"
    assert connection.parameters["resource_name"] == "USB0::ASTERION::INSTR"


def test_duplicate_keithley_network_target_is_rejected_before_check() -> None:
    calls = 0

    def identify(_: Keithley2260BConfiguration) -> KeithleyIdentity:
        nonlocal calls
        calls += 1
        raise AssertionError("identity check should not run")

    model = make_model(keithley_checker=identify)
    result = model.add_keithley_and_check(
        AddKeithleyRequest(
            "second_supply",
            "Second supply",
            "",
            "CHANGE_ME",
            port=2268,
        )
    )

    assert result.succeeded is False
    assert "already used" in result.technical_details
    assert calls == 0


def _guardian_diagnostic() -> GuardianDiagnosticResult:
    return GuardianDiagnosticResult(
        identity=GuardianIdentity("e-G52HSRDA", "123456", "1.01"),
        mode=OperatingMode.IDLE,
        temperature=95.5,
        probe_temperature=None,
        stir_speed=499.0,
    )


def test_checked_guardian_is_saved_to_new_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def check(configuration: GuardianConfiguration) -> GuardianDiagnosticResult:
        assert configuration.port == "COM8"
        return _guardian_diagnostic()

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        guardian_checker=check,
    )

    result = model.add_guardian_and_check(
        AddGuardianRequest(
            device_id="hotplate",
            hardware_label="Guardian 5000",
            purpose_label="Electrolyte heating",
            port="COM8",
            maximum_temperature=300.0,
            maximum_speed=1500.0,
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("hotplate")
    assert role.friendly_name == "Guardian 5000"
    assert role.driver == "ohaus_guardian_5000"
    assert role.settings["purpose_label"] == "Electrolyte heating"
    assert role.settings["maximum_temperature"] == 300.0
    assert role.settings["maximum_speed"] == 1500.0
    assert role.poll_interval_seconds == 2.0
    assert saved.get_connection(role.connection_id).parameters["port"] == "COM8"


def test_failed_guardian_check_does_not_write_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def fail(_: GuardianConfiguration) -> GuardianDiagnosticResult:
        raise TimeoutError("no response")

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        guardian_checker=fail,
    )

    result = model.add_guardian_and_check(
        AddGuardianRequest("hotplate", "Guardian 5000", "", "COM8")
    )

    assert result.succeeded is False
    assert not profile_path.exists()


def test_duplicate_guardian_port_is_rejected_without_check(tmp_path) -> None:
    calls = 0

    def check(_: GuardianConfiguration) -> GuardianDiagnosticResult:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("check should not run again")
        return _guardian_diagnostic()

    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )
    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=tmp_path / "rig-profile.toml",
        guardian_checker=check,
    )
    first = model.add_guardian_and_check(
        AddGuardianRequest("hotplate", "Guardian 5000", "", "COM8")
    )
    assert first.succeeded is True

    result = model.add_guardian_and_check(
        AddGuardianRequest("hotplate_2", "Guardian 5000 B", "", "COM8")
    )

    assert result.succeeded is False
    assert "already used" in result.technical_details
    assert calls == 1


def _pump_diagnostic(**overrides) -> KamoerM1StpDiagnosticResult:
    values = dict(
        fault_status=0, running=False,
        direction=PumpDirection.FORWARD, speed_setpoint_rpm=0.0,
    )
    values.update(overrides)
    return KamoerM1StpDiagnosticResult(**values)


def test_checked_pump_is_saved_to_new_local_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def check(configuration: KamoerM1StpConfiguration) -> KamoerM1StpDiagnosticResult:
        assert configuration.port == "COM9"
        assert configuration.slave == 3
        return _pump_diagnostic(speed_setpoint_rpm=12.5)

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        kamoer_m1_stp_checker=check,
    )

    result = model.add_kamoer_m1_stp_and_check(
        AddKamoerM1StpRequest(
            device_id="pump1",
            hardware_label="Feed pump",
            purpose_label="Electrolyte dosing",
            port="COM9",
            slave=3,
            maximum_speed_rpm=200.0,
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("pump1")
    assert role.friendly_name == "Feed pump"
    assert role.driver == "kamoer_m1_stp"
    assert role.capability is DeviceCapability.PERISTALTIC_PUMP
    assert role.settings["purpose_label"] == "Electrolyte dosing"
    assert role.settings["slave"] == 3
    assert role.settings["maximum_speed_rpm"] == 200.0
    assert saved.get_connection(role.connection_id).parameters["port"] == "COM9"
    assert saved.get_connection(role.connection_id).parameters["baud_rate"] == 9600


def test_faulted_pump_check_does_not_write_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def faulted(_: KamoerM1StpConfiguration) -> KamoerM1StpDiagnosticResult:
        return _pump_diagnostic(fault_status=3)

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        kamoer_m1_stp_checker=faulted,
    )

    result = model.add_kamoer_m1_stp_and_check(
        AddKamoerM1StpRequest("pump1", "Feed pump", "", "COM9")
    )

    assert result.succeeded is False
    assert "fault" in result.technical_details.lower()
    assert not profile_path.exists()


def test_failed_pump_check_does_not_write_profile(tmp_path) -> None:
    profile_path = tmp_path / "rig-profile.toml"
    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )

    def fail(_: KamoerM1StpConfiguration) -> KamoerM1StpDiagnosticResult:
        raise TimeoutError("no response")

    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=profile_path,
        kamoer_m1_stp_checker=fail,
    )

    result = model.add_kamoer_m1_stp_and_check(
        AddKamoerM1StpRequest("pump1", "Feed pump", "", "COM9")
    )

    assert result.succeeded is False
    assert not profile_path.exists()


def test_duplicate_pump_port_is_rejected_without_check(tmp_path) -> None:
    calls = 0

    def check(_: KamoerM1StpConfiguration) -> KamoerM1StpDiagnosticResult:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("check should not run again")
        return _pump_diagnostic()

    empty_profile = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )
    model = DeviceSetupViewModel(
        empty_profile,
        profile_path=tmp_path / "rig-profile.toml",
        kamoer_m1_stp_checker=check,
    )
    first = model.add_kamoer_m1_stp_and_check(
        AddKamoerM1StpRequest("pump1", "Feed pump", "", "COM9")
    )
    assert first.succeeded is True

    result = model.add_kamoer_m1_stp_and_check(
        AddKamoerM1StpRequest("pump2", "Feed pump 2", "", "COM9")
    )

    assert result.succeeded is False
    assert "already used" in result.technical_details
    assert calls == 1
