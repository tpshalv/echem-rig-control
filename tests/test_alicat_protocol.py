import pytest

from rig_control.devices.alicat.bus import AlicatBus
from rig_control.devices.alicat.protocol import (
    AlicatAsciiProtocolClient,
    AlicatEngineeringUnits,
    AlicatFrameField,
)
from rig_control.models import Quality
from rig_control.transports.simulated_serial_text import (
    SimulatedSerialTextTransport,
)


STANDARD_FIELDS = (
    AlicatFrameField.ABSOLUTE_PRESSURE,
    AlicatFrameField.GAS_TEMPERATURE,
    AlicatFrameField.VOLUMETRIC_FLOW,
    AlicatFrameField.MASS_FLOW,
    AlicatFrameField.SETPOINT,
    AlicatFrameField.GAS,
)


def make_protocol(
    fields: tuple[AlicatFrameField, ...] = STANDARD_FIELDS,
    *,
    totalized_flow_unit: str | None = None,
) -> tuple[AlicatAsciiProtocolClient, SimulatedSerialTextTransport]:
    transport = SimulatedSerialTextTransport()
    bus = AlicatBus("alicat_bus", transport)
    bus.connect()
    protocol = AlicatAsciiProtocolClient(
        bus,
        fields,
        AlicatEngineeringUnits(
            mass_flow="sccm",
            volumetric_flow="sccm",
            absolute_pressure="psia",
            gas_temperature="degC",
            setpoint="sccm",
            totalized_flow=totalized_flow_unit,
        ),
    )
    return protocol, transport


def test_poll_parses_explicitly_configured_frame_order() -> None:
    protocol, transport = make_protocol()
    transport.queue_response(
        "A",
        "A +14.7 +22.5 +11.8 +12.3 +15.0 N2",
    )

    state = protocol.read_state("A")

    assert state.absolute_pressure == 14.7
    assert state.gas_temperature == 22.5
    assert state.volumetric_flow == 11.8
    assert state.mass_flow == 12.3
    assert state.setpoint == 15.0
    assert state.gas == "N2"
    assert state.mass_flow_unit == "sccm"
    assert state.pressure_unit == "psia"
    assert state.quality is Quality.GOOD


def test_different_confirmed_frame_order_is_honoured() -> None:
    fields = (
        AlicatFrameField.MASS_FLOW,
        AlicatFrameField.VOLUMETRIC_FLOW,
        AlicatFrameField.GAS_TEMPERATURE,
        AlicatFrameField.ABSOLUTE_PRESSURE,
        AlicatFrameField.SETPOINT,
    )
    protocol, transport = make_protocol(fields)
    transport.queue_response("B", "B 12.3 11.8 22.5 14.7 15.0")

    state = protocol.read_state("b")

    assert state.mass_flow == 12.3
    assert state.absolute_pressure == 14.7
    assert state.gas is None


def test_optional_totalizer_and_status_codes_are_recorded() -> None:
    fields = STANDARD_FIELDS[:-1] + (
        AlicatFrameField.TOTALIZED_FLOW,
        AlicatFrameField.GAS,
    )
    protocol, transport = make_protocol(
        fields,
        totalized_flow_unit="scc",
    )
    transport.queue_response(
        "A",
        "A 14.7 22.5 11.8 12.3 15.0 22741.4 Air HLD MOV",
    )

    state = protocol.read_state("A")

    assert state.totalized_flow == 22741.4
    assert state.totalized_flow_unit == "scc"
    assert state.status_codes == ("HLD", "MOV")
    assert state.quality is Quality.BAD


def test_non_error_status_does_not_degrade_measurement_quality() -> None:
    protocol, transport = make_protocol()
    transport.queue_response(
        "A",
        "A 14.7 22.5 11.8 12.3 15.0 Air LCK",
    )

    state = protocol.read_state("A")

    assert state.status_codes == ("LCK",)
    assert state.quality is Quality.GOOD


def test_setpoint_uses_addressed_alicat_ascii_command() -> None:
    protocol, transport = make_protocol()
    transport.queue_response(
        "CS25.5",
        "C 14.7 22.5 11.8 12.3 25.5 Air",
    )

    protocol.set_flow_setpoint("c", 25.5)

    assert transport.requests == ("CS25.5",)


def test_response_address_must_match_requested_device() -> None:
    protocol, transport = make_protocol()
    transport.queue_response(
        "A",
        "B 14.7 22.5 11.8 12.3 15.0 Air",
    )

    with pytest.raises(ValueError, match="does not match"):
        protocol.read_state("A")


def test_short_response_is_rejected_with_raw_response() -> None:
    protocol, transport = make_protocol()
    transport.queue_response("A", "A 14.7 22.5")

    with pytest.raises(ValueError, match="too few fields") as captured:
        protocol.read_state("A")

    assert "A 14.7 22.5" in str(captured.value)


def test_non_numeric_measurement_is_rejected_with_field_name() -> None:
    protocol, transport = make_protocol()
    transport.queue_response(
        "A",
        "A 14.7 22.5 bad 12.3 15.0 Air",
    )

    with pytest.raises(ValueError, match="volumetric_flow"):
        protocol.read_state("A")


def test_required_fields_cannot_be_omitted() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        make_protocol((AlicatFrameField.MASS_FLOW,))


def test_totalizer_field_requires_an_explicit_unit() -> None:
    fields = STANDARD_FIELDS + (AlicatFrameField.TOTALIZED_FLOW,)

    with pytest.raises(ValueError, match="requires its configured unit"):
        make_protocol(fields)
