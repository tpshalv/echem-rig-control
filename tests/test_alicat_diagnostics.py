from pathlib import Path

import pytest

import rig_control.diagnostics.alicat as diagnostic
from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    configuration_from_profile,
)
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.transports.simulated_serial_text import (
    SimulatedSerialTextTransport,
)
from rig_control.transports.serial_text import SerialTextTransport

from captures import load_capture


#: Replayed from tests/captures/, which holds what these instruments really
#: answered. Keeping the captures in files rather than inline string literals
#: means a capture cannot quietly drift away from the parser it documents.
MASS_FLOW_CAPTURE = load_capture("alicat-mc-2slpm-d-10v22-mass-flow.txt")
BACK_PRESSURE_CAPTURE = load_capture("alicat-mc-2slpm-d-10v22-back-pressure.txt")


def replies(address: str, capture: dict[str, str] | None = None) -> dict[str, str]:
    """Re-address one capture so it can answer at any polling address."""

    source = capture if capture is not None else MASS_FLOW_CAPTURE
    captured_address = next(iter(source))[0]
    return {
        address + command[1:]: reply.replace(captured_address, address)
        for command, reply in source.items()
    }


def bpr_replies(address: str) -> dict[str, str]:
    return replies(address, BACK_PRESSURE_CAPTURE)


class ScanTransport(SerialTextTransport):
    """Answers as the two real instruments on the bench bus do."""

    def __init__(self) -> None:
        self._open = False
        self.requests = []
        self.replies = {**replies("A"), **replies("B")}

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def request(self, message: str) -> str:
        self.requests.append(message)
        try:
            return self.replies[message]
        except KeyError:
            raise TimeoutError("no device") from None


def write_configuration(path: Path, port: str = "COM5") -> Path:
    profile_path = path / "rig-profile.toml"
    profile_path.write_text(
        f"""
[profile]
profile_id = "test_rig"
friendly_name = "Test rig"

[[connections]]
connection_id = "alicat_bus"
connection_type = "serial_text"

[connections.parameters]
port = "{port}"
baud_rate = 19200
timeout_seconds = 1.0

[[devices]]
device_id = "mfc_a"
friendly_name = "MFC A"
capability = "mass_flow_controller"
driver = "alicat"
backend = "real"
enabled = true
connection_id = "alicat_bus"

[devices.connection]
address = "A"

[devices.settings]
maximum_flow = 200.0
flow_unit = "sccm"
volumetric_flow_unit = "sccm"
pressure_unit = "psia"
temperature_unit = "degC"
frame_fields = "absolute_pressure,gas_temperature,volumetric_flow,mass_flow,setpoint,gas"
""",
        encoding="utf-8",
    )
    return profile_path


def load_configuration(path: Path) -> AlicatMfcConfiguration:
    return configuration_from_profile(
        load_rig_profile(path),
        "mfc_a",
    )


def test_read_only_diagnostic_polls_and_attempts_configuration_verification() -> None:
    configuration = configuration_from_profile(
        load_rig_profile("rig-profile.example.toml"),
        "nitrogen_mfc",
    )
    transport = SimulatedSerialTextTransport()
    transport.queue_response(
        "A",
        "A 14.7 22.5 11.8 12.3 15.0 N2 LCK",
    )

    result = diagnostic.read_alicat_state(configuration, transport)

    assert transport.requests == ("A", "AVE")
    assert result.control_configuration is None
    assert result.verification_error
    assert transport.is_open is False
    assert result.raw_response.endswith("N2 LCK")
    assert result.state.mass_flow == 12.3
    assert result.state.gas == "N2"
    assert result.state.status_codes == ("LCK",)


def test_transport_closes_after_poll_failure() -> None:
    configuration = configuration_from_profile(
        load_rig_profile("rig-profile.example.toml"),
        "nitrogen_mfc",
    )
    transport = SimulatedSerialTextTransport()
    transport.queue_error("A", TimeoutError("simulated timeout"))

    with pytest.raises(RuntimeError, match="simulated timeout"):
        diagnostic.read_alicat_state(configuration, transport)

    assert transport.is_open is False


def test_bus_scan_finds_addressed_devices_without_sending_commands() -> None:
    transport = ScanTransport()

    found = diagnostic.scan_alicat_bus("COM5", transport=transport)

    assert [device.address for device in found] == ["A", "B"]
    assert found[0].raw_response.endswith("CO2")
    assert transport.requests == [
        *[chr(code) for code in range(ord("A"), ord("Z") + 1)],
        "A??M*", "A??D*", "AVE", "ALR", "AR20",
        "B??M*", "B??D*", "BVE", "BLR", "BR20",
    ]
    assert transport.is_open is False


