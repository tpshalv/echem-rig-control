from dataclasses import replace
import struct

import pytest

from rig_control.device_factory import create_device_manager, DeviceFactoryError
from rig_control.devices.kamoer_m1_stp.configuration import (
    HARDWARE_MAXIMUM_SPEED_RPM,
    KamoerM1StpConfiguration,
    configuration_from_profile,
)
from rig_control.devices.kamoer_m1_stp.driver import (
    CONTINUOUS_RUN_TIME_MS,
    DIRECTION_CHANGE_PAUSE_SECONDS,
    KamoerM1Stp,
)
from rig_control.devices.kamoer_m1_stp.protocol import (
    COIL_DIRECTION,
    COIL_PUMP_SWITCH,
    COIL_RUN_MODE,
    INPUT_FAULT_STATUS,
    INPUT_HARDWARE_VERSION,
    INPUT_SOFTWARE_VERSION,
    REGISTER_SPEED,
    REGISTER_TARGET_LAPS,
    REGISTER_TARGET_TIME_MS,
    KamoerM1StpProtocol,
    KamoerM1StpProtocolError,
    float32_to_words,
    modbus_crc16,
    pack_registers,
    uint32_to_words,
    unpack_registers,
    words_to_float32,
    words_to_uint32,
)
from rig_control.control.commands import CommandSource, SetPumpDirection, SetPumpRunning, SetPumpSpeed
from rig_control.control.service import ControlExecutionError, RigControlService
from rig_control.devices.manager import DeviceManager
from rig_control.devices.pump import Pump, PumpDirection, PumpLimits
from rig_control.models import DeviceStatus
from rig_control.rig_profile import ConnectionDefinition, DeviceCapability, DeviceRole, RigProfile
from rig_control.transports.serial_binary import SerialBinaryTransport


SLAVE = 1


def frame(slave: int, function: int, body: bytes) -> bytes:
    payload = bytes([slave, function]) + body
    return payload + struct.pack("<H", modbus_crc16(payload))


def read_request(function: int, address: int, count: int) -> bytes:
    return frame(SLAVE, function, struct.pack(">HH", address, count))


def read_response(function: int, data: bytes) -> bytes:
    return frame(SLAVE, function, bytes([len(data)]) + data)


def coil_write_request(address: int, value: bool) -> bytes:
    field = b"\xff\x00" if value else b"\x00\x00"
    return frame(SLAVE, 0x05, struct.pack(">H", address) + field)


def register_write_request(address: int, words: tuple[int, ...]) -> bytes:
    data = pack_registers(words)
    body = struct.pack(">HB", len(words), len(data)) + data
    return frame(SLAVE, 0x10, struct.pack(">H", address) + body)


def register_write_response(address: int, count: int) -> bytes:
    return frame(SLAVE, 0x10, struct.pack(">HH", address, count))


class FakeTransport(SerialBinaryTransport):
    def __init__(self):
        self._open = False
        self.responses: dict[bytes, bytes | Exception] = {}
        self.pending = b""
        self.writes: list[bytes] = []
        self.resets = 0

    @property
    def is_open(self):
        return self._open

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def reset_input_buffer(self):
        self.resets += 1
        self.pending = b""

    def write(self, data):
        assert self._open
        self.writes.append(data)
        response = self.responses[data]
        if isinstance(response, Exception):
            raise response
        self.pending = response

    def read(self, size, *, timeout_seconds):
        assert timeout_seconds > 0
        result, self.pending = self.pending[:size], self.pending[size:]
        return result


def make_protocol(transport=None) -> tuple[KamoerM1StpProtocol, FakeTransport]:
    transport = transport or FakeTransport()
    return KamoerM1StpProtocol(transport, SLAVE, timeout_seconds=0.2), transport


