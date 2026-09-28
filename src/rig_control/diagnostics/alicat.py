from dataclasses import replace

from rig_control.devices.alicat.protocol_fields import AlicatFrameField
from rig_control.devices.alicat.verification import (
    ABSOLUTE_PRESSURE_VARIABLE,
    AlicatControlConfiguration,
    AlicatFrameColumn,
    addressed_tokens,
    default_frame_layout,
    parse_control_configuration,
    parse_control_mode,
    parse_frame_layout,
    parse_identity,
)
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
    control_configuration: AlicatControlConfiguration | None = None
    verification_error: str = ""
    #: The controller's own setpoint readback, used to confirm that the
    #: saved data-frame layout really matches the live frame.
    setpoint_readback: tuple[float, str] | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredAlicat:
    """One address that answered a scan, with its detected configuration.

    The detected role comes from the instrument's live control variable and
    inverse-control register, never from model or serial text, which some
    firmware does not report at all.
    """

    address: str
    raw_response: str
    manufacturer_response: str = ""
    data_format_response: str = ""
    firmware_response: str = ""
    model: str | None = None
    serial_number: str | None = None
    detected_role: str | None = None
    is_controller: bool | None = None
    setpoint_unit: str | None = None
    minimum_setpoint: float | None = None
    maximum_setpoint: float | None = None
    frame_columns: tuple[AlicatFrameColumn, ...] | None = None
    inferred_maximum_flow_sccm: float | None = None
    configuration_error: str = ""

    @property
    def frame_fields(self) -> tuple[AlicatFrameField, ...] | None:
        if self.frame_columns is None:
            return None
        return tuple(column.field for column in self.frame_columns)

    def unit_for(self, field: AlicatFrameField) -> str | None:
        """Return the unit this instrument prints for one frame column."""

        for column in self.frame_columns or ():
            if column.field is field:
                return column.unit
        return None

    @property
    def control_description(self) -> str:
        if self.detected_role == "bpr":
            return "Back-pressure controller (absolute pressure, inverse)"
        if self.detected_role == "mfc":
            return "Mass-flow controller (mass flow, forward)"
        if self.is_controller is False:
            return "Mass-flow meter (read-only)"
        return self.configuration_error or "Control mode could not be read"

    @property
    def usable(self) -> bool:
        """True when the scan learned enough to add this device automatically."""

        return self.detected_role is not None or self.is_controller is False


def probe_alicat_device(
    transport: SerialTextTransport,
    address: str,
    raw_response: str = "",
) -> DiscoveredAlicat:
    """Read one already-responding Alicat's configuration, without writing.

    Identity text is informational: a missing or unusual manufacturer reply
    must not prevent role detection, which uses LR and register 20 only.
    """

    manufacturer = _optional_query(transport, address + "??M*")
    data_format = _optional_query(transport, address + "??D*")
    firmware = _optional_query(transport, address + "VE")
    serial_number, model = parse_identity(manufacturer)
    inferred_maximum = infer_flow_range_sccm(manufacturer)

    try:
        frame_columns = parse_frame_layout(address, data_format)
    except ValueError as error:
        return DiscoveredAlicat(
            address, raw_response, manufacturer, data_format, firmware,
            model, serial_number, configuration_error=str(error),
        )

    loop = _optional_query(transport, address + "LR")
    register = _optional_query(transport, address + "R20")
    mode = None
    mode_error = ""
    if loop and register:
        try:
            mode = parse_control_mode(address, loop, register)
        except (ValueError, TypeError) as error:
            mode_error = str(error)
    else:
        missing = " and ".join(
            name for name, reply in (("LR", loop), ("R20", register)) if not reply
        )
        mode_error = f"the instrument did not answer the {missing} query"

    if mode is None:
        # A failed controller query never proves this is a meter. Only the
        # instrument's own data frame, which has no setpoint column on a
        # meter, is allowed to say so.
        fields = tuple(column.field for column in frame_columns or ())
        if frame_columns is not None and AlicatFrameField.SETPOINT not in fields:
            return DiscoveredAlicat(
                address, raw_response, manufacturer, data_format, firmware,
                model, serial_number, None, False, None, None, None,
                frame_columns, inferred_maximum,
            )
        return DiscoveredAlicat(
            address, raw_response, manufacturer, data_format, firmware,
            model, serial_number, configuration_error=(
                "Control configuration could not be read at address "
                + address + ": " + mode_error
                + ". If the instrument answered but its reply is not the shape "
                "shown above, run scripts/alicat_dump.py to capture every "
                "read-only reply from this unit."
            ),
        )

    if mode.loop_variable == ABSOLUTE_PRESSURE_VARIABLE and mode.maximum_setpoint is None:
        try:
            mode = replace(mode, **_read_full_scale(transport, address, mode.setpoint_unit))
        except (ValueError, TypeError, IndexError, TimeoutError, OSError):
            pass  # Bounds stay unknown; the add path reports that specifically.

    if frame_columns is None or AlicatFrameField.SETPOINT not in tuple(
        column.field for column in frame_columns
    ):
        # A controller always reports a setpoint column, so an unreadable or
        # partial frame description falls back to the documented order, which
        # the add path then confirms against a live setpoint readback. Its
        # units are unknown, so each one is left for the caller to default.
        frame_columns = tuple(
            AlicatFrameColumn(field)
            for field in default_frame_layout(controller=True)
        )

    detected = mode.detected_role
    error = ""
    if detected is None:
        error = (
            "This Alicat reports " + mode.description + ", which this software does "
            "not control. Configure it for mass flow with forward regulation, or "
            "for absolute pressure with inverse regulation, then scan again."
        )
    return DiscoveredAlicat(
        address, raw_response, manufacturer, data_format, firmware, model,
        serial_number, detected, True, mode.setpoint_unit,
        mode.minimum_setpoint, mode.maximum_setpoint, frame_columns,
        inferred_maximum, error,
    )


