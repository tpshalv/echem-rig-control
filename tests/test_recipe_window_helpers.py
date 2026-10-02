from types import SimpleNamespace

import pytest

from rig_control.devices.power_supply import PowerSupplyOperatingMode
from rig_control.recipes import Assignment
from rig_control.rig_profile import DeviceCapability
from rig_control.ui.operation.recipe_settings import (
    SettingSpec, as_range, device_settings, expand_range, format_duration, format_loop_values, group_by_device,
    group_mode, next_device, parse_duration, parse_loop_values, parse_pass_names, parse_value, parse_value_list,
)

NUMBER = SettingSpec("flow", "Flow")
ON_OFF = SettingSpec("output_enabled", "Output", kind="bool")
CC, CV = PowerSupplyOperatingMode.CONSTANT_CURRENT, PowerSupplyOperatingMode.CONSTANT_VOLTAGE


def test_range_is_inclusive_and_counts_either_way():
    assert expand_range(40, 80, 20) == (40, 60, 80)
    assert expand_range(1, 0, -0.25) == (1, 0.75, 0.5, 0.25, 0)
    assert expand_range(0, 0.3, 0.1) == (0, 0.1, 0.2, 0.3)
    with pytest.raises(ValueError, match="wrong way"):
        expand_range(0, 10, -1)
    with pytest.raises(ValueError, match="zero"):
        expand_range(0, 10, 0)


def test_evenly_spaced_values_are_shown_as_a_range():
    assert as_range((40.0, 60.0, 80.0)) == (40, 80, 20)
    assert as_range((1.0, 2.0, 4.0)) is None
    assert as_range((1.0, 2.0)) is None


def test_values_are_parsed_for_the_setting_kind():
    assert parse_value_list("1, 2, 2, 0.5", NUMBER) == (1, 2, 2, 0.5)
    assert parse_value_list("On, off", ON_OFF) == (True, False)
    with pytest.raises(ValueError, match="not a number"):
        parse_value("abc", NUMBER)
    with pytest.raises(ValueError, match="On or Off"):
        parse_value("maybe", ON_OFF)
    with pytest.raises(ValueError, match="Enter a value"):
        parse_value_list("1,,2", NUMBER)


def test_loop_values_can_be_typed_as_a_list_or_a_range():
    assert format_loop_values((40.0, 60.0, 80.0)) == "40 to 80 step 20"
    assert format_loop_values((1.0, 2.0, 4.0)) == "1, 2, 4"
    assert parse_loop_values("40 to 80 step 20", NUMBER) == (40, 60, 80)
    assert parse_loop_values("0.5 TO -0.5 step -0.5", NUMBER) == (0.5, 0, -0.5)
    assert parse_loop_values("1, 2, 4", NUMBER) == (1, 2, 4)
    for values in ((40.0, 60.0, 80.0), (1.0, 2.0, 4.0)):
        assert parse_loop_values(format_loop_values(values), NUMBER) == values


def test_durations_read_naturally_and_read_back():
    shown = [format_duration(s) for s in (45, 90, 600, 3600, 4080, 90000)]
    assert shown == ["45 s", "1.5 min", "10 min", "1 h", "1 h 8 min", "1 d 1 h"]
    assert [parse_duration(text) for text in shown] == [45, 90, 600, 3600, 4080, 90000]
    assert parse_duration("90") == 90 and parse_duration("2.5min") == 150 and parse_duration("1 hr 30 mins") == 5400
    with pytest.raises(ValueError, match="Unknown time unit"):
        parse_duration("5 weeks")
    with pytest.raises(ValueError, match="not a duration"):
        parse_duration("soon")


def test_pass_names_are_comma_separated_or_none():
    assert parse_pass_names("break in, 100 mA , 200 mA") == ("break in", "100 mA", "200 mA")
    assert parse_pass_names("  ") == ()


def test_supply_card_lists_one_mode_with_output_last():
    cc = [spec.name for spec in device_settings(DeviceCapability.DC_POWER_SUPPLY, CC)]
    cv = [spec.name for spec in device_settings(DeviceCapability.DC_POWER_SUPPLY, CV)]
    assert cc == ["current_setpoint", "voltage_limit", "output_enabled"]
    assert cv == ["voltage", "current_limit", "output_enabled"]
    assert [s.name for s in device_settings(DeviceCapability.HOTPLATE_STIRRER)] == [
        "hotplate_temperature", "hotplate_speed", "hotplate_heating", "hotplate_stirring"]


def test_settings_group_by_device_in_first_seen_order():
    items = (Assignment("psu", "current_setpoint", 1), Assignment("hp", "hotplate_temperature", 60),
             Assignment("psu", "output_enabled", True))
    assert [(d, [a.setting for a in group]) for d, group in group_by_device(items)] == [
        ("psu", ["current_setpoint", "output_enabled"]), ("hp", ["hotplate_temperature"])]
    assert group_mode([items[0], items[2]]) is CC
    assert group_mode([items[2]]) is None


def test_next_device_skips_devices_already_in_the_step():
    roles = [SimpleNamespace(device_id="psu"), SimpleNamespace(device_id="hp")]
    assert next_device({"psu"}, roles).device_id == "hp"
    assert next_device({"psu", "hp"}, roles) is None


def test_short_label_drops_the_mode_suffix():
    from rig_control.ui.operation.recipe_settings import short_label, spec_for
    assert short_label(spec_for("current_setpoint")) == "Current setpoint"
    assert short_label(spec_for("hotplate_speed")) == "Stir speed"


