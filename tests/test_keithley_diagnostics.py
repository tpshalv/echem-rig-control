from pathlib import Path

import pytest

import rig_control.diagnostics.keithley as diagnostic
from rig_control.configuration import (
    Keithley2260BConfiguration,
    SocketScpiConfiguration,
)
from rig_control.devices.keithley_2260b.protocol import KeithleyIdentity
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.transports.scpi import ScpiTransport


class DiagnosticTransport(ScpiTransport):
    """Fake instrument connection for diagnostic tests."""

    def __init__(
        self,
        response: str = (
            "Keithley Instruments,2260B-30-108,1234567,1.00"
        ),
        error: Exception | None = None,
    ) -> None:
        self._is_open = False
        self.response = response
        self.error = error
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.was_closed = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False
        self.was_closed = True

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query(self, command: str) -> str:
        self.queries.append(command)

        if self.error is not None:
            raise self.error

        return self.response


def make_configuration(
    host: str = "192.168.1.50",
) -> Keithley2260BConfiguration:
    return Keithley2260BConfiguration(
        device_id="main_power_supply",
        connection=SocketScpiConfiguration(
            host=host,
            port=2268,
            timeout_seconds=5.0,
        ),
        limits=PowerSupplyLimits(
            maximum_voltage=30.0,
            maximum_current=108.0,
            maximum_power=1080.0,
        ),
    )


def write_configuration(path: Path) -> Path:
    configuration_path = path / "rig-profile.toml"
    configuration_path.write_text(
        """
[profile]
profile_id = "test_rig"
friendly_name = "Test rig"

[[connections]]
connection_id = "keithley_ethernet"
connection_type = "socket_scpi"

[connections.parameters]
host = "192.168.1.50"
port = 2268
timeout_seconds = 5.0

[[devices]]
device_id = "main_power_supply"
friendly_name = "Main power supply"
capability = "dc_power_supply"
driver = "keithley_2260b"
backend = "real"
required = true
enabled = true
connection_id = "keithley_ethernet"

[devices.settings]
maximum_voltage = 30.0
maximum_current = 108.0
maximum_power = 1080.0
""",
        encoding="utf-8",
    )
    return configuration_path

def test_diagnostic_sends_identification_query_only() -> None:
    transport = DiagnosticTransport()

    identity = diagnostic.identify_keithley(
        make_configuration(),
        transport,
    )

    assert identity.model == "2260B-30-108"
    assert transport.queries == ["*IDN?"]
    assert transport.writes == []
    assert transport.was_closed is True
    assert transport.is_open is False


def test_diagnostic_rejects_wrong_instrument() -> None:
    transport = DiagnosticTransport(
        response="Example Instruments,OTHER-DEVICE,1234,1.0"
    )

    with pytest.raises(
        RuntimeError,
        match="did not identify as a Keithley 2260B",
    ):
        diagnostic.identify_keithley(
            make_configuration(),
            transport,
        )

    assert transport.was_closed is True


def test_diagnostic_closes_transport_after_invalid_response() -> None:
    transport = DiagnosticTransport(
        response="invalid response"
    )

    with pytest.raises(
        ValueError,
        match="Invalid Keithley identity response",
    ):
        diagnostic.identify_keithley(
            make_configuration(),
            transport,
        )

    assert transport.was_closed is True


def test_diagnostic_closes_transport_after_query_error() -> None:
    transport = DiagnosticTransport(
        error=TimeoutError("simulated timeout")
    )

    with pytest.raises(
        TimeoutError,
        match="simulated timeout",
    ):
        diagnostic.identify_keithley(
            make_configuration(),
            transport,
        )

    assert transport.was_closed is True


def test_placeholder_address_is_rejected_before_connection() -> None:
    with pytest.raises(
        ValueError,
        match="IP address has not been configured",
    ):
        diagnostic.identify_keithley(
            make_configuration(host="CHANGE_ME")
        )


def test_command_line_success_report_is_informative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_configuration(tmp_path)

    identity = KeithleyIdentity(
        manufacturer="Keithley Instruments",
        model="2260B-30-108",
        serial_number="1234567",
        firmware_version="1.00",
    )

    monkeypatch.setattr(
        diagnostic,
        "identify_keithley",
        lambda configuration: identity,
    )

    result = diagnostic.main([str(path)])
    output = capsys.readouterr().out

    assert result == 0
    assert "DIAGNOSTIC PASSED" in output
    assert "2260B-30-108" in output
    assert "1234567" in output
    assert "No output-enable" in output


def test_command_line_failure_report_is_informative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_configuration(tmp_path)

    def raise_connection_error(
        configuration: Keithley2260BConfiguration,
    ) -> KeithleyIdentity:
        raise ConnectionError("simulated connection failure")

    monkeypatch.setattr(
        diagnostic,
        "identify_keithley",
        raise_connection_error,
    )

    result = diagnostic.main([str(path)])
    output = capsys.readouterr().out

    assert result == 1
    assert "DIAGNOSTIC FAILED" in output
    assert "ConnectionError" in output
    assert "simulated connection failure" in output
    assert "No output-enable" in output