def _read_full_scale(
    transport: SerialTextTransport,
    address: str,
    setpoint_unit: str,
) -> dict[str, float]:
    from rig_control.devices.pressure_controller import absolute_unit_factor

    parts = addressed_tokens(address, transport.request(address + "FPF 2"))
    if len(parts) != 3:
        raise ValueError("Unrecognised absolute-pressure full-scale response")
    maximum = float(parts[0])
    int(parts[1])
    if maximum <= 0:
        raise ValueError("Invalid absolute-pressure full scale")
    maximum *= absolute_unit_factor(parts[2]) / absolute_unit_factor(setpoint_unit)
    return {"minimum_setpoint": 0.0, "maximum_setpoint": maximum}


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
    found: list[tuple[str, str]] = []
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
            found.append((address, response))
        discovered = tuple(
            probe_alicat_device(selected_transport, address, response)
            for address, response in found
        )
    finally:
        selected_transport.close()
    return discovered


def probe_alicat_address(
    port: str,
    address: str,
    baud_rate: int = 19200,
    *,
    timeout_seconds: float = 1.0,
    transport: SerialTextTransport | None = None,
) -> DiscoveredAlicat:
    """Read one address's configuration so a new device can be added."""

    address = address.strip().upper()
    if len(address) != 1 or not "A" <= address <= "Z":
        raise ValueError("Alicat address must be one letter A-Z")
    selected = transport or PySerialTextTransport(
        port=port,
        baud_rate=baud_rate,
        timeout_seconds=timeout_seconds,
    )
    try:
        selected.open()
        response = selected.request(address)
        if response.split(maxsplit=1)[0].upper() != address:
            raise ValueError(
                "Address " + address + " answered with " + repr(response)
                + ", which is not its own status frame"
            )
        return probe_alicat_device(selected, address, response)
    finally:
        selected.close()


def _optional_query(transport: SerialTextTransport, command: str) -> str:
    try:
        return transport.request(command)
    except (TimeoutError, OSError, RuntimeError):
        return ""


def infer_flow_range_sccm(manufacturer_response: str) -> float | None:
    """Read the flow range printed in a model name, when one is reported.

    This is a convenience for meters, which have no LR range to read. It
    never decides what kind of device this is: only the live control
    configuration and the instrument's own data frame do that.
    """

    match = re.search(
        r"\bM[CW]?\s*-\s*(\d+(?:\.\d+)?)\s*(SCCM|SLPM)\b",
        manufacturer_response,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    number, unit = match.groups()
    return float(number) * (1000.0 if unit.upper() == "SLPM" else 1.0)


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

    observed = None
    verification_error = ""
    setpoint_readback = None
    try:
        bus.connect()
        raw_response = bus.request(configuration.unit_address)
        state = protocol.parse_state(
            configuration.unit_address,
            raw_response,
        )
        if configuration.is_controller:
            try:
                observed = protocol.read_control_configuration(configuration.unit_address)
                setpoint_readback = protocol.read_setpoint(configuration.unit_address)
            except Exception as error:
                verification_error = f"{type(error).__name__}: {error}"
    finally:
        bus.disconnect()

    return AlicatDiagnosticResult(
        raw_response, state, observed, verification_error, setpoint_readback
    )


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


def read_alicat_configuration(configuration, *, transport=None):
    """Inspect identity and controller settings without issuing any writes."""
    connection = configuration.connection
    selected = transport or PySerialTextTransport(connection.port, connection.baud_rate, connection.timeout_seconds)
    bus = AlicatBus(connection.connection_id, selected)
    protocol = AlicatAsciiProtocolClient(bus, configuration.frame_fields, configuration.engineering_units,
                                        requires_setpoint=configuration.is_controller)
    protocol.connect()
    try:
        return protocol.read_control_configuration(configuration.unit_address)
    finally:
        protocol.disconnect()
