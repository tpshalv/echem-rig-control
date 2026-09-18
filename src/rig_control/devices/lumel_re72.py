from dataclasses import dataclass

from rig_control.devices.base import Device
from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.models import DeviceStatus, Measurement


@dataclass(frozen=True, slots=True)
class Re72Setting:
    key: str
    label: str
    register: int
    group: str
    description: str = ""
    writable: bool = True
    temperature: bool = False
    choices: tuple[tuple[int, str], ...] = ()
    divisor: float = 1.0
    visible: bool = True


_OUTPUT_1_FUNCTIONS = (
    (0, "Off"),
    (1, "Heating control"),
    (2, "Stepper opening"),
    (3, "Stepper closing"),
    (4, "Cooling control"),
    (5, "Upper absolute alarm"),
    (6, "Lower absolute alarm"),
    (7, "Upper relative alarm"),
    (8, "Lower relative alarm"),
    (9, "Inner relative alarm"),
    (10, "Outer relative alarm"),
    (11, "Timer alarm"),
    (12, "Retransmission"),
    (13, "Program auxiliary EV1"),
    (14, "Program auxiliary EV2"),
    (15, "Sensor/range failure alarm"),
)
_OUTPUT_2_FUNCTIONS = _OUTPUT_1_FUNCTIONS[:12] + (
    (12, "Heater burnout alarm"),
    (13, "Control-element short-circuit alarm"),
    (14, "Retransmission"),
    (15, "Program auxiliary EV1"),
    (16, "Program auxiliary EV2"),
    (17, "Sensor/range failure alarm"),
)
_OUTPUT_TYPES = (
    (0, "Not fitted"),
    (1, "Relay"),
    (2, "0/5 V SSR drive"),
    (3, "4-20 mA"),
    (4, "0-20 mA"),
    (5, "0-5 V"),
    (6, "0-10 V"),
)

RE72_SNAPSHOT_FIRST_REGISTER = 4001
RE72_SNAPSHOT_LAST_REGISTER = 4124
RE72_RESTORABLE_REGISTERS = frozenset(
    (*range(4014, 4026), 4027, 4029, 4030,
     *range(4034, 4077), *range(4081, 4090),
     *range(4095, 4107), 4111, *range(4113, 4125))
)