def connect_responses(transport, *, fault=0, running=False, reverse=False, mode=False, speed_rpm=0.0):
    transport.responses[read_request(0x04, INPUT_FAULT_STATUS, 1)] = read_response(
        0x04, pack_registers([fault])
    )
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 3)] = read_response(
        0x01, bytes([int(running) | (int(reverse) << 1) | (int(mode) << 2)])
    )
    high, low = uint32_to_words(round(speed_rpm * 100))
    transport.responses[read_request(0x03, REGISTER_SPEED, 2)] = read_response(
        0x03, pack_registers((high, low))
    )


def run_time_responses(transport, *, by_laps=False, target_ms=CONTINUOUS_RUN_TIME_MS):
    """Replies for the run-mode and target-time setup that start() performs."""
    transport.responses[coil_write_request(COIL_RUN_MODE, False)] = coil_write_request(
        COIL_RUN_MODE, False
    )
    transport.responses[
        register_write_request(REGISTER_TARGET_TIME_MS, uint32_to_words(CONTINUOUS_RUN_TIME_MS))
    ] = register_write_response(REGISTER_TARGET_TIME_MS, 2)
    transport.responses[read_request(0x01, COIL_RUN_MODE, 1)] = read_response(
        0x01, bytes([int(by_laps)])
    )
    transport.responses[read_request(0x03, REGISTER_TARGET_TIME_MS, 2)] = read_response(
        0x03, pack_registers(uint32_to_words(target_ms))
    )


def make_device(transport=None, maximum_speed_rpm=350.0):
    protocol, transport = make_protocol(transport)
    configuration = KamoerM1StpConfiguration(
        "pump1", "COM9", SLAVE, 0.2, PumpLimits(maximum_speed_rpm)
    )
    return KamoerM1Stp(configuration, protocol), transport


def profile(**role_changes):
    role = DeviceRole(
        "pump1", "Kamoer M1-STP pump", DeviceCapability.PERISTALTIC_PUMP,
        "kamoer_m1_stp", connection_id="pump_serial",
    )
    return RigProfile("test", "Test", (replace(role, **role_changes),), (
        ConnectionDefinition("pump_serial", "serial_binary", {"port": "COM9", "baud_rate": 9600}),
    ))


# --- Byte order: standard Modbus, confirmed against a real M1-STP on the
# bench (see protocol.py docstring). ---

def test_speed_setpoint_is_sent_high_byte_first():
    # 50 RPM == 5000 == 0x1388. Sent swapped, the pump ran at 348.4 RPM.
    assert pack_registers(uint32_to_words(5000)) == bytes([0x00, 0x00, 0x13, 0x88])


def test_speed_reading_is_decoded_high_byte_first():
    # A pump running at 350 RPM reports 35000 == 0x88B8. Decoded swapped,
    # it showed as 472.4 RPM.
    assert words_to_uint32(*unpack_registers(bytes([0x00, 0x00, 0x88, 0xB8]))) == 35000


def test_uint32_and_float32_word_helpers_round_trip():
    for value in (0, 1, 10000, 0xFFFFFFFF):
        assert words_to_uint32(*uint32_to_words(value)) == value
    for value in (0.0, 100.0, 12.5, -3.25):
        assert words_to_float32(*float32_to_words(value)) == pytest.approx(value)


# --- CRC, verified against a documented frame from a related Kamoer pump
# (Kamoer KCM-ODM), since the M1-STP capture files hold decoded payloads
# only, not full wire frames with a CRC. ---

def test_crc16_matches_documented_kamoer_frame():
    assert modbus_crc16(bytes.fromhex("C0051004FF00")) == 0xEAD9


# --- Protocol transaction tests ---

def test_read_coils_unpacks_bits_lsb_first():
    protocol, transport = make_protocol()
    transport.open()
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 3)] = read_response(0x01, bytes([0b101]))
    assert protocol.read_coils(COIL_PUMP_SWITCH, 3) == (True, False, True)


