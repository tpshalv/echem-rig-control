from rig_control.devices.base import Device
from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.models import DeviceStatus, Measurement


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

    @staticmethod
    def _measurement(name: str, value: float | int, unit: str) -> DeviceMeasurement:
        return DeviceMeasurement(name, Measurement(float(value), unit))


def _signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value
