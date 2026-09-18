from dataclasses import replace
import struct

import pytest

from rig_control.device_factory import create_device_manager, DeviceFactoryError
from rig_control.devices.tasi_ta612c.configuration import Ta612cConfiguration, configuration_from_profile
from rig_control.devices.tasi_ta612c.driver import Ta612cTemperatureProbe
from rig_control.devices.tasi_ta612c.protocol import (
    CHANNELS, READ_TEMPERATURES, STOP_AND_IDENTIFY, Ta612cProtocol,
    Ta612cProtocolError, decode_temperature, parse_identity, parse_raw_temperatures,
)
from rig_control.devices.temperature_probe import TemperatureProbe
from rig_control.devices.manager import DeviceManager
from rig_control.models import DeviceStatus
from rig_control.polling import PollingService
from rig_control.rig_profile import ConnectionDefinition, DeviceCapability, DeviceRole, RigProfile
from rig_control.transports.serial_binary import SerialBinaryTransport


# Literal examples from TA Series Communication Protocols section V.
IDENTITY = bytes.fromhex("55 AA 00 07 64 02 22 01 8F")
TEMPERATURES = bytes.fromhex("55 AA 01 0B 13 01 0D 01 0C 01 0D 01 48")


def frame(command, payload):
    prefix = b"\x55\xaa" + bytes((command, len(payload) + 3)) + payload
    return prefix + bytes((sum(prefix) & 255,))


class FakeTransport(SerialBinaryTransport):
    def __init__(self, *, chunk_size=64):
        self._open = False
        self.chunk_size = chunk_size
        self.responses = {STOP_AND_IDENTIFY: IDENTITY, READ_TEMPERATURES: TEMPERATURES}
        self.pending = b""
        self.writes = []
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
        count = min(size, self.chunk_size)
        result, self.pending = self.pending[:count], self.pending[count:]
        return result


def make_device(transport=None, channels=CHANNELS):
    transport = transport or FakeTransport()
    return Ta612cTemperatureProbe(
        Ta612cConfiguration("thermocouples", "COM7", channels=channels),
        Ta612cProtocol(transport),
    ), transport


def profile(**role_changes):
    role = DeviceRole("thermocouples", "DANOPLUS DP-373", DeviceCapability.TEMPERATURE_SENSOR,
                      "tasi_ta612c", connection_id="tc_serial",
                      channel_labels={"tc1": "Water bath", "tc2": "Cell"})
    return RigProfile("test", "Test", (replace(role, **role_changes),), (
        ConnectionDefinition("tc_serial", "serial_binary", {"port": "COM7", "baud_rate": 9600}),
    ))


def test_published_protocol_examples():
    identity = parse_identity(IDENTITY)
    assert identity.model_number == 612
    assert identity.firmware_version == "2.90"
    assert tuple(map(decode_temperature, parse_raw_temperatures(TEMPERATURES))) == (27.5, 26.9, 26.8, 26.9)
    assert STOP_AND_IDENTIFY.hex() == "aa55000302"
    assert READ_TEMPERATURES.hex() == "aa55010303"


@pytest.mark.parametrize("broken", [TEMPERATURES[:-1], TEMPERATURES + b"x",
    b"\xaa\x55" + TEMPERATURES[2:], TEMPERATURES[:3] + b"\x0a" + TEMPERATURES[4:],
    TEMPERATURES[:2] + b"\x02" + TEMPERATURES[3:], TEMPERATURES[:-1] + b"\x49"])
def test_rejects_invalid_frames(broken):
    with pytest.raises(Ta612cProtocolError):
        parse_raw_temperatures(broken)


def test_protocol_handles_fragmented_reads_and_traces_exact_bytes():
    transport = FakeTransport(chunk_size=1)
    trace = []
    protocol = Ta612cProtocol(transport, trace=lambda direction, data: trace.append((direction, data)))
    transport.open()
    assert protocol.identify().model_number == 612
    assert trace[0] == ("TX", STOP_AND_IDENTIFY)
    assert b"".join(data for direction, data in trace if direction == "RX") == IDENTITY


def test_timeout_discards_partial_bytes_before_next_request():
    device, transport = make_device()
    device.connect()
    transport.responses[READ_TEMPERATURES] = TEMPERATURES[:6]
    with pytest.raises(TimeoutError):
        device.read_measurements()
    assert device.status is DeviceStatus.DEGRADED
    transport.responses[READ_TEMPERATURES] = TEMPERATURES
    assert len(device.read_measurements()) == 4
    assert device.status is DeviceStatus.READY