RE72_SETTINGS: tuple[Re72Setting, ...] = (
    Re72Setting("target_setpoint", "Target setpoint", 4084, "Basic", "Desired temperature. The controller varies heater power to bring the measured temperature to this value.", temperature=True),
    Re72Setting("control_algorithm", "Control algorithm", 4034, "Basic", "PID varies output power continuously; on/off switches fully on and off around the setpoint.", choices=((0, "On/off"), (1, "PID"))),
    Re72Setting("control_action", "Control action", 4035, "Basic", "Use Reverse/heating: power rises below setpoint. Direct/cooling does the opposite.", choices=((0, "Direct/cooling"), (1, "Reverse/heating"))),
    Re72Setting("pid1_pb", "PID 1 proportional band (PB)", 4043, "Basic", "Temperature span over which proportional output changes from 0 to 100%. Smaller PB is more aggressive; larger PB is gentler and usually reduces oscillation.", temperature=True),
    Re72Setting("pid1_ti", "PID 1 integral time (TI), s", 4044, "Basic", "Removes persistent offset from setpoint. Smaller TI corrects faster but can overshoot or oscillate; 0 disables integral action."),
    Re72Setting("pid1_td", "PID 1 derivative time (TD), s", 4045, "Basic", "Anticipates temperature movement and adds damping. Larger TD may reduce overshoot but reacts more to noisy readings; 0 disables derivative action.", divisor=10.0),
    Re72Setting("pid1_y0", "PID 1 manual reset (Y0), %", 4046, "Basic", "Fixed output bias for P or PD control. It shifts the output level and normally has no steady-state role when integral action is enabled.", divisor=10.0),
    Re72Setting("output_1_period", "OUT1 pulse period (TO1), s", 4059, "Basic", "Time-proportioning cycle for the relay. The manual recommends at least 10-20 s for a mechanical relay to reduce wear.", divisor=10.0),
    Re72Setting("output_2_period", "OUT2 pulse period (TO2), s", 4064, "Basic", "Time-proportioning cycle for the 0/5 V SSR drive. The manual recommends about 1-3 s for an external SSR.", divisor=10.0),
    Re72Setting("output_1_assignment", "OUT1 relay function", 4027, "Advanced", "Selects what drives the physical OUT1 relay fitted to model 122100. Choose the required absolute or relative alarm.", choices=_OUTPUT_1_FUNCTIONS),
    Re72Setting("output_1_type", "Output 1 fitted hardware (O1TY)", 4028, "Advanced", "Internally checked hardware type; model 122100 has a relay here.", False, choices=_OUTPUT_TYPES, visible=False),
    Re72Setting("output_2_assignment", "OUT2 SSR-drive function", 4030, "Advanced", "Selects what drives the physical 0/5 V SSR output fitted to model 122100. Choose Heating control for PID heater pulses.", choices=_OUTPUT_2_FUNCTIONS),
    Re72Setting("output_2_type", "Output 2 fitted hardware (O2TY)", 4031, "Advanced", "Internally checked hardware type; model 122100 has a 0/5 V SSR drive here.", False, choices=_OUTPUT_TYPES, visible=False),
    *(
        Re72Setting(f"pid{pid}_pb", f"PID {pid} proportional band (PB)", 4043 + (pid - 1) * 4, "Advanced", "Alternate proportional band, used only when Gain Scheduling selects this PID set. Smaller is more aggressive; larger is gentler.", temperature=True)
        for pid in range(2, 5)
    ),
    *(
        Re72Setting(f"pid{pid}_ti", f"PID {pid} integral time (TI), s", 4044 + (pid - 1) * 4, "Advanced", "Alternate integral time, used only when Gain Scheduling selects this PID set. Smaller corrects offset faster; 0 disables integral action.")
        for pid in range(2, 5)
    ),
    *(
        Re72Setting(f"pid{pid}_td", f"PID {pid} derivative time (TD), s", 4045 + (pid - 1) * 4, "Advanced", "Alternate derivative time, used only when Gain Scheduling selects this PID set. Larger adds damping; 0 disables derivative action.", divisor=10.0)
        for pid in range(2, 5)
    ),
    *(
        Re72Setting(f"pid{pid}_y0", f"PID {pid} manual reset (Y0), %", 4046 + (pid - 1) * 4, "Advanced", "Alternate fixed output bias for P/PD control, used only when Gain Scheduling selects this PID set.", divisor=10.0)
        for pid in range(2, 5)
    ),
    *(
        Re72Setting(
            f"alarm_{alarm}_{key}",
            f"Alarm {alarm} {label.replace('A1', f'A{alarm}')}",
            4065 + (alarm - 1) * 4 + offset,
            "Advanced",
            description,
            temperature=key in {"setpoint", "deviation", "hysteresis"},
            choices=((0, "Disabled"), (1, "Enabled")) if key == "latch" else (),
        )
        for alarm in range(1, 4)
        for offset, (key, label, description) in enumerate(
            (
                ("setpoint", "absolute setpoint (A1SP)", "Temperature threshold used when the output function is an absolute alarm."),
                ("deviation", "relative deviation (A1DV)", "Offset from the active setpoint used when the output function is a relative alarm."),
                ("hysteresis", "hysteresis (A1HY)", "Temperature movement required before an active alarm clears, preventing rapid relay chatter near its threshold."),
                ("latch", "latch (A1LT)", "When enabled, an alarm remains memorised after its condition clears and must be reset manually."),
            )
        )
    ),
    Re72Setting("setpoint_mode", "Setpoint source (SPMD)", 4083, "Advanced", "Selects where the active target comes from.", choices=((0, "SP1 or SP2"), (1, "Soft start, units/min"), (2, "Soft start, units/hour"), (3, "Additional input"), (4, "Programmed control"))),
    Re72Setting("setpoint_2", "Setpoint 2", 4085, "Advanced", "Alternate target selected by the controller's setpoint-source configuration.", temperature=True),
    Re72Setting("setpoint_3", "Setpoint 3", 4086, "Advanced", "Target used by Gain Scheduling or programmed operation when configured.", temperature=True),
    Re72Setting("setpoint_4", "Setpoint 4", 4087, "Advanced", "Target used by Gain Scheduling or programmed operation when configured.", temperature=True),
    Re72Setting("slave_address", "Slave address", 4091, "Advanced", "Protected: changing this can break communication.", False),
    Re72Setting("baud_rate", "Baud rate", 4092, "Advanced", "Read-only here because changing it would immediately break the current connection.", False, choices=((0, "4800 baud"), (1, "9600 baud"), (2, "19200 baud"), (3, "38400 baud"), (4, "57600 baud"))),
    Re72Setting("protocol", "Serial protocol", 4093, "Advanced", "Read-only here because changing it would immediately break the current connection.", False, choices=((0, "Disabled"), (1, "Modbus RTU 8N2"), (2, "Modbus RTU 8E1"), (3, "Modbus RTU 8O1"), (4, "Modbus RTU 8N1"))),
    Re72Setting("autotune_enabled", "Autotune availability", 4099, "Advanced", "Must be Enabled before the controller will start autotuning.", choices=((0, "Locked"), (1, "Enabled"))),
    Re72Setting("autotune_lower", "Autotune lower process limit", 4100, "Advanced", "Expected measured temperature with heater control disabled. Used to validate and calculate autotuning.", temperature=True),
    Re72Setting("autotune_upper", "Autotune upper process limit", 4101, "Advanced", "Expected maximum measured temperature at full heater power. This must safely represent the process.", temperature=True),
)