def test_real_mass_flow_replies_are_detected_as_a_forward_controller() -> None:
    found = diagnostic.scan_alicat_bus("COM5", transport=ScanTransport())[0]

    assert found.detected_role == "mfc"
    assert found.is_controller is True
    assert found.setpoint_unit == "SLPM"
    assert (found.minimum_setpoint, found.maximum_setpoint) == (0.0, 2.0)
    assert found.model == "MC-2SLPM-D"
    assert found.serial_number == "539144"
    assert found.usable


def test_real_data_frame_gives_the_column_order_and_units() -> None:
    from rig_control.devices.alicat.protocol_fields import AlicatFrameField

    found = diagnostic.scan_alicat_bus("COM5", transport=ScanTransport())[0]

    # The totaliser column sits between setpoint and gas on this instrument.
    # Assuming the documented default order would have read the totaliser
    # value as the gas name.
    assert found.frame_fields == (
        AlicatFrameField.ABSOLUTE_PRESSURE,
        AlicatFrameField.GAS_TEMPERATURE,
        AlicatFrameField.VOLUMETRIC_FLOW,
        AlicatFrameField.MASS_FLOW,
        AlicatFrameField.SETPOINT,
        AlicatFrameField.TOTALIZED_FLOW,
        AlicatFrameField.GAS,
    )
    assert found.unit_for(AlicatFrameField.ABSOLUTE_PRESSURE) == "PSIA"
    assert found.unit_for(AlicatFrameField.GAS_TEMPERATURE) == "degC"
    assert found.unit_for(AlicatFrameField.VOLUMETRIC_FLOW) == "LPM"
    assert found.unit_for(AlicatFrameField.MASS_FLOW) == "SLPM"
    assert found.unit_for(AlicatFrameField.TOTALIZED_FLOW) == "SL"
    assert found.unit_for(AlicatFrameField.GAS) is None


def test_real_status_frame_decodes_against_the_reported_layout() -> None:
    from rig_control.devices.alicat.bus import AlicatBus
    from rig_control.devices.alicat.protocol import (
        AlicatAsciiProtocolClient,
        AlicatEngineeringUnits,
    )

    found = diagnostic.scan_alicat_bus("COM5", transport=ScanTransport())[0]
    protocol = AlicatAsciiProtocolClient(
        AlicatBus("bus", SimulatedSerialTextTransport()),
        found.frame_fields,
        AlicatEngineeringUnits("SLPM", "LPM", "PSIA", "degC", "SLPM", "SL"),
    )

    state = protocol.parse_state("A", found.raw_response)

    assert state.absolute_pressure == 14.81
    assert state.gas_temperature == 21.13
    assert state.mass_flow == 0.0
    assert state.totalized_flow == 0.0
    assert state.gas == "CO2"
    assert state.status_codes == ()


def test_unreadable_controller_is_never_assumed_to_be_a_meter() -> None:
    class SilentConfigurationTransport(ScanTransport):
        def request(self, message: str) -> str:
            if message in {"ALR", "AR20"}:
                self.requests.append(message)
                raise TimeoutError("no configuration reply")
            return super().request(message)

    found = diagnostic.scan_alicat_bus(
        "COM5", transport=SilentConfigurationTransport()
    )

    unreadable = found[0]
    assert unreadable.address == "A"
    assert unreadable.detected_role is None
    assert unreadable.is_controller is None
    assert unreadable.usable is False
    assert "could not be read" in unreadable.configuration_error


def test_meter_without_a_setpoint_column_is_recognised_as_a_meter() -> None:
    class MeterTransport(ScanTransport):
        def __init__(self) -> None:
            super().__init__()
            frame = self.replies["A??D*"]
            setpoint_line = next(
                line for line in frame.split("\r") if "Setpt" in line
            )
            self.replies["A??D*"] = frame.replace(setpoint_line + "\r", "")

        def request(self, message: str) -> str:
            if message in {"ALR", "AR20"}:
                self.requests.append(message)
                raise TimeoutError("a meter has no control loop")
            return super().request(message)

    found = diagnostic.scan_alicat_bus("COM5", transport=MeterTransport())[0]

    assert found.is_controller is False
    assert found.detected_role is None
    assert found.usable