def test_currents_show_and_read_in_milliamps_but_stay_amperes():
    from rig_control.ui.operation.recipe_settings import ValueDisplay, spec_for
    current, flow = spec_for("current_setpoint"), spec_for("flow")
    ma, amps = ValueDisplay("mA"), ValueDisplay("A")
    assert ma.show(0.07, current) == "70" and ma.unit(current) == "mA"
    assert ma.read("70", current) == 0.07 and ma.read("250", current) == 0.25
    assert amps.show(0.07, current) == "0.07" and amps.unit(current) == "A"
    assert ma.show(50.0, flow) == "50" and ma.read("50", flow) == 50.0   # only amperes change
    assert ma.show_values((0.05, 0.1, 0.15), current) == "50 to 150 step 50"
    assert ma.read_values("50 to 150 step 50", current) == (0.05, 0.1, 0.15)
    assert ma.read_values("100, 200", current) == (0.1, 0.2)
    assert ma.text("current_setpoint", 0.1, "A") == "100 mA" and amps.text("current_setpoint", 0.1, "A") == "0.1 A"
    assert ma.text("voltage_limit", 5.0, "V") == "5 V"
    with pytest.raises(ValueError):
        ValueDisplay("uA")


def test_ramps_are_described_in_display_units():
    from rig_control.recipes import Ramp, RampKind
    from rig_control.ui.operation.recipe_settings import ValueDisplay, describe_ramp, spec_for
    current, ma = spec_for("current_setpoint"), ValueDisplay("mA")
    assert describe_ramp(Ramp(RampKind.STEP_SIZE, 30, step=0.02), current, ma, start=0.02) == \
        "ramp from 20 mA in 20 mA steps, 30 s each"
    assert describe_ramp(Ramp(RampKind.STEP_SIZE, 30, step=0.02), current, ma) == "ramp in 20 mA steps, 30 s each"
    assert describe_ramp(Ramp(RampKind.STEP_SIZE, 30, start=0.0, step=0.02), current, ma, start=0.05) == \
        "jump to 0 mA, then ramp in 20 mA steps, 30 s each"
    assert describe_ramp(Ramp(RampKind.STEP_COUNT, 60, start=0.05, count=5), current, ma) == \
        "jump to 50 mA, then ramp in 5 steps, 1 min each"
    assert describe_ramp(Ramp(RampKind.SETPOINTS, 30, setpoints=(0.01, 0.05)), current, ma) == \
        "ramp via 10, 50 mA, 30 s each"
    assert describe_ramp(Ramp(RampKind.RATE, 2, start=0.0, rate=0.01 / 60), current, ma) == \
        "jump to 0 mA, then ramp at 10 mA/min, updated every 2 s"


def test_a_typed_current_unit_wins_and_switching_units_keeps_the_quantity():
    from rig_control.ui.operation.recipe_settings import ValueDisplay, spec_for
    current, voltage = spec_for("current_setpoint"), spec_for("voltage_limit")
    ma, amps = ValueDisplay("mA"), ValueDisplay("A")
    assert ma.read("0.25 A", current) == 0.25 and ma.read("250mA", current) == 0.25
    assert amps.read("250 mA", current) == 0.25 and amps.read("0.25", current) == 0.25
    assert ma.read_values("100 to 400 step 100 mA", current) == (0.1, 0.2, 0.3, 0.4)
    assert ma.read_values("0.1, 0.2 A", current) == (0.1, 0.2)
    with pytest.raises(ValueError, match="one unit"):
        ma.read_values("100 mA, 0.2 A", current)
    assert ma.read("5", voltage) == 5.0                      # only currents have mA/A
    assert ma.convert_text("250", current, amps) == "0.25"
    assert amps.convert_text("0.25", current, ma) == "250"
    assert ma.convert_text("100, 200, 300", current, amps, many=True) == "0.1, 0.2, 0.3"
    assert ma.convert_text("", current, amps) == ""          # unfinished text is left alone


def test_end_state_default_summary_and_warnings():
    from rig_control.recipes import Assignment, EndAction, EndDevice, EndState
    from rig_control.ui.operation.recipe_settings import (
        ValueDisplay, default_end_state, end_state_summary, end_state_warnings,
    )
    roles = [SimpleNamespace(device_id="mfc", friendly_name="CO2 MFC", capability=DeviceCapability.MASS_FLOW_CONTROLLER),
             SimpleNamespace(device_id="bpr", friendly_name="BPR", capability=DeviceCapability.BACK_PRESSURE_CONTROLLER),
             SimpleNamespace(device_id="psu", friendly_name="Keithley", capability=DeviceCapability.DC_POWER_SUPPLY)]
    end = default_end_state(roles)
    assert [(d.device_id, d.action) for d in end.devices] == [("mfc", EndAction.LEAVE), ("bpr", EndAction.LEAVE)]
    assert end_state_summary(end, roles, ValueDisplay()) == ["Left as they are: CO2 MFC, BPR", "Everything else: off (safe state)"]
    assert end_state_warnings(end, roles) == []
    assert end_state_summary(EndState(), roles, ValueDisplay()) == ["Everything off (safe state)"]
    # MFC flowing into a closed outlet, and a supply left on, are both flagged.
    risky = EndState((EndDevice("mfc", EndAction.SET, (Assignment("mfc", "flow", 20),)),
                      EndDevice("psu", EndAction.LEAVE)))
    assert end_state_summary(risky, roles, ValueDisplay()) == [
        "Left as it is: Keithley", "CO2 MFC: Flow setpoint 20", "Everything else: off (safe state)"]
    warnings = end_state_warnings(risky, roles)
    assert "can pressurise" in warnings[0] and "output stays on" in warnings[1]
