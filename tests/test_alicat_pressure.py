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
from rig_control.devices.alicat.verification import AlicatControlConfiguration, frame_signature, parse_control_configuration
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.devices.pressure_controller import PressurePolicy, absolute_unit_factor
from rig_control.models import DeviceStatus
from rig_control.transports.simulated_serial_text import SimulatedSerialTextTransport


FRAME = "A Pressure (PSIA)\nTemperature (degC)\nVolumetric flow (CCM)\nMass flow (SCCM)\nSetpoint (PSIA)\nGas"


def observed(**changes):
    return replace(AlicatControlConfiguration("12345", "MC-2SLPM-D", "10v05", 34,
        "psia", True, 0.0, 100.0, FRAME, datetime.now(UTC)), **changes)


def configuration(**changes):
    return replace(AlicatMfcConfiguration(
        "outlet", "Outlet BPR", "B", AlicatSerialConfiguration("bus", "COM5", 19200, 0.1),
        MassFlowControllerLimits(2000, "SCCM"),
        tuple(AlicatFrameField(name) for name in ("absolute_pressure", "gas_temperature", "volumetric_flow", "mass_flow", "setpoint", "gas")),
        AlicatEngineeringUnits("SCCM", "CCM", "psia", "degC", "psia"),
        is_bpr=True, expected_serial="12345", verified_frame_signature=frame_signature(FRAME),
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


@pytest.mark.parametrize("changes", [dict(loop_variable=37), dict(inverse=False), dict(serial_number="wrong"),
                                    dict(frame_description="changed layout"), dict(setpoint_unit="barg")])
def test_detected_configuration_change_blocks_every_operating_write(changes):
    controller, protocol = device()
    protocol.observed = observed(**changes)
    with pytest.raises(RuntimeError):
        controller.set_pressure_setpoint(2)
    assert not protocol.writes
    assert not controller.control_ready


def test_missing_identity_or_physical_confirmation_does_not_become_ready():
    for changes in (dict(expected_serial=None), dict(downstream_valve_confirmed=False), dict(verified_frame_signature=None)):
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
    assert readings["gauge_pressure"].value == pytest.approx(readings["absolute_pressure"].value - 1.01325)
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


def test_stop_after_mode_change_still_closes_only_the_identified_outlet():
    controller, protocol = device()
    protocol.observed = observed(loop_variable=37)
    controller.enter_safe_state()
    assert protocol.hold
    protocol.observed = observed(serial_number="replacement")
    before = list(protocol.writes)
    with pytest.raises(RuntimeError, match="identity"):
        controller.enter_safe_state()
    assert protocol.writes == before


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
            "A 34 10 PSIA 0 100", "A R20 = 33044", FRAME)
    result = parse_control_configuration(*args)
    assert result.inverse and result.loop_variable == 34
    assert result.serial_number == "12345"
    with pytest.raises(ValueError, match="legacy"):
        parse_control_configuration(args[0], "A 8v24", *args[2:])
    with pytest.raises(ValueError):
        parse_control_configuration(*args[:3], "B 34 10 PSIA 0 100", *args[4:])


def test_ascii_queries_are_read_only_and_9v_uses_pressure_full_scale():
    transport = SimulatedSerialTextTransport()
    config = configuration()
    protocol = AlicatAsciiProtocolClient(AlicatBus("bus", transport), config.frame_fields, config.engineering_units)
    replies = {"BVE": "B 9v00", "B??M*": "B Model: MC-2SLPM-D\nB Serial No: 12345",
               "BLR": "B 34 10 PSIA", "BR20": "B 33044", "B??D*": FRAME,
               "BFPF 2": "B 100 10 PSIA"}
    for command, reply in replies.items():
        transport.queue_response(command, reply)
    protocol.connect()
    result = protocol.read_control_configuration("B")
    assert result.maximum_setpoint == 100
    assert transport.requests == tuple(replies)


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
    assert rows["pressure_setpoint_gauge"].unit == "barg"
    assert "OVERRIDE" in rows["pressure_ceiling"].channel_name
    assert "setpoint" not in rows
    assert model.apply_channel_value("outlet", "pressure_setpoint_gauge", 0.5).succeeded
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