def test_write_coil_confirms_echo():
    protocol, transport = make_protocol()
    transport.open()
    transport.responses[coil_write_request(COIL_DIRECTION, True)] = coil_write_request(COIL_DIRECTION, True)
    protocol.write_coil(COIL_DIRECTION, True)  # Does not raise.


def test_write_registers_confirms_echoed_quantity():
    protocol, transport = make_protocol()
    transport.open()
    words = uint32_to_words(10000)
    transport.responses[register_write_request(REGISTER_SPEED, words)] = register_write_response(
        REGISTER_SPEED, 2
    )
    protocol.write_registers(REGISTER_SPEED, words)  # Does not raise.


def test_write_rejects_mismatched_echo():
    protocol, transport = make_protocol()
    transport.open()
    request = coil_write_request(COIL_PUMP_SWITCH, True)
    transport.responses[request] = coil_write_request(COIL_PUMP_SWITCH, False)  # Wrong echo
    with pytest.raises(KamoerM1StpProtocolError):
        protocol.write_coil(COIL_PUMP_SWITCH, True)


def test_modbus_exception_reply_is_reported():
    protocol, transport = make_protocol()
    transport.open()
    request = read_request(0x03, REGISTER_SPEED, 2)
    transport.responses[request] = frame(SLAVE, 0x83, bytes([0x02]))
    with pytest.raises(KamoerM1StpProtocolError, match="exception 2"):
        protocol.read_holding_registers(REGISTER_SPEED, 2)


def test_crc_mismatch_is_rejected():
    protocol, transport = make_protocol()
    transport.open()
    request = read_request(0x04, INPUT_SOFTWARE_VERSION, 1)
    good = read_response(0x04, pack_registers([4103]))
    transport.responses[request] = good[:-1] + bytes([good[-1] ^ 0xFF])
    with pytest.raises(KamoerM1StpProtocolError, match="CRC"):
        protocol.read_input_registers(INPUT_SOFTWARE_VERSION, 1)


def test_reply_from_wrong_slave_is_rejected():
    protocol, transport = make_protocol()
    transport.open()
    request = read_request(0x04, INPUT_SOFTWARE_VERSION, 1)
    transport.responses[request] = frame(2, 0x04, bytes([2, 7, 16]))
    with pytest.raises(KamoerM1StpProtocolError, match="slave"):
        protocol.read_input_registers(INPUT_SOFTWARE_VERSION, 1)


def test_incomplete_reply_times_out():
    protocol, transport = make_protocol()
    transport.open()
    request = read_request(0x04, INPUT_SOFTWARE_VERSION, 1)
    transport.responses[request] = read_response(0x04, pack_registers([4103]))[:-1]
    with pytest.raises(TimeoutError):
        protocol.read_input_registers(INPUT_SOFTWARE_VERSION, 1)


# --- Driver tests ---

def test_connect_reads_fault_and_current_state():
    device, transport = make_device()
    connect_responses(transport, fault=0, running=True, reverse=True, speed_rpm=42.5)
    device.connect()
    assert device.status is DeviceStatus.READY
    assert device.running is True
    assert device.direction is PumpDirection.REVERSE
    assert device.speed_setpoint_rpm == pytest.approx(42.5)
    assert isinstance(device, Pump)


def test_connect_rejects_a_faulted_pump():
    device, transport = make_device()
    connect_responses(transport, fault=3)
    with pytest.raises(KamoerM1StpProtocolError):
        device.connect()
    assert device.status is DeviceStatus.DISCONNECTED
    assert not transport.is_open


def test_set_speed_writes_and_verifies_readback():
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    words = uint32_to_words(round(12.34 * 100))
    transport.responses[register_write_request(REGISTER_SPEED, words)] = register_write_response(
        REGISTER_SPEED, 2
    )
    transport.responses[read_request(0x03, REGISTER_SPEED, 2)] = read_response(0x03, pack_registers(words))
    device.set_speed_rpm(12.34)
    assert device.speed_setpoint_rpm == pytest.approx(12.34)