def test_back_pressure_configuration_is_detected_from_loop_and_register() -> None:
    class BprTransport(ScanTransport):
        def __init__(self) -> None:
            super().__init__()
            # Absolute-pressure control with the inverse bit set: 9239 + 32768.
            self.replies["ALR"] = "A 34 +0.0000 +160.000 10 PSIA"
            self.replies["AR20"] = "A   020 = 42007"

    found = diagnostic.scan_alicat_bus("COM5", transport=BprTransport())[0]

    assert found.detected_role == "bpr"
    assert found.setpoint_unit == "PSIA"
    assert found.maximum_setpoint == 160.0


def test_identity_query_failure_does_not_prevent_role_detection() -> None:
    class NoIdentityTransport(ScanTransport):
        def request(self, message: str) -> str:
            if message == "A??M*":
                self.requests.append(message)
                raise TimeoutError("no manufacturer reply")
            return super().request(message)

    found = diagnostic.scan_alicat_bus("COM5", transport=NoIdentityTransport())

    assert found[0].model is None
    assert found[0].serial_number is None
    assert found[0].detected_role == "mfc"


def test_placeholder_port_is_rejected_before_opening() -> None:
    configuration = configuration_from_profile(
        load_rig_profile("rig-profile.example.toml"),
        "nitrogen_mfc",
    )

    with pytest.raises(ValueError, match="COM port has not been configured"):
        diagnostic.read_alicat_state(configuration)