def test_generic_probe_and_numeric_identity_independent_of_brand():
    device, transport = make_device()
    transport.responses[STOP_AND_IDENTIFY] = frame(0, struct.pack("<HH", 373, 101))
    device.connect()
    assert isinstance(device, TemperatureProbe)
    assert device.identity.model_number == 373
    assert device.channels == CHANNELS
    readings = device.read_measurements()
    assert [r.channel for r in readings] == list(CHANNELS)
    assert {r.measurement.unit for r in readings} == {"degC"}
    assert len({r.measurement.timestamp for r in readings}) == 1
    assert readings[0].measurement.timestamp.tzinfo is not None
    assert device.read_measurement().value == 27.5


def test_negative_temperature_provisional_twos_complement_decoding():
    assert decode_temperature(65536 - 123) == -12.3
    assert decode_temperature(65536 - 2000) == -200
    assert decode_temperature(13720) == 1372


@pytest.mark.parametrize("raw", [0xffff, 0x7fff, 0x8000, 13721, 65536 - 2001])
def test_missing_or_out_of_range_probe_never_logged_as_valid(raw):
    device, transport = make_device()
    device.connect()
    transport.responses[READ_TEMPERATURES] = frame(1, struct.pack("<4H", 250, raw, 260, 270))
    with pytest.raises(Ta612cProtocolError):
        device.read_measurements()
    assert device.status is DeviceStatus.DEGRADED


def test_unused_disconnected_channels_can_be_excluded():
    device, transport = make_device(channels=("tc1", "tc3"))
    transport.responses[READ_TEMPERATURES] = frame(1, struct.pack("<4H", 250, 0xffff, 300, 0x7fff))
    device.connect()
    assert [(r.channel, r.measurement.value) for r in device.read_measurements()] == [("tc1", 25), ("tc3", 30)]


def test_connect_failure_closes_and_clears_state():
    device, transport = make_device()
    transport.responses[STOP_AND_IDENTIFY] = IDENTITY[:-1]
    with pytest.raises(TimeoutError):
        device.connect()
    assert not transport.is_open
    assert device.identity is None
    assert device.status is DeviceStatus.DISCONNECTED
    with pytest.raises(RuntimeError):
        device.read_measurements()


def test_disconnect_sends_stop_and_closes_even_when_stop_fails():
    device, transport = make_device()
    device.connect()
    transport.responses[STOP_AND_IDENTIFY] = TimeoutError("timeout")
    with pytest.raises(TimeoutError):
        device.disconnect()
    assert transport.writes[-1] == STOP_AND_IDENTIFY
    assert not transport.is_open
    assert device.identity is None
    device.disconnect()  # Idempotent when already closed.


def test_existing_polling_records_stable_ids_and_reports_bad_batches():
    device, transport = make_device()
    device.connect()
    manager = DeviceManager()
    manager.register(device)
    polling = PollingService(manager)
    batch = polling.poll_once()
    assert not batch.failures
    assert [r.channel for r in batch.measurements] == list(CHANNELS)
    transport.responses[READ_TEMPERATURES] = TEMPERATURES[:-1] + b"\x00"
    failed = polling.poll_once()
    assert not failed.measurements
    assert failed.failures[0].device_id == "thermocouples"


def test_profile_factory_uses_binary_serial_without_connecting(monkeypatch):
    captured = []
    transport = FakeTransport()
    def factory(*args):
        captured.append(args)
        return transport
    monkeypatch.setattr("rig_control.device_factory.PySerialBinaryTransport", factory)
    device = create_device_manager(profile()).get("thermocouples")
    assert isinstance(device, TemperatureProbe)
    assert device.status is DeviceStatus.DISCONNECTED
    assert captured == [("COM7", 9600, 2.0)]
    assert not transport.writes
    configuration = configuration_from_profile(profile(settings={"channels": "tc2,tc1"}), "thermocouples")
    assert configuration.channels == ("tc2", "tc1")


@pytest.mark.parametrize("settings", [{"channels": ""}, {"channels": "tc1,tc1"}, {"channels": "tc5"}, {"channels": 2}])
def test_invalid_channels_rejected_before_connection(settings):
    with pytest.raises(DeviceFactoryError):
        create_device_manager(profile(settings=settings))


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        Ta612cConfiguration("probe", "COM7", timeout)


def test_raw_diagnostic_uses_same_driver_and_closes():
    from rig_control.diagnostics.tasi_ta612c import read_probe
    transport = FakeTransport()
    trace = []
    identity, readings = read_probe(Ta612cConfiguration("probe", "COM7"), transport,
                                  trace=lambda direction, data: trace.append((direction, data)))
    assert identity.model_number == 612
    assert len(readings) == 4
    assert not transport.is_open
    assert any(direction == "RX" for direction, _ in trace)
