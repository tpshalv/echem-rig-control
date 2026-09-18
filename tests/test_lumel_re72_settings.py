import pytest

from rig_control.devices.lumel_re72 import LumelRe72, RE72_SETTINGS
from rig_control.ui.home.model import HomeViewModel


class FakeBus:
    def __init__(self) -> None:
        self.registers = {item.register: item.register % 100 for item in RE72_SETTINGS}
        self.registers[4003] = 2
        self.registers[4084] = 2750
        self.registers[4028] = 1
        self.registers[4031] = 2
        self.writes: list[tuple[int, int, int]] = []

    def read_holding_registers(self, slave: int, address: int, count: int) -> list[int]:
        assert slave == 1
        return [self.registers.get(register, 0) for register in range(address, address + count)]

    def write_register(self, slave: int, address: int, value: int) -> None:
        self.writes.append((slave, address, value))
        self.registers[address] = value


def test_read_settings_scales_temperature_registers() -> None:
    device = LumelRe72("re72_1", FakeBus(), 1)  # type: ignore[arg-type]

    values = device.read_settings()

    assert values["target_setpoint"] == 27.5
    assert values["control_algorithm"] == 34


def test_read_settings_converts_tenths_to_display_units() -> None:
    bus = FakeBus()
    bus.registers[4045] = 125
    bus.registers[4046] = 375
    bus.registers[4064] = 20
    device = LumelRe72("re72_1", bus, 1)  # type: ignore[arg-type]

    values = device.read_settings()

    assert values["pid1_td"] == 12.5
    assert values["pid1_y0"] == 37.5
    assert values["output_2_period"] == 2.0


def test_runtime_state_identifies_mode_active_pid_and_setpoint() -> None:
    bus = FakeBus()
    bus.registers[4003] = 2 | (1 << 7) | (1 << 8) | (2 << 9)
    bus.registers[4008] = 3150
    device = LumelRe72("re72_1", bus, 1)  # type: ignore[arg-type]

    state = device.read_runtime_state()

    assert state == {
        "mode": "Manual",
        "active_pid_set": 3,
        "autotune_active": True,
        "autotune_failed": False,
        "active_setpoint": 31.5,
    }


def test_start_autotune_validates_configuration_and_sends_command() -> None:
    bus = FakeBus()
    bus.registers[4034] = 1
    bus.registers[4044] = 300
    bus.registers[4099] = 1
    device = LumelRe72("re72_1", bus, 1)  # type: ignore[arg-type]

    device.start_autotune()

    assert bus.writes == [(1, 4000, 3)]


def test_full_snapshot_reads_complete_controller_configuration_range() -> None:
    device = LumelRe72("re72_1", FakeBus(), 1)  # type: ignore[arg-type]

    snapshot = device.read_full_snapshot()

    assert min(snapshot) == 4001
    assert max(snapshot) == 4124
    assert len(snapshot) == 124


def test_restore_snapshot_writes_only_changed_restorable_registers() -> None:
    bus = FakeBus()
    device = LumelRe72("re72_1", bus, 1)  # type: ignore[arg-type]
    snapshot = device.read_full_snapshot()
    snapshot[4044] = 500

    changed = device.restore_snapshot(snapshot)

    assert changed == (4044,)
    assert bus.writes == [(1, 4044, 500)]


def test_write_setting_scales_temperature_and_writes_raw_values() -> None:
    bus = FakeBus()
    device = LumelRe72("re72_1", bus, 1)  # type: ignore[arg-type]

    device.write_setting("target_setpoint", "31.25")
    device.write_setting("control_algorithm", "1")
    device.write_setting("pid1_td", "12.5")

    assert bus.writes == [(1, 4084, 3125), (1, 4034, 1), (1, 4045, 125)]


def test_write_setting_accepts_human_readable_output_choice() -> None:
    bus = FakeBus()
    device = LumelRe72("re72_1", bus, 1)  # type: ignore[arg-type]

    device.write_setting("output_1_assignment", "Upper absolute alarm")
    device.write_setting("output_2_assignment", "Heating control")
    device.write_setting("control_algorithm", "PID")
    device.write_setting("control_action", "Reverse/heating")
    device.write_setting("alarm_1_latch", "Enabled")

    assert bus.writes == [
        (1, 4027, 5),
        (1, 4030, 1),
        (1, 4034, 1),
        (1, 4035, 1),
        (1, 4068, 1),
    ]


def test_write_setting_rejects_fractional_raw_and_protected_values() -> None:
    device = LumelRe72("re72_1", FakeBus(), 1)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="precision"):
        device.write_setting("control_algorithm", "0.5")
    with pytest.raises(ValueError, match="protected"):
        device.write_setting("slave_address", "2")
    with pytest.raises(ValueError, match="protected"):
        device.write_setting("output_2_type", "0/5 V SSR drive")


def test_readback_verification_treats_equivalent_numeric_text_as_equal() -> None:
    assert HomeViewModel._re72_values_match("12", "12.0")
    assert HomeViewModel._re72_values_match("0.50", "0.5")
    assert not HomeViewModel._re72_values_match("PID", "On/off")
