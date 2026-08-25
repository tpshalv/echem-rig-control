import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass

from rig_control.devices.alicat.bus import AlicatBus
from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    configuration_from_profile,
)
from rig_control.devices.alicat.protocol import (
    AlicatAsciiProtocolClient,
    AlicatInstrumentState,
)
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.transports.pyserial_text import PySerialTextTransport
from rig_control.transports.serial_text import SerialTextTransport


@dataclass(frozen=True, slots=True)
class AlicatDiagnosticResult:
    """Raw and decoded results from one read-only poll."""

    raw_response: str
    state: AlicatInstrumentState


@dataclass(frozen=True, slots=True)
class DiscoveredAlicat:
    """One address that returned a valid addressed frame during a scan."""

    address: str
    raw_response: str
    manufacturer_response: str = ""
    data_format_response: str = ""
    firmware_response: str = ""
    model: str | None = None
    inferred_kind: str | None = None
    inferred_maximum_flow_sccm: float | None = None


def scan_alicat_bus(
    port: str,
    baud_rate: int = 19200,
    *,
    timeout_seconds: float = 0.2,
    transport: SerialTextTransport | None = None,
) -> tuple[DiscoveredAlicat, ...]:
    """Read-only scan of polling addresses A-Z on one Alicat bus."""

    selected_transport = transport or PySerialTextTransport(
        port=port,
        baud_rate=baud_rate,
        timeout_seconds=timeout_seconds,
    )
    found: list[DiscoveredAlicat] = []
    try:
        selected_transport.open()
        for code in range(ord("A"), ord("Z") + 1):
            address = chr(code)
            try:
                response = selected_transport.request(address)
            except TimeoutError:
                continue
            if response.split(maxsplit=1)[0].upper() != address:
                continue
            found.append(DiscoveredAlicat(address, response))
        enriched: list[DiscoveredAlicat] = []
        for device in found:
            manufacturer = _optional_query(
                selected_transport, f"{device.address}??M*"
            )
            data_format = _optional_query(
                selected_transport, f"{device.address}??D*"
            )
            firmware = _optional_query(
                selected_transport, f"{device.address}VE"
            )
            model, kind, maximum = infer_alicat_identity(
                manufacturer,
                data_format,
            )
            enriched.append(
                DiscoveredAlicat(
                    device.address,
                    device.raw_response,
                    manufacturer,
                    data_format,
                    firmware,
                    model,
                    kind,
                    maximum,
                )
            )
    finally:
        selected_transport.close()
    return tuple(enriched)


def _optional_query(transport: SerialTextTransport, command: str) -> str:
    try:
        return transport.request(command)
    except (TimeoutError, OSError, RuntimeError):
        return ""


def infer_alicat_identity(
    manufacturer_response: str,
    data_format_response: str = "",
) -> tuple[str | None, str | None, float | None]:
    """Conservatively infer model, function and canonical SCCM range."""

    match = re.search(
        r"\b(MC|M)\s*-\s*(\d+(?:\.\d+)?)\s*(SCCM|SLPM)\b",
        manufacturer_response,
        flags=re.IGNORECASE,
    )
    if match is None:
        kind = (
            "controller"
            if re.search(r"\bset\s*point\b|\bsetpoint\b", data_format_response, re.I)
            else None
        )
        return None, kind, None
    prefix, number, unit = match.groups()
    model = match.group(0).replace(" ", "").upper()
    maximum = float(number) * (1000.0 if unit.upper() == "SLPM" else 1.0)
    return model, ("controller" if prefix.upper() == "MC" else "meter"), maximum


def read_alicat_state(
    configuration: AlicatMfcConfiguration,
    transport: SerialTextTransport | None = None,
) -> AlicatDiagnosticResult:
    """Open, poll once, decode, and close without changing the MFC."""

    connection = configuration.connection
    if transport is None:
        if connection.port.strip().upper() == "CHANGE_ME":
            raise ValueError(
                "The Alicat COM port has not been configured. Copy "
                "rig-profile.example.toml to rig-profile.toml and replace "
                "CHANGE_ME with the BB3 Windows COM port."
            )
        transport = PySerialTextTransport(
            port=connection.port,
            baud_rate=connection.baud_rate,
            timeout_seconds=connection.timeout_seconds,
        )

    bus = AlicatBus(connection.connection_id, transport)
    protocol = AlicatAsciiProtocolClient(
        bus,
        configuration.frame_fields,
        configuration.engineering_units,
        requires_setpoint=configuration.is_controller,
    )

    try:
        bus.connect()
        raw_response = bus.request(configuration.unit_address)
        state = protocol.parse_state(
            configuration.unit_address,
            raw_response,
        )
    finally:
        bus.disconnect()

    return AlicatDiagnosticResult(raw_response, state)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one deliberately read-only Alicat connection diagnostic."""

    parser = argparse.ArgumentParser(
        description=(
            "Poll one configured Alicat MFC and display its raw and "
            "decoded state without sending a setpoint."
        )
    )
    parser.add_argument(
        "device_id",
        help="Configured MFC device ID, for example nitrogen_mfc",
    )
    parser.add_argument(
        "--configuration",
        default="rig-profile.toml",
        help="Rig profile path (default: rig-profile.toml)",
    )
    parsed = parser.parse_args(arguments)

    try:
        profile = load_rig_profile(parsed.configuration)
        configuration = configuration_from_profile(
            profile,
            parsed.device_id,
        )
        connection = configuration.connection

        print("Alicat MFC read-only diagnostic")
        print(f"Device ID: {configuration.device_id}")
        print(f"Target: {connection.port}, address {configuration.unit_address}")
        print(f"Baud rate: {connection.baud_rate}")
        print(f"Timeout: {connection.timeout_seconds:g} seconds")
        print("Sending one status poll only; no setpoint command.")

        result = read_alicat_state(configuration)

    except Exception as error:
        print()
        print("DIAGNOSTIC FAILED")
        print(f"Error type: {type(error).__name__}")
        print(f"Details: {error}")
        print()
        print("No setpoint or gas-selection command was requested.")
        return 1

    state = result.state
    print()
    print("DIAGNOSTIC PASSED")
    print(f"Raw response: {result.raw_response}")
    print(f"Mass flow: {state.mass_flow:g} {state.mass_flow_unit}")
    print(
        "Volumetric flow: "
        f"{state.volumetric_flow:g} {state.volumetric_flow_unit}"
    )
    print(
        "Absolute pressure: "
        f"{state.absolute_pressure:g} {state.pressure_unit}"
    )
    print(
        "Gas temperature: "
        f"{state.gas_temperature:g} {state.temperature_unit}"
    )
    print(f"Setpoint: {state.setpoint:g} {state.setpoint_unit}")
    print(f"Selected gas/calibration: {state.gas or 'not reported'}")
    print(
        "Status codes: "
        + (", ".join(state.status_codes) if state.status_codes else "none")
    )
    print(f"Measurement quality: {state.quality.value}")
    print(f"Timestamp (UTC): {state.timestamp.isoformat()}")
    print()
    print("No setpoint or gas-selection command was requested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
