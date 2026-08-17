from dataclasses import replace

from rig_control.devices.alicat.configuration import AlicatMfcConfiguration
from rig_control.devices.alicat.protocol import AlicatInstrumentState
from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
)
from rig_control.devices.keithley_2260b.protocol import KeithleyIdentity
from rig_control.diagnostics.alicat import AlicatDiagnosticResult
from rig_control.rig_profile import DeviceBackend
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.ui.device_setup.model import (
    AddAlicatRequest,
    AddKeithleyRequest,
    DeviceSetupViewModel,
    SerialPortInfo,
)


def make_model(
    *,
    serial_ports: tuple[SerialPortInfo, ...] = (),
    alicat_checker=None,
    keithley_checker=None,
) -> DeviceSetupViewModel:
    arguments = {
        "serial_port_provider": lambda: serial_ports,
    }
    if alicat_checker is not None:
        arguments["alicat_checker"] = alicat_checker
    if keithley_checker is not None:
        arguments["keithley_checker"] = keithley_checker
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
    assert rows["main_power_supply"].connection == "CHANGE_ME:2268"
    assert rows["main_power_supply"].readiness == "not checked"


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


def test_alicat_check_uses_read_only_diagnostic_and_updates_readiness() -> None:
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

    assert result.succeeded is True
    assert captured[0].unit_address == "A"
    assert "mass flow 0 sccm" in result.summary
    assert "Raw response" in result.technical_details
    rows = {row.device_id: row for row in model.device_rows()}
    assert rows["nitrogen_mfc"].readiness == "ready"


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
    assert role.settings["maximum_flow"] == 200.0
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
