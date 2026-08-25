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


class ScanTransport(SerialTextTransport):
    def __init__(self) -> None:
        self._open = False
        self.requests = []

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def request(self, message: str) -> str:
        self.requests.append(message)
        if message == "A":
            return "A 14.7 22.5 0 0 0 Air"
        if message == "B":
            return "B 14.7 22.5 0 0 Air"
        if message == "A??M*":
            return "A Alicat Scientific MC-2SLPM-D SN123"
        if message == "A??D*":
            return "A pressure temperature volumetric_flow mass_flow setpoint gas"
        if message == "AVE":
            return "A 8.1.0"
        if message == "B??M*":
            return "B Alicat Scientific M-500SCCM-D SN456"
        if message == "B??D*":
            return "B pressure temperature volumetric_flow mass_flow gas"
        if message == "BVE":
            return "B 8.1.0"
        raise TimeoutError("no device")


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


def test_read_only_diagnostic_sends_one_address_poll() -> None:
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

    assert transport.requests == ("A",)
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
    assert found[1].raw_response == "B 14.7 22.5 0 0 Air"
    assert found[0].inferred_kind == "controller"
    assert found[0].inferred_maximum_flow_sccm == 2000.0
    assert found[1].inferred_kind == "meter"
    assert found[1].inferred_maximum_flow_sccm == 500.0
    assert transport.requests == [
        *[chr(code) for code in range(ord("A"), ord("Z") + 1)],
        "A??M*", "A??D*", "AVE",
        "B??M*", "B??D*", "BVE",
    ]
    assert transport.is_open is False


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