def test_set_speed_readback_mismatch_faults_the_device():
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    requested_words = uint32_to_words(1000)
    transport.responses[register_write_request(REGISTER_SPEED, requested_words)] = register_write_response(
        REGISTER_SPEED, 2
    )
    transport.responses[read_request(0x03, REGISTER_SPEED, 2)] = read_response(
        0x03, pack_registers(uint32_to_words(500))
    )
    with pytest.raises(KamoerM1StpProtocolError):
        device.set_speed_rpm(10.0)
    assert device.status is DeviceStatus.FAULTED
    assert device.speed_setpoint_rpm is None


def test_set_speed_rejects_values_beyond_configured_limit():
    device, transport = make_device(maximum_speed_rpm=100.0)
    connect_responses(transport)
    device.connect()
    with pytest.raises(ValueError):
        device.set_speed_rpm(150.0)
    with pytest.raises(ValueError):
        device.set_speed_rpm(-1.0)


def test_set_direction_and_start_stop():
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    transport.responses[coil_write_request(COIL_DIRECTION, True)] = coil_write_request(
        COIL_DIRECTION, True
    )
    transport.responses[read_request(0x01, COIL_DIRECTION, 1)] = read_response(0x01, bytes([1]))
    device.set_direction(PumpDirection.REVERSE)
    assert device.direction is PumpDirection.REVERSE

    transport.responses[coil_write_request(COIL_PUMP_SWITCH, True)] = coil_write_request(
        COIL_PUMP_SWITCH, True
    )
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 1)] = read_response(0x01, bytes([1]))
    run_time_responses(transport)
    device.start()
    assert device.running is True

    transport.responses[coil_write_request(COIL_PUMP_SWITCH, False)] = coil_write_request(
        COIL_PUMP_SWITCH, False
    )
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 1)] = read_response(0x01, bytes([0]))
    device.stop()
    assert device.running is False


def test_enter_safe_state_stops_the_pump():
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    transport.responses[coil_write_request(COIL_PUMP_SWITCH, False)] = coil_write_request(
        COIL_PUMP_SWITCH, False
    )
    device.enter_safe_state()
    assert device.running is False
    assert transport.writes[-1] == coil_write_request(COIL_PUMP_SWITCH, False)


def test_disconnect_stops_the_pump_and_closes():
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    transport.responses[coil_write_request(COIL_PUMP_SWITCH, False)] = coil_write_request(
        COIL_PUMP_SWITCH, False
    )
    device.disconnect()
    assert not transport.is_open
    assert device.status is DeviceStatus.DISCONNECTED
    assert device.speed_setpoint_rpm is None


# --- Configuration and factory tests ---

@pytest.mark.parametrize("slave", [0, 248, -1, True])
def test_invalid_slave_address_rejected(slave):
    with pytest.raises(ValueError):
        KamoerM1StpConfiguration("pump1", "COM9", slave, 0.2, PumpLimits(100.0))


def test_configured_limit_cannot_exceed_hardware_rating():
    with pytest.raises(ValueError):
        KamoerM1StpConfiguration(
            "pump1", "COM9", SLAVE, 0.2, PumpLimits(HARDWARE_MAXIMUM_SPEED_RPM + 1)
        )


def test_configuration_from_profile_defaults_slave_and_maximum_speed():
    configuration = configuration_from_profile(profile(), "pump1")
    assert configuration.port == "COM9"
    assert configuration.slave == 1
    assert configuration.limits.maximum_speed_rpm == HARDWARE_MAXIMUM_SPEED_RPM


def test_configuration_from_profile_rejects_wrong_baud():
    bad_profile = RigProfile(
        "test", "Test",
        (DeviceRole("pump1", "Pump", DeviceCapability.PERISTALTIC_PUMP, "kamoer_m1_stp",
                    connection_id="pump_serial"),),
        (ConnectionDefinition("pump_serial", "serial_binary", {"port": "COM9", "baud_rate": 19200}),),
    )
    with pytest.raises(ValueError):
        configuration_from_profile(bad_profile, "pump1")