class LumelRe72(Device, MeasurementSource):
    """Lumel RE72 reached through the ESP32 Modbus RTU bridge."""

    def __init__(self, device_id: str, bus: Esp32Bus, slave: int) -> None:
        if not 1 <= slave <= 247:
            raise ValueError("RE72 slave address must be between 1 and 247")
        self._device_id = device_id
        self._bus = bus
        self._slave = slave
        self._status = DeviceStatus.DISCONNECTED
        self._bus_acquired = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def slave(self) -> int:
        return self._slave

    def connect(self) -> None:
        self._status = DeviceStatus.CONNECTING
        try:
            self._bus.acquire()
            self._bus_acquired = True
            self._bus.read_holding_registers(self._slave, 4003, 1)
        except Exception:
            if self._bus_acquired:
                self._bus.release()
                self._bus_acquired = False
            self._status = DeviceStatus.DISCONNECTED
            raise
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        if self._bus_acquired:
            try:
                self._bus.release()
            finally:
                self._bus_acquired = False
        self._status = DeviceStatus.DISCONNECTED

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        try:
            # 4003..4010 includes status/scaling, alarms, errors, PV, active
            # setpoint and both output signals in one Modbus transaction.
            values = self._bus.read_holding_registers(self._slave, 4003, 8)
            decimal_places = values[0] & 0x03
            temperature_scale = 10 ** decimal_places
            target = self._bus.read_holding_registers(self._slave, 4084, 1)[0]
            measurements = (
                self._measurement("process_value", _signed(values[3]) / temperature_scale, "degC"),
                self._measurement("active_setpoint", _signed(values[5]) / temperature_scale, "degC"),
                self._measurement("target_setpoint", _signed(target) / temperature_scale, "degC"),
                self._measurement("output_1", _signed(values[6]) / 10.0, "%"),
                self._measurement("output_2", _signed(values[7]) / 10.0, "%"),
                self._measurement("alarm_state", values[1], "bitfield"),
                self._measurement("error_status", values[2], "bitfield"),
            )
        except Exception:
            self._status = DeviceStatus.DEGRADED
            raise
        self._status = DeviceStatus.READY
        return measurements

    def set_target_setpoint(self, value: float) -> None:
        """Write a temperature-unit target to register 4084."""
        status = self._bus.read_holding_registers(self._slave, 4003, 1)[0]
        raw = round(float(value) * (10 ** (status & 0x03)))
        if not -32768 <= raw <= 32767:
            raise ValueError("RE72 target setpoint is outside the register range")
        self._bus.write_register(self._slave, 4084, raw & 0xFFFF)

    def read_settings(self) -> dict[str, float | int]:
        """Read the settings exposed by the Settings window."""
        decimal_places = (
            self._bus.read_holding_registers(self._slave, 4003, 1)[0] & 0x03
        )
        scale = 10 ** decimal_places
        ranges = ((4027, 9), (4043, 17), (4064, 13), (4083, 11), (4099, 3))
        registers = {
            address + offset: value
            for address, count in ranges
            for offset, value in enumerate(
                self._bus.read_holding_registers(self._slave, address, count)
            )
        }
        values: dict[str, float | int] = {}
        for setting in RE72_SETTINGS:
            raw = registers[setting.register]
            if setting.temperature:
                values[setting.key] = _signed(raw) / scale
            else:
                values[setting.key] = raw / setting.divisor
        fitted = (values["output_1_type"], values["output_2_type"])
        if fitted != (1.0, 2.0):
            raise RuntimeError(
                "RE72 fitted outputs do not match model 122100: expected "
                f"OUT1 relay and OUT2 0/5 V SSR drive, received codes {fitted}"
            )
        return values

    def read_runtime_state(self) -> dict[str, object]:
        """Return the control state needed to explain which settings are active."""
        values = self._bus.read_holding_registers(self._slave, 4003, 6)
        status = values[0]
        scale = 10 ** (status & 0x03)
        return {
            "mode": "Manual" if status & (1 << 7) else "Automatic",
            "active_pid_set": ((status >> 9) & 0x03) + 1,
            "autotune_active": bool(status & (1 << 8)),
            "autotune_failed": bool(status & (1 << 4)),
            "active_setpoint": _signed(values[5]) / scale,
        }

    def start_autotune(self) -> None:
        """Validate the controller configuration and start SMART PID tuning."""
        algorithm = self._bus.read_holding_registers(self._slave, 4034, 1)[0]
        integral_time = self._bus.read_holding_registers(self._slave, 4044, 1)[0]
        enabled = self._bus.read_holding_registers(self._slave, 4099, 1)[0]
        if algorithm != 1:
            raise ValueError("Control algorithm must be PID before autotuning")
        if integral_time == 0:
            raise ValueError("PID integral time TI must be above zero before autotuning")
        if enabled != 1:
            raise ValueError("Autotune availability is Locked; set it to Enabled first")
        self._bus.write_register(self._slave, 4000, 3)

    def read_full_snapshot(self) -> dict[int, int]:
        """Read the complete non-program controller register range."""
        registers: dict[int, int] = {}
        address = RE72_SNAPSHOT_FIRST_REGISTER
        while address <= RE72_SNAPSHOT_LAST_REGISTER:
            count = min(64, RE72_SNAPSHOT_LAST_REGISTER - address + 1)
            values = self._bus.read_holding_registers(self._slave, address, count)
            registers.update(
                (address + offset, value) for offset, value in enumerate(values)
            )
            address += count
        return registers

    def restore_snapshot(self, saved: dict[int, int]) -> tuple[int, ...]:
        """Restore changed, documented writable settings and verify each write."""
        missing = RE72_RESTORABLE_REGISTERS.difference(saved)
        if missing:
            raise ValueError(
                f"Snapshot is missing restorable registers: {sorted(missing)}"
            )
        current = self.read_full_snapshot()
        changed = tuple(
            address
            for address in sorted(RE72_RESTORABLE_REGISTERS)
            if current[address] != saved[address]
        )
        for address in changed:
            self._bus.write_register(self._slave, address, saved[address])
        verified = self.read_full_snapshot()
        mismatches = [
            address for address in changed if verified[address] != saved[address]
        ]
        if mismatches:
            raise RuntimeError(
                f"Controller did not retain restored registers: {mismatches}"
            )
        return changed

    def write_setting(self, key: str, value: str | float | int) -> None:
        setting = next((item for item in RE72_SETTINGS if item.key == key), None)
        if setting is None:
            raise KeyError(f"Unknown RE72 setting {key!r}")
        if not setting.writable:
            raise ValueError(f"{setting.label} is protected")
        if setting.choices and isinstance(value, str):
            labels = {label: raw for raw, label in setting.choices}
            value = labels.get(value, value)
        if setting.temperature:
            decimal_places = (
                self._bus.read_holding_registers(self._slave, 4003, 1)[0] & 0x03
            )
            raw = round(float(value) * (10 ** decimal_places))
            if not -32768 <= raw <= 32767:
                raise ValueError(f"{setting.label} is outside the register range")
            raw &= 0xFFFF
        else:
            numeric = float(value) * setting.divisor
            if not numeric.is_integer():
                raise ValueError(
                    f"{setting.label} has more precision than the controller supports"
                )
            raw = int(numeric)
            if not 0 <= raw <= 65535:
                raise ValueError(f"{setting.label} must be between 0 and 65535")
        self._bus.write_register(self._slave, setting.register, raw)

    @staticmethod
    def _measurement(name: str, value: float | int, unit: str) -> DeviceMeasurement:
        return DeviceMeasurement(name, Measurement(float(value), unit))


def _signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value