def test_command_line_success_report_is_informative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_configuration(tmp_path)
    configuration = load_configuration(path)
    transport = SimulatedSerialTextTransport()
    response = "A 14.7 22.5 11.8 12.3 15.0 N2 MOV"
    transport.queue_response("A", response)
    expected = diagnostic.read_alicat_state(configuration, transport)
    monkeypatch.setattr(
        diagnostic,
        "read_alicat_state",
        lambda _: expected,
    )

    exit_code = diagnostic.main(
        ["mfc_a", "--configuration", str(path)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "DIAGNOSTIC PASSED" in output
    assert f"Raw response: {response}" in output
    assert "Mass flow: 12.3 sccm" in output
    assert "Selected gas/calibration: N2" in output
    assert "Status codes: MOV" in output
    assert "Measurement quality: bad" in output
    assert "No setpoint or gas-selection command" in output


def test_command_line_failure_report_is_informative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_configuration(tmp_path)

    def fail(_: AlicatMfcConfiguration) -> diagnostic.AlicatDiagnosticResult:
        raise ConnectionError("simulated COM failure")

    monkeypatch.setattr(diagnostic, "read_alicat_state", fail)

    exit_code = diagnostic.main(
        ["mfc_a", "--configuration", str(path)]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "DIAGNOSTIC FAILED" in output
    assert "ConnectionError" in output
    assert "simulated COM failure" in output
    assert "No setpoint or gas-selection command" in output


def test_gauge_or_differential_frame_columns_are_refused_with_a_clear_error() -> None:
    class GaugeTransport(ScanTransport):
        def __init__(self) -> None:
            super().__init__()
            self.replies["A??D*"] = self.replies["A??D*"].replace(
                "Abs Press", "Gauge Press"
            )

    found = diagnostic.scan_alicat_bus("COM5", transport=GaugeTransport())

    assert found[0].usable is False
    assert "gauge or differential" in found[0].configuration_error


def test_unrecognised_frame_description_falls_back_to_the_documented_order() -> None:
    from rig_control.devices.alicat.protocol_fields import AlicatFrameField

    class OddFrameTransport(ScanTransport):
        def __init__(self) -> None:
            super().__init__()
            self.replies["A??D*"] = "A ?"

    found = diagnostic.scan_alicat_bus("COM5", transport=OddFrameTransport())

    assert found[0].detected_role == "mfc"
    assert found[0].frame_fields == (
        AlicatFrameField.ABSOLUTE_PRESSURE,
        AlicatFrameField.GAS_TEMPERATURE,
        AlicatFrameField.VOLUMETRIC_FLOW,
        AlicatFrameField.MASS_FLOW,
        AlicatFrameField.SETPOINT,
        AlicatFrameField.GAS,
    )


def test_adding_a_real_instrument_saves_its_reported_frame_and_units(tmp_path) -> None:
    """The whole add path, driven by one instrument's actual replies."""

    from dataclasses import replace

    from rig_control.ui.device_setup.model import DeviceSetupViewModel
    from rig_control.ui.device_setup.types import AddAlicatRequest

    transport = ScanTransport()
    empty = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )
    model = DeviceSetupViewModel(
        empty,
        profile_path=tmp_path / "rig-profile.toml",
        alicat_probe=lambda port, address, baud=19200: diagnostic.probe_alicat_address(
            port, address, baud, transport=transport
        ),
        alicat_checker=lambda configuration: diagnostic.read_alicat_state(
            configuration, transport
        ),
    )

    result = model.add_alicat_and_check(
        AddAlicatRequest("mfc_a", "MFC A", "CO2", "COM5", "A")
    )

    assert result.succeeded is True, result.technical_details
    assert "Mass-flow controller" in result.summary

    role = load_rig_profile(tmp_path / "rig-profile.toml").get_role("mfc_a")
    assert role.capability.value == "mass_flow_controller"
    assert role.settings["frame_fields"] == (
        "absolute_pressure,gas_temperature,volumetric_flow,mass_flow,"
        "setpoint,totalized_flow,gas"
    )
    assert role.settings["flow_unit"] == "SLPM"
    assert role.settings["maximum_flow"] == 2.0
    assert role.settings["pressure_unit"] == "PSIA"
    assert role.settings["totalized_flow_unit"] == "SL"
    assert role.expected_identity.serial_number == "539144"

    # The saved device reconnects and checks out with no further steps.
    check = model.check_device("mfc_a")
    assert check.succeeded is True, check.technical_details
    assert "mass flow; forward; SLPM" in check.summary


def test_a_forward_mass_flow_controller_is_never_saved_as_a_back_pressure_one(
    tmp_path,
) -> None:
    """A unit that has not been configured for pressure control is refused."""

    from dataclasses import replace

    from rig_control.ui.device_setup.model import DeviceSetupViewModel
    from rig_control.ui.device_setup.types import AddAlicatRequest

    transport = ScanTransport()
    empty = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )
    model = DeviceSetupViewModel(
        empty,
        profile_path=tmp_path / "rig-profile.toml",
        alicat_probe=lambda port, address, baud=19200: diagnostic.probe_alicat_address(
            port, address, baud, transport=transport
        ),
        alicat_checker=lambda configuration: diagnostic.read_alicat_state(
            configuration, transport
        ),
    )

    result = model.add_alicat_and_check(
        AddAlicatRequest(
            "outlet_bpr", "Outlet BPR", "", "COM5", "B",
            downstream_valve_acknowledged=True,
        )
    )

    # Acknowledging the warning cannot turn a forward mass-flow controller
    # into a back-pressure controller.
    assert result.succeeded is True
    role = load_rig_profile(tmp_path / "rig-profile.toml").get_role("outlet_bpr")
    assert role.capability.value == "mass_flow_controller"
    assert "Mass-flow controller" in result.summary


class BenchTransport(ScanTransport):
    """The real bench bus: an MFC at A and, after reconfiguration, a BPR at C."""

    def __init__(self) -> None:
        super().__init__()
        self.replies = {**replies("A"), **bpr_replies("C")}


def test_reconfigured_unit_is_detected_as_a_back_pressure_controller() -> None:
    found = {
        device.address: device
        for device in diagnostic.scan_alicat_bus("COM5", transport=BenchTransport())
    }

    assert found["A"].detected_role == "mfc"
    assert found["C"].detected_role == "bpr"
    assert found["C"].setpoint_unit == "PSIA"
    assert (found["C"].minimum_setpoint, found["C"].maximum_setpoint) == (0.0, 160.0)
    assert found["C"].serial_number == "539145"


def test_pressure_setpoint_column_is_not_mistaken_for_the_pressure_reading() -> None:
    from rig_control.devices.alicat.protocol_fields import AlicatFrameField

    found = diagnostic.scan_alicat_bus("COM5", transport=BenchTransport())[1]

    # The frame names both "Abs Press" and "Abs Press Setpt"; they are
    # different columns and only one of them is the measurement.
    assert found.frame_fields == (
        AlicatFrameField.ABSOLUTE_PRESSURE,
        AlicatFrameField.GAS_TEMPERATURE,
        AlicatFrameField.VOLUMETRIC_FLOW,
        AlicatFrameField.MASS_FLOW,
        AlicatFrameField.SETPOINT,
        AlicatFrameField.TOTALIZED_FLOW,
        AlicatFrameField.GAS,
    )
    assert found.unit_for(AlicatFrameField.SETPOINT) == "PSIA"
    assert found.unit_for(AlicatFrameField.ABSOLUTE_PRESSURE) == "PSIA"


def add_bench_bpr(tmp_path, transport):
    from dataclasses import replace

    from rig_control.ui.device_setup.model import DeviceSetupViewModel
    from rig_control.ui.device_setup.types import AddAlicatRequest

    empty = replace(
        load_rig_profile("rig-profile.example.toml"),
        connections=(),
        device_roles=(),
    )
    model = DeviceSetupViewModel(
        empty,
        profile_path=tmp_path / "rig-profile.toml",
        alicat_probe=lambda port, address, baud=19200: diagnostic.probe_alicat_address(
            port, address, baud, transport=transport
        ),
        alicat_checker=lambda configuration: diagnostic.read_alicat_state(
            configuration, transport
        ),
    )
    request = AddAlicatRequest("outlet_bpr", "Outlet BPR", "", "COM5", "C")
    return model, request


def test_adding_the_real_bpr_asks_once_and_then_saves_it_ready(tmp_path) -> None:
    from dataclasses import replace

    model, request = add_bench_bpr(tmp_path, BenchTransport())

    first = model.add_alicat_and_check(request)
    assert first.succeeded is False
    assert "valve is downstream of the sensing section" in first.requires_acknowledgement
    assert not (tmp_path / "rig-profile.toml").exists()

    result = model.add_alicat_and_check(
        replace(request, downstream_valve_acknowledged=True)
    )

    assert result.succeeded is True, result.technical_details
    assert "Back-pressure controller" in result.summary

    role = load_rig_profile(tmp_path / "rig-profile.toml").get_role("outlet_bpr")
    assert role.capability.value == "back_pressure_controller"
    assert role.settings["pressure_unit"] == "PSIA"
    assert role.settings["downstream_valve_confirmed"] is True
    assert role.settings["maximum_pressure_bara"] == 2.5
    assert role.settings["frame_fields"] == (
        "absolute_pressure,gas_temperature,volumetric_flow,mass_flow,"
        "setpoint,totalized_flow,gas"
    )

    # Reconnecting re-reads the live mode and needs no further confirmation.
    check = model.check_device("outlet_bpr")
    assert check.succeeded is True, check.technical_details
    assert "absolute pressure; inverse; PSIA" in check.summary


def test_saved_bpr_still_enforces_the_software_pressure_ceiling(tmp_path) -> None:
    from dataclasses import replace

    from rig_control.devices.alicat.bus import AlicatBus
    from rig_control.devices.alicat.configuration import configuration_from_profile
    from rig_control.devices.alicat.pressure import AlicatBackPressureController
    from rig_control.devices.alicat.protocol import AlicatAsciiProtocolClient

    transport = BenchTransport()
    model, request = add_bench_bpr(tmp_path, transport)
    saved = model.add_alicat_and_check(
        replace(request, downstream_valve_acknowledged=True)
    )
    assert saved.succeeded is True, saved.technical_details

    configuration = configuration_from_profile(model.profile, "outlet_bpr")
    controller = AlicatBackPressureController(
        configuration,
        AlicatAsciiProtocolClient(
            AlicatBus("bench", transport),
            configuration.frame_fields,
            configuration.engineering_units,
        ),
    )
    controller.connect()

    assert controller.control_ready is True
    # The instrument's own range is 160 PSIA (about 11 bara). The rig's
    # ordinary ceiling still applies.
    assert controller.maximum_pressure_pa == 250_000
    with pytest.raises(ValueError, match="at most"):
        controller.set_pressure_setpoint(3.0)
    assert not any(
        request.startswith("CLS ") for request in transport.requests
    )

    # The expected values come from the capture itself, so they cannot drift
    # apart from the file that documents this instrument.
    pressure_psia, temperature = (
        float(token) for token in BACK_PRESSURE_CAPTURE["C"].split()[1:3]
    )
    readings = {item.channel: item.measurement for item in controller.read_measurements()}
    assert readings["absolute_pressure"].unit == "bara"
    assert readings["absolute_pressure"].value == pytest.approx(
        pressure_psia * 6894.757293168 / 100_000
    )
    assert readings["mass_flow"].unit == "SLPM"
    assert readings["gas_temperature"].value == temperature
    assert "gauge_pressure" not in readings