def test_profile_factory_uses_binary_serial_without_connecting(monkeypatch):
    captured = []
    transport = FakeTransport()

    def factory(*args):
        captured.append(args)
        return transport

    monkeypatch.setattr("rig_control.device_factory.PySerialBinaryTransport", factory)
    device = create_device_manager(profile()).get("pump1")
    assert isinstance(device, Pump)
    assert device.status is DeviceStatus.DISCONNECTED
    assert captured == [("COM9", 9600, 2.0)]
    assert not transport.writes


def test_factory_rejects_wrong_capability():
    with pytest.raises(DeviceFactoryError):
        create_device_manager(profile(capability=DeviceCapability.MASS_FLOW_CONTROLLER))


# --- Control service dispatch ---

def make_control_service():
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    manager = DeviceManager()
    manager.register(device)
    return RigControlService(manager), device, transport


def test_control_service_dispatches_pump_speed():
    service, device, transport = make_control_service()
    words = uint32_to_words(round(20.0 * 100))
    transport.responses[register_write_request(REGISTER_SPEED, words)] = register_write_response(
        REGISTER_SPEED, 2
    )
    transport.responses[read_request(0x03, REGISTER_SPEED, 2)] = read_response(0x03, pack_registers(words))
    result = service.execute(SetPumpSpeed("pump1", 20.0, CommandSource.MANUAL))
    assert device.speed_setpoint_rpm == pytest.approx(20.0)
    assert "20 rpm" in result.message


def test_control_service_dispatches_pump_direction():
    service, device, transport = make_control_service()
    transport.responses[coil_write_request(COIL_DIRECTION, True)] = coil_write_request(
        COIL_DIRECTION, True
    )
    transport.responses[read_request(0x01, COIL_DIRECTION, 1)] = read_response(0x01, bytes([1]))
    service.execute(SetPumpDirection("pump1", PumpDirection.REVERSE, CommandSource.MANUAL))
    assert device.direction is PumpDirection.REVERSE


def test_control_service_dispatches_pump_running():
    service, device, transport = make_control_service()
    transport.responses[coil_write_request(COIL_PUMP_SWITCH, True)] = coil_write_request(
        COIL_PUMP_SWITCH, True
    )
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 1)] = read_response(0x01, bytes([1]))
    run_time_responses(transport)
    result = service.execute(SetPumpRunning("pump1", True, CommandSource.MANUAL))
    assert device.running is True
    assert "started" in result.message


def test_control_service_wraps_a_rejected_pump_speed():
    service, device, _transport = make_control_service()
    with pytest.raises(ControlExecutionError):
        service.execute(SetPumpSpeed("pump1", HARDWARE_MAXIMUM_SPEED_RPM + 1, CommandSource.MANUAL))


def test_control_service_enters_pump_safe_state():
    service, device, transport = make_control_service()
    transport.responses[coil_write_request(COIL_PUMP_SWITCH, False)] = coil_write_request(
        COIL_PUMP_SWITCH, False
    )
    result = service.enter_global_safe_state()
    assert result.all_succeeded
    assert device.running is False


def test_start_selects_run_by_time_with_the_longest_target_before_switching_on():
    device, transport = make_device()
    connect_responses(transport, mode=True)  # stored as run-by-laps
    device.connect()
    run_time_responses(transport)
    transport.responses[coil_write_request(COIL_PUMP_SWITCH, True)] = coil_write_request(
        COIL_PUMP_SWITCH, True
    )
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 1)] = read_response(0x01, bytes([1]))

    device.start()

    setup = [
        coil_write_request(COIL_RUN_MODE, False),
        register_write_request(REGISTER_TARGET_TIME_MS, uint32_to_words(CONTINUOUS_RUN_TIME_MS)),
    ]
    switch_on = transport.writes.index(coil_write_request(COIL_PUMP_SWITCH, True))
    assert all(transport.writes.index(write) < switch_on for write in setup)
    assert device.running is True


