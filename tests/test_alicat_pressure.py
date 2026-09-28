"""Protocol-contract fixtures, not captures from commissioned hardware."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from rig_control.app_settings import default_app_settings
from rig_control.control.commands import CommandSource, SetMfcFlow, SetPressureSetpoint, ResumePressureControl
from rig_control.control.service import RigControlService, ControlExecutionError
from rig_control.devices.alicat.bus import AlicatBus
from rig_control.devices.alicat.configuration import AlicatMfcConfiguration, AlicatSerialConfiguration
from rig_control.devices.alicat.pressure import AlicatBackPressureController
from rig_control.devices.alicat.protocol import AlicatAsciiProtocolClient, AlicatEngineeringUnits, AlicatFrameField, AlicatInstrumentState, AlicatProtocolClient
from rig_control.devices.alicat.verification import AlicatControlConfiguration, parse_control_configuration, verify_role
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.devices.pressure_controller import PressurePolicy, absolute_unit_factor
from rig_control.models import DeviceStatus
from rig_control.transports.simulated_serial_text import SimulatedSerialTextTransport

from captures import load_capture


FRAME = "A Pressure (PSIA)\nTemperature (degC)\nVolumetric flow (CCM)\nMass flow (SCCM)\nSetpoint (PSIA)\nGas"

#: The data-frame table this instrument really reports, which states
#: each column's precision as well as its unit.
REAL_FRAME = load_capture(
    "alicat-mc-2slpm-d-10v22-back-pressure.txt"
)["C??D*"].replace("C ", "B ")


def observed(**changes):
    return replace(AlicatControlConfiguration("12345", "MC-2SLPM-D", "10v05", 34,
        "psia", True, 0.0, 100.0, FRAME, datetime.now(UTC)), **changes)


def configuration(**changes):
    return replace(AlicatMfcConfiguration(
        "outlet", "Outlet BPR", "B", AlicatSerialConfiguration("bus", "COM5", 19200, 0.1),
        MassFlowControllerLimits(2000, "SCCM"),
        tuple(AlicatFrameField(name) for name in ("absolute_pressure", "gas_temperature", "volumetric_flow", "mass_flow", "setpoint", "gas")),
        AlicatEngineeringUnits("SCCM", "CCM", "psia", "degC", "psia"),
        is_bpr=True, expected_serial="12345",
        downstream_valve_confirmed=True, maximum_pressure_bara=6.0,
    ), **changes)


class FakePressureProtocol(AlicatProtocolClient):
    def __init__(self):
        self.observed = observed()
        self.setpoint = 20.0
        self.writes = []
        self.hold = False
        self.accept = True
        self.read_error = None

    def read_control_configuration(self, address):
        if self.read_error:
            raise self.read_error
        return self.observed

    def read_state(self, address):
        return AlicatInstrumentState(100, "SCCM", 80, "CCM", 20, "psia", 25, "degC", self.setpoint, "psia",
                                     status_codes=("HLD",) if self.hold else (), valve_drive_percent=0 if self.hold else 20)

    def set_flow_setpoint(self, address, flow):
        raise AssertionError("BPR must never send a flow command")

    def set_pressure_setpoint(self, address, value):
        self.writes.append((address, "pressure", value))
        if self.accept:
            self.setpoint = value

    def read_setpoint(self, address):
        return self.setpoint, self.observed.setpoint_unit

    def hold_closed(self, address):
        self.writes.append((address, "close"))
        self.hold = True
        return self.read_state(address)

    def cancel_hold(self, address):
        self.writes.append((address, "resume"))
        self.hold = False
        return self.read_state(address)


def device(policy=None, **changes):
    protocol = FakePressureProtocol()
    controller = AlicatBackPressureController(configuration(**changes), protocol, pressure_policy=policy)
    controller.connect()
    return controller, protocol


@pytest.mark.parametrize("value,unit,expected", [(2.5, "bara", 250000), (1.48675, "barg", 250000),
    (0, "barg", 101325), (250, "kpaa", 250000), (100000, "paa", 100000)])
def test_explicit_pressure_conversion(value, unit, expected):
    assert PressurePolicy().to_absolute_pa(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, 2.50001])
def test_invalid_pressure_never_writes(value):
    controller, protocol = device()
    with pytest.raises((ValueError, TypeError)):
        controller.set_pressure_setpoint(value)
    assert protocol.writes == []


@pytest.mark.parametrize("unit", ["bara", "barg", "psia", "psig", "kpaa", "kpag", "paa", "pag"])
def test_absolute_limit_applies_in_every_input_unit(unit):
    policy = PressurePolicy()
    factor = policy.to_absolute_pa(1, unit) - policy.to_absolute_pa(0, unit)
    value = (251000 - policy.to_absolute_pa(0, unit)) / factor
    controller, protocol = device()
    with pytest.raises(ValueError, match="at most"):
        controller.set_pressure_setpoint(value, unit)
    assert not protocol.writes


def test_exact_boundary_survives_native_unit_serialization():
    controller, protocol = device()
    controller.set_pressure_setpoint(1.48675, "barg")
    sent = protocol.writes[-1][2]
    assert sent * absolute_unit_factor("psia") <= 250000
    assert controller.pressure_setpoint_pa == pytest.approx(250000)


def test_high_mode_is_deliberate_and_still_bounded_by_instrument():
    controller, protocol = device(PressurePolicy(False, 5))
    with pytest.raises(ValueError):
        controller.set_pressure_setpoint(3)
    controller, protocol = device(PressurePolicy(True, 5))
    controller.set_pressure_setpoint(4)
    protocol.observed = observed(maximum_setpoint=40)  # ~2.758 bara
    before = list(protocol.writes)
    with pytest.raises(ValueError):
        controller.set_pressure_setpoint(4)
    assert protocol.writes == before


@pytest.mark.parametrize("changes", [dict(loop_variable=37), dict(inverse=False), dict(setpoint_unit="barg")])
def test_detected_configuration_change_blocks_every_operating_write(changes):
    controller, protocol = device()
    protocol.observed = observed(**changes)
    with pytest.raises(RuntimeError):
        controller.set_pressure_setpoint(2)
    assert not protocol.writes
    assert not controller.control_ready


def test_missing_physical_confirmation_does_not_become_ready():
    for changes in (dict(downstream_valve_confirmed=False),):
        controller, protocol = device(**changes)
        assert not controller.control_ready
        with pytest.raises(RuntimeError):
            controller.set_pressure_setpoint(2)
        assert not protocol.writes


def test_failed_setpoint_readback_faults_without_retrying_write():
    controller, protocol = device()
    protocol.accept = False
    with pytest.raises(RuntimeError, match="not accepted"):
        controller.set_pressure_setpoint(2)
    assert len(protocol.writes) == 1
    assert controller.status is DeviceStatus.FAULTED


def test_telemetry_includes_pressure_flow_temperature_drive_and_hold():
    controller, protocol = device()
    readings = {r.channel: r.measurement for r in controller.read_measurements()}
    assert readings["mass_flow"].value == 100
    assert readings["gas_temperature"].value == 25
    assert readings["absolute_pressure"].unit == "bara"
    # Alicat pressure is absolute only; no derived gauge channel is published.
    assert "gauge_pressure" not in readings
    assert readings["pressure_setpoint_absolute"].unit == "bara"
    assert readings["valve_drive_percent"].value == 20
    assert readings["valve_hold"].value == 0
    assert "setpoint" not in readings
    assert not protocol.writes


def test_global_stop_closes_outlet_and_requires_explicit_resume():
    controller, protocol = device()
    manager = DeviceManager()
    manager.register(controller)
    service = RigControlService(manager)
    assert service.enter_global_safe_state().all_succeeded
    assert protocol.writes == [("B", "close")]
    assert controller.valve_hold
    controller.set_pressure_setpoint(2)
    assert protocol.hold  # Setting a target does not release the stop.
    service.execute(ResumePressureControl("outlet", CommandSource.MANUAL))
    assert not protocol.hold
    assert protocol.writes[-1] == ("B", "resume")


def test_stop_after_mode_change_blocks_without_writing():
    controller, protocol = device()
    protocol.observed = observed(loop_variable=37)
    with pytest.raises(RuntimeError, match="Saved role does not match"):
        controller.enter_safe_state()
    assert protocol.writes == []


def test_resume_rejects_existing_overlimit_setpoint_without_releasing_hold():
    controller, protocol = device()
    controller.enter_safe_state()
    protocol.setpoint = 70
    with pytest.raises(ValueError):
        controller.resume_regulation()
    assert protocol.writes == [("B", "close")]


def test_central_api_rejects_flow_commands_to_bpr_and_enforces_recipe_ownership():
    controller, protocol = device()
    manager = DeviceManager()
    manager.register(controller)
    service = RigControlService(manager)
    with pytest.raises(ControlExecutionError, match="mass flow"):
        service.execute(SetMfcFlow("outlet", 100, CommandSource.MANUAL))
    with pytest.raises(ControlExecutionError, match="at most"):
        service.execute(SetPressureSetpoint("outlet", 3, CommandSource.SAFETY_SYSTEM))
    service.begin_recipe_control()
    service.execute(SetPressureSetpoint("outlet", 2, CommandSource.RECIPE))
    assert len(protocol.writes) == 1


def test_parse_documented_configuration_and_reject_legacy_or_bad_replies():
    args = ("A", "A 10v05 2023", "A Model: MC-2SLPM-D\nA Serial No: 12345",
            "A 34 +0.0000 +160.000 10 PSIA", "A   020 = 42007", FRAME)
    result = parse_control_configuration(*args)
    assert result.inverse and result.loop_variable == 34
    assert result.serial_number == "12345"
    with pytest.raises(ValueError, match="older firmware"):
        parse_control_configuration(args[0], "A 8v24", *args[2:])
    with pytest.raises(ValueError):
        parse_control_configuration(*args[:3], "B 34 +0.0000 +160.000 10 PSIA", *args[4:])


def test_mode_detection_does_not_require_labelled_identity_fields():
    result = parse_control_configuration(
        "A",
        "A 10v05 2023",
        "A M00 ALICAT SCIENTIFIC\nA M01 www.alicat.com",
        "A 34 +0.0000 +160.000 10 PSIA",
        "A   020 = 42007",
        FRAME,
    )
    assert result.serial_number is None
    assert result.model is None
    assert result.detected_role == "bpr"
    verify_role(result, bpr=True, flow_unit="SCCM", downstream_confirmed=True)


def test_ascii_queries_are_read_only_and_9v_uses_pressure_full_scale():
    transport = SimulatedSerialTextTransport()
    config = configuration()
    protocol = AlicatAsciiProtocolClient(AlicatBus("bus", transport), config.frame_fields, config.engineering_units)
    replies = {"BVE": "B 9v00", "B??M*": "B Model: MC-2SLPM-D\nB Serial No: 12345",
               "BLR": "B 34 10 PSIA", "BR20": "B   020 = 42007", "B??D*": FRAME,
               "BFPF 2": "B 100 10 PSIA"}
    for command, reply in replies.items():
        transport.queue_response(command, reply)
    protocol.connect()
    result = protocol.read_control_configuration("B")
    assert result.maximum_setpoint == 100
    assert transport.requests == tuple(replies)


def test_live_mode_check_asks_only_for_the_loop_variable_and_register():
    transport = SimulatedSerialTextTransport()
    config = configuration()
    protocol = AlicatAsciiProtocolClient(AlicatBus("bus", transport), config.frame_fields, config.engineering_units)
    transport.queue_response("BLR", "B 34 +0.0000 +100.000 10 PSIA")
    transport.queue_response("BR20", "B   020 = 42007")
    protocol.connect()

    mode = protocol.read_control_mode("B")

    assert (mode.loop_variable, mode.inverse, mode.detected_role) == (34, True, "bpr")
    assert mode.maximum_setpoint == 100
    # No identity, firmware or data-frame query is sent for a live check.
    assert transport.requests == ("BLR", "BR20")


def test_missing_manufacturer_reply_does_not_block_reading_the_configuration():
    transport = SimulatedSerialTextTransport()
    config = configuration()
    protocol = AlicatAsciiProtocolClient(AlicatBus("bus", transport), config.frame_fields, config.engineering_units)
    transport.queue_response("BVE", "B 10v05")
    transport.queue_error("B??M*", TimeoutError("no manufacturer reply"))
    transport.queue_response("BLR", "B 34 +0.0000 +100.000 10 PSIA")
    transport.queue_response("BR20", "B   020 = 42007")
    transport.queue_response("B??D*", FRAME)
    protocol.connect()

    result = protocol.read_control_configuration("B")

    assert result.model is None and result.serial_number is None
    assert result.detected_role == "bpr"


def test_ascii_pressure_write_readback_and_close_resume_commands():
    transport = SimulatedSerialTextTransport()
    config = configuration()
    protocol = AlicatAsciiProtocolClient(AlicatBus("bus", transport), config.frame_fields, config.engineering_units)
    protocol.connect()
    transport.queue_response("BLS 20", "B 20 20 10 PSIA")
    transport.queue_response("BLS", "B 20 20 10 PSIA")
    transport.queue_response("BHC", "B 20 25 80 100 20 N2 HLD")
    transport.queue_response("BC", "B 20 25 80 100 20 N2")
    protocol.set_pressure_setpoint("B", 20)
    assert protocol.read_setpoint("B") == (20, "PSIA")
    assert "HLD" in protocol.hold_closed("B").status_codes
    assert "HLD" not in protocol.cancel_hold("B").status_codes
    assert transport.requests == ("BLS 20", "BLS", "BHC", "BC")


def test_operation_ui_has_pressure_editors_and_no_flow_editor():
    from rig_control.polling import PollingService
    from rig_control.experiment_recording import ExperimentRecorder
    from rig_control.ui.operation.model import OperationViewModel
    controller, protocol = device(PressurePolicy(True, 5))
    manager = DeviceManager()
    manager.register(controller)
    model = OperationViewModel(manager, PollingService(manager), ExperimentRecorder(), RigControlService(manager), profile_id="test")
    rows = {r.channel: r for r in model.channel_rows()}
    assert rows["pressure_setpoint_absolute"].writable
    assert rows["pressure_setpoint_absolute"].unit == "bara"
    # The gauge entry and derived gauge reading have been removed.
    assert "pressure_setpoint_gauge" not in rows
    assert "atmospheric_reference" not in rows
    assert "OVERRIDE" in rows["pressure_ceiling"].channel_name
    assert "setpoint" not in rows
    assert model.apply_channel_value("outlet", "pressure_setpoint_absolute", 1.5).succeeded
    assert not model.apply_channel_value("outlet", "setpoint", 50).succeeded


def test_settings_persist_pressure_policy_without_enabling_override_by_value_alone(tmp_path):
    from rig_control.app_settings_writing import write_app_settings
    from rig_control.app_settings_loading import load_app_settings
    settings = default_app_settings()
    values = dict(settings.values, pressure_maximum_bara=5)
    updated = replace(settings, values=values)
    path = tmp_path / "settings.toml"
    write_app_settings(updated, path)
    assert load_app_settings(path).pressure_policy.maximum_pa == 250000
    updated = replace(updated, values=dict(values, pressure_high_pressure_mode=True))
    write_app_settings(updated, path)
    assert load_app_settings(path).pressure_policy.maximum_pa == 500000


@pytest.mark.parametrize("value,inverse", [
    # From Alicat's "change your pressure controller to inverse control mode"
    # tutorial: register 20 reads 276, and inverse control is enabled by
    # adding 32768 to give 33044. Other bits differ between instruments, so
    # only bit 32768 may be read.
    (276, False),
    (33044, True),
    (9239, False),      # As read from the bench MC-2SLPM-D units.
    (9239 + 32768, True),
])
def test_inverse_control_is_read_from_bit_32768_alone(value, inverse):
    from rig_control.devices.alicat.verification import inverse_control_enabled

    assert inverse_control_enabled("A", f"A   020 = {value}") is inverse


def test_display_units_change_what_is_shown_and_what_a_typed_value_means():
    """A preference must convert both directions, and never change control."""

    from rig_control.polling import PollingService
    from rig_control.experiment_recording import ExperimentRecorder
    from rig_control.display_units import DisplayUnits
    from rig_control.ui.operation.model import OperationViewModel

    controller, protocol = device()
    manager = DeviceManager()
    manager.register(controller)
    model = OperationViewModel(
        manager, PollingService(manager), ExperimentRecorder(),
        RigControlService(manager), profile_id="test",
        display_units=DisplayUnits(pressure="psia"),
    )

    rows = {r.channel: r for r in model.channel_rows()}
    setpoint = rows["pressure_setpoint_absolute"]
    assert setpoint.unit == "psia"
    assert "psia" in setpoint.channel_name
    # The instrument's own ceiling, shown in the chosen unit.
    assert rows["pressure_ceiling"].unit == "psia"
    assert rows["pressure_ceiling"].value == pytest.approx(2.5 * 100_000 / 6894.757293168)

    assert model.apply_channel_value("outlet", "pressure_setpoint_absolute", 29).succeeded

    # 29 psia is about 2 bara: sent in psia, which is this instrument's own
    # unit, and well under the 2.5 bara ceiling.
    assert protocol.writes[-1][1] == "pressure"
    assert protocol.writes[-1][2] == pytest.approx(29, abs=0.001)


def test_the_pressure_ceiling_still_applies_in_the_displayed_unit():
    from rig_control.polling import PollingService
    from rig_control.experiment_recording import ExperimentRecorder
    from rig_control.display_units import DisplayUnits
    from rig_control.ui.operation.model import OperationViewModel

    controller, protocol = device()
    manager = DeviceManager()
    manager.register(controller)
    model = OperationViewModel(
        manager, PollingService(manager), ExperimentRecorder(),
        RigControlService(manager), profile_id="test",
        display_units=DisplayUnits(pressure="psia"),
    )

    # 40 psia is about 2.76 bara, over the 2.5 bara ceiling.
    result = model.apply_channel_value("outlet", "pressure_setpoint_absolute", 40)

    assert not result.succeeded
    assert not protocol.writes


def test_a_setpoint_rounded_to_the_instrument_s_own_precision_is_accepted():
    """The failure seen on the bench: 1 bara is 14.5038 psia, and this
    instrument reports its setpoint to two decimal places."""

    controller, protocol = device()

    protocol.observed = observed(frame_description=REAL_FRAME)
    controller.verify_control(full=True)

    # Store the setpoint the way the instrument does: to hundredths of a psi.
    original = type(protocol).set_pressure_setpoint

    def quantised(self, address, value):
        original(self, address, value)
        self.setpoint = round(value, 2) + 0.005  # rounded, and then some

    type(protocol).set_pressure_setpoint = quantised
    try:
        controller.set_pressure_setpoint(1.0, "bara")
    finally:
        type(protocol).set_pressure_setpoint = original

    assert controller.pressure_setpoint_pa == pytest.approx(100_000, rel=1e-3)


def test_the_tolerance_comes_from_the_frame_the_instrument_reports():
    controller, protocol = device()

    # The fixture's frame states no precision, so the configured value stands.
    assert controller.setpoint_tolerance() == pytest.approx(0.001)

    protocol.observed = observed(frame_description=REAL_FRAME)
    controller.verify_control(full=True)

    # Two decimal places in PSIA: the instrument cannot express finer.
    assert controller.setpoint_tolerance() == pytest.approx(0.01)


def test_a_setpoint_the_instrument_did_not_take_is_still_rejected():
    controller, protocol = device()
    protocol.accept = False

    with pytest.raises(RuntimeError, match="not accepted"):
        controller.set_pressure_setpoint(2.0)

    # The message names both values and the tolerance they were compared at.
    assert len(protocol.writes) == 1
    assert controller.status is DeviceStatus.FAULTED


def test_a_setpoint_at_the_ceiling_is_not_faulted_by_the_instrument_s_rounding():
    """A request at exactly the ceiling may read back a fraction of one
    reported digit above it. That must not fault an accepted setpoint."""

    controller, protocol = device(PressurePolicy(True, 2.5))
    protocol.observed = observed(frame_description=REAL_FRAME)
    controller.verify_control(full=True)
    original = type(protocol).set_pressure_setpoint

    def rounds_up(self, address, value):
        original(self, address, value)
        self.setpoint = value + 0.01  # one reported digit, in psia

    type(protocol).set_pressure_setpoint = rounds_up
    try:
        controller.set_pressure_setpoint(2.5, "bara")
    finally:
        type(protocol).set_pressure_setpoint = original

    assert controller.status is not DeviceStatus.FAULTED
    # Accepted, and above the ceiling by no more than the one reported digit
    # of 0.01 psia that the instrument rounded by.
    one_digit_pa = 0.01 * absolute_unit_factor("psia")
    assert 250_000 < controller.pressure_setpoint_pa <= 250_000 + one_digit_pa


def test_a_setpoint_clamped_far_above_the_ceiling_is_still_caught():
    controller, protocol = device(PressurePolicy(True, 2.5))
    protocol.observed = observed(frame_description=REAL_FRAME)
    controller.verify_control(full=True)
    original = type(protocol).set_pressure_setpoint

    def clamps_to_full_scale(self, address, value):
        original(self, address, value)
        self.setpoint = 100.0  # the instrument's own range, not what was asked

    type(protocol).set_pressure_setpoint = clamps_to_full_scale
    try:
        with pytest.raises(RuntimeError, match="not accepted"):
            controller.set_pressure_setpoint(2.0, "bara")
    finally:
        type(protocol).set_pressure_setpoint = original

    assert controller.status is DeviceStatus.FAULTED
