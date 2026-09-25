from threading import RLock

from rig_control.devices.kamoer_m1_stp.configuration import KamoerM1StpConfiguration
from rig_control.devices.kamoer_m1_stp.protocol import (
    COIL_DIRECTION,
    COIL_PUMP_SWITCH,
    INPUT_FAULT_STATUS,
    REGISTER_SPEED,
    KamoerM1StpProtocol,
    KamoerM1StpProtocolError,
    uint32_to_words,
    words_to_uint32,
)
from rig_control.devices.pump import Pump, PumpDirection, PumpLimits
from rig_control.models import DeviceStatus


class KamoerM1Stp(Pump):
    """M1-STP peristaltic pump driven directly over its own RS485 adapter."""

    def __init__(self, configuration: KamoerM1StpConfiguration, protocol: KamoerM1StpProtocol) -> None:
        self.configuration = configuration
        self._protocol = protocol
        self._status = DeviceStatus.DISCONNECTED
        self._speed_setpoint_rpm: float | None = None
        self._direction: PumpDirection | None = None
        self._running: bool | None = None
        self._lock = RLock()

    @property
    def device_id(self) -> str:
        return self.configuration.device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def limits(self) -> PumpLimits:
        return self.configuration.limits

    @property
    def speed_setpoint_rpm(self) -> float | None:
        return self._speed_setpoint_rpm

    @property
    def direction(self) -> PumpDirection | None:
        return self._direction

    @property
    def running(self) -> bool | None:
        return self._running

    def connect(self) -> None:
        with self._lock:
            if self._protocol.transport.is_open:
                raise RuntimeError(f"Pump {self.device_id!r} is already connected")
            self._status = DeviceStatus.CONNECTING
            try:
                self._protocol.transport.open()
                fault = self._protocol.read_input_registers(INPUT_FAULT_STATUS, 1)[0]
                if fault:
                    raise KamoerM1StpProtocolError(f"Pump reports fault status {fault}")
                self._refresh_state()
                self._status = DeviceStatus.READY
            except Exception:
                try:
                    self._protocol.transport.close()
                finally:
                    self._clear_state()
                raise

    def disconnect(self) -> None:
        with self._lock:
            try:
                if self._protocol.transport.is_open:
                    self.enter_safe_state()
            finally:
                try:
                    self._protocol.transport.close()
                finally:
                    self._clear_state()

    def _refresh_state(self) -> None:
        running, reversed_direction, _mode = self._protocol.read_coils(COIL_PUMP_SWITCH, 3)
        high, low = self._protocol.read_holding_registers(REGISTER_SPEED, 2)
        self._running = running
        self._direction = PumpDirection.REVERSE if reversed_direction else PumpDirection.FORWARD
        self._speed_setpoint_rpm = words_to_uint32(high, low) / 100.0

    def _clear_state(self) -> None:
        self._speed_setpoint_rpm = None
        self._direction = None
        self._running = None
        self._status = DeviceStatus.DISCONNECTED

    def _require_ready(self) -> None:
        if self._status is not DeviceStatus.READY or not self._protocol.transport.is_open:
            raise RuntimeError(f"Pump {self.device_id!r} is not ready")

    def set_speed_rpm(self, rpm: float) -> None:
        with self._lock:
            self._require_ready()
            if isinstance(rpm, bool) or not isinstance(rpm, (int, float)):
                raise TypeError("Speed must be an int or float")
            if rpm < 0:
                raise ValueError("Speed cannot be negative")
            if rpm > self.limits.maximum_speed_rpm:
                raise ValueError(
                    f"Speed {rpm} rpm exceeds configured maximum {self.limits.maximum_speed_rpm} rpm"
                )
            try:
                raw = round(rpm * 100)
                self._protocol.write_registers(REGISTER_SPEED, uint32_to_words(raw))
                high, low = self._protocol.read_holding_registers(REGISTER_SPEED, 2)
                actual = words_to_uint32(high, low)
                if actual != raw:
                    raise KamoerM1StpProtocolError(
                        f"Speed readback {actual / 100.0} rpm differs from requested {rpm} rpm"
                    )
            except Exception:
                self._speed_setpoint_rpm = None
                self._status = DeviceStatus.FAULTED
                raise
            self._speed_setpoint_rpm = raw / 100.0

    def set_direction(self, direction: PumpDirection) -> None:
        with self._lock:
            self._require_ready()
            if not isinstance(direction, PumpDirection):
                raise TypeError("Direction must be a PumpDirection")
            try:
                self._protocol.write_coil(COIL_DIRECTION, direction is PumpDirection.REVERSE)
                (actual,) = self._protocol.read_coils(COIL_DIRECTION, 1)
                if actual is not (direction is PumpDirection.REVERSE):
                    raise KamoerM1StpProtocolError("Direction was not confirmed by the pump")
            except Exception:
                self._direction = None
                self._status = DeviceStatus.FAULTED
                raise
            self._direction = direction

    def start(self) -> None:
        self._switch(True)

    def stop(self) -> None:
        self._switch(False)

    def _switch(self, running: bool) -> None:
        with self._lock:
            self._require_ready()
            try:
                self._protocol.write_coil(COIL_PUMP_SWITCH, running)
                (actual,) = self._protocol.read_coils(COIL_PUMP_SWITCH, 1)
                if actual is not running:
                    raise KamoerM1StpProtocolError("Pump switch was not confirmed by the pump")
            except Exception:
                self._running = None
                self._status = DeviceStatus.FAULTED
                raise
            self._running = running

    def enter_safe_state(self) -> None:
        with self._lock:
            if not self._protocol.transport.is_open:
                self._running = False
                return
            self._protocol.write_coil(COIL_PUMP_SWITCH, False)
            self._running = False

    def read_fault_status(self) -> int:
        with self._lock:
            self._require_ready()
            return self._protocol.read_input_registers(INPUT_FAULT_STATUS, 1)[0]