@pytest.mark.parametrize("by_laps, target_ms", [(True, CONTINUOUS_RUN_TIME_MS), (False, 10000)])
def test_start_refuses_to_switch_on_when_run_time_is_not_confirmed(by_laps, target_ms):
    device, transport = make_device()
    connect_responses(transport)
    device.connect()
    run_time_responses(transport, by_laps=by_laps, target_ms=target_ms)

    with pytest.raises(KamoerM1StpProtocolError):
        device.start()

    assert coil_write_request(COIL_PUMP_SWITCH, True) not in transport.writes
    assert device.status is DeviceStatus.FAULTED


def switch_responses(transport):
    for running in (True, False):
        transport.responses[coil_write_request(COIL_PUMP_SWITCH, running)] = coil_write_request(
            COIL_PUMP_SWITCH, running
        )


def test_direction_change_while_running_stops_pauses_reverses_and_restarts():
    protocol, transport = make_protocol()
    events = []
    device = KamoerM1Stp(
        KamoerM1StpConfiguration("pump1", "COM9", SLAVE, 0.2, PumpLimits(350.0)),
        protocol,
        pause=lambda seconds: events.append(("pause", seconds)),
    )
    connect_responses(transport, running=True)
    device.connect()
    switch_responses(transport)
    run_time_responses(transport)
    transport.responses[coil_write_request(COIL_DIRECTION, True)] = coil_write_request(
        COIL_DIRECTION, True
    )
    transport.responses[read_request(0x01, COIL_DIRECTION, 1)] = read_response(0x01, bytes([1]))
    switch_state = iter((0, 1))
    original_write = transport.write

    def write(data):
        events.append(data)
        original_write(data)
        if data == read_request(0x01, COIL_PUMP_SWITCH, 1):
            transport.pending = read_response(0x01, bytes([next(switch_state)]))

    transport.write = write
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 1)] = b""

    device.set_direction(PumpDirection.REVERSE)

    order = [
        events.index(coil_write_request(COIL_PUMP_SWITCH, False)),
        events.index(("pause", DIRECTION_CHANGE_PAUSE_SECONDS)),
        events.index(coil_write_request(COIL_DIRECTION, True)),
        events.index(coil_write_request(COIL_PUMP_SWITCH, True)),
    ]
    assert order == sorted(order)
    assert device.direction is PumpDirection.REVERSE
    assert device.running is True


def test_direction_change_while_stopped_does_not_start_the_pump():
    device, transport = make_device()
    connect_responses(transport, running=False)
    device.connect()
    transport.responses[coil_write_request(COIL_DIRECTION, True)] = coil_write_request(
        COIL_DIRECTION, True
    )
    transport.responses[read_request(0x01, COIL_DIRECTION, 1)] = read_response(0x01, bytes([1]))

    device.set_direction(PumpDirection.REVERSE)

    assert not any(write[:4] == coil_write_request(COIL_PUMP_SWITCH, True)[:4] for write in transport.writes)
    assert device.running is False


def test_failed_direction_change_leaves_a_running_pump_stopped():
    device, transport = make_device()
    connect_responses(transport, running=True)
    device.connect()
    switch_responses(transport)
    transport.responses[read_request(0x01, COIL_PUMP_SWITCH, 1)] = read_response(0x01, bytes([0]))
    transport.responses[coil_write_request(COIL_DIRECTION, True)] = coil_write_request(
        COIL_DIRECTION, True
    )
    transport.responses[read_request(0x01, COIL_DIRECTION, 1)] = read_response(0x01, bytes([0]))
    device._pause = lambda seconds: None

    with pytest.raises(KamoerM1StpProtocolError):
        device.set_direction(PumpDirection.REVERSE)

    assert coil_write_request(COIL_PUMP_SWITCH, True) not in transport.writes
    assert device.status is DeviceStatus.FAULTED
