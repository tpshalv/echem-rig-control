import pytest

from rig_control.device_factory import create_device_manager, DeviceFactoryError
from rig_control.devices.ohaus_guardian_5000 import OhausGuardian5000
from rig_control.devices.ohaus_guardian_5000.configuration import (
    MODEL_SPECS, GuardianLimits, configuration_from_profile, normalize_model,
)
from rig_control.devices.ohaus_guardian_5000.protocol import (
    GuardianProtocol, GuardianProtocolError, OperatingMode, parse_number,
    parse_temperatures, parse_timer,
)
from rig_control.devices.measurement_source import MeasurementSource
from rig_control.devices.safe_state import SafeStateCapable
from rig_control.models import DeviceStatus
from rig_control.rig_profile import ConnectionDefinition, DeviceCapability, DeviceRole, RigProfile
from rig_control.transports.serial_text import SerialTextTransport


class Instrument(SerialTextTransport):
    """Stateful protocol fixture; reply grammar assumptions are documented in README."""

    def __init__(self, model="e-G52HSRDA", mode=0):
        self.model, self.mode = model, mode
        self.temperature, self.speed = 100.0, 500.0
        self.messages = []
        self.failures = {}
        self._is_open = False

    @property
    def is_open(self):
        return self._is_open

    def open(self):
        self._is_open = True

    def close(self):
        self._is_open = False

    def request(self, message):
        assert self.is_open
        self.messages.append(message)
        if message in self.failures:
            failure = self.failures[message]
            if isinstance(failure, Exception):
                raise failure
            return failure
        command, _, argument = message.partition(" ")
        if command in {"TARGET_TEMPERATURE", "TARGET_SPEED"}:
            name = "temperature" if command == "TARGET_TEMPERATURE" else "speed"
            if argument:
                setattr(self, name, float(argument))
                return f"{command} A"
            return str(getattr(self, name))
        modes = {
            "START_HEAT": {0: 1, 3: 4}, "STOP_HEAT": {1: 0, 2: 0, 4: 3, 5: 3},
            "START_STIR": {0: 3, 1: 4, 2: 5}, "STOP_STIR": {3: 0, 4: 1, 5: 2},
        }
        if command in modes:
            self.mode = modes[command].get(self.mode, self.mode)
            return f"{command} A"
        if command in {"TIMER_RESET", "TIMER"} and (argument or command == "TIMER_RESET"):
            return f"{command} A"
        return {
            "MODEL": self.model, "SERIAL": "123456", "VERSION": "1.01",
            "MODE": str(self.mode), "MEASURED_TEMPERATURE": "95.5 80.0" if self.mode in {2, 5} else "95.5",
            "MEASURED_SPEED": "499", "TIMER": "00:10:00",
            "PARAM": "00:10:00,1,0,100,95.5,500,0,0,",
        }[command]


def connected(model="e-G52HSRDA", mode=0, limits=None):
    transport = Instrument(model, mode)
    device = OhausGuardian5000("hotplate", transport, limits)
    device.connect()
    return device, transport


@pytest.mark.parametrize("model,heat,stir,temp", [
    ("e-G52HSRDA", True, True, 360), ("e-G52HS10C", True, True, 500),
    ("e-G52HS07C", True, True, 550), ("e-G52HP07C", True, False, 550),
    ("e-G52ST07C", False, True, None),
])
def test_models_and_capability_specific_queries(model, heat, stir, temp):
    device, transport = connected(model)
    assert device.model_spec.maximum_temperature == temp
    assert device.model_spec.heating is heat
    assert device.model_spec.stirring is stir
    assert device.capabilities == frozenset(n for n, present in (("heating", heat), ("stirring", stir)) if present)
    assert ("TARGET_TEMPERATURE" in transport.messages) is heat
    assert ("TARGET_SPEED" in transport.messages) is stir
    channels = {m.channel for m in device.read_measurements()}
    assert ("temperature" in channels) is heat
    assert ("stir_speed" in channels) is stir
    if not heat:
        with pytest.raises(ValueError, match="does not support"):
            device.start_heating()
    if not stir:
        with pytest.raises(ValueError, match="does not support"):
            device.set_target_speed(500)
    transport.messages.clear()
    device.enter_safe_state()
    assert ("STOP_HEAT" in transport.messages) is heat
    assert ("STOP_STIR" in transport.messages) is stir


@pytest.mark.parametrize("model", [" e-g52hsrda ", "E-G52HSRDA-K1", "e-G52HSRDA 230V EU"])
def test_model_normalisation(model):
    assert normalize_model(model) == "e-G52HSRDA"
    spec = MODEL_SPECS[normalize_model(model)]
    assert (spec.maximum_temperature, spec.minimum_speed, spec.maximum_speed) == (360, 50, 1800)


@pytest.mark.parametrize("model", ["e-G51HSRDA", "e-G52HSRDA-other", "Other", "e-G52HSRDAgarbage"])
def test_unsupported_model_closes_connection(model):
    transport = Instrument(model)
    device = OhausGuardian5000("hotplate", transport)
    with pytest.raises(ValueError, match="Unsupported"):
        device.connect()
    assert not transport.is_open
    assert device.identity is None
    assert device.capabilities == frozenset()
    assert transport.messages == ["MODEL"]


def test_connect_synchronises_actual_state_without_writes():
    device, transport = connected(mode=5)
    assert device.status is DeviceStatus.READY
    assert device.identity.serial_number == "123456"
    assert device.identity.firmware_version == "1.01"
    assert device.target_temperature == 100
    assert device.target_speed == 500
    assert device.heating_enabled and device.stirring_enabled
    assert device.operating_mode is OperatingMode.HEATING_PROBE_AND_STIRRING
    assert transport.messages == ["MODEL", "SERIAL", "VERSION", "MODE", "TARGET_TEMPERATURE", "TARGET_SPEED"]
    assert isinstance(device, MeasurementSource)
    assert isinstance(device, SafeStateCapable)


@pytest.mark.parametrize("method,value", [
    ("set_target_temperature", -1), ("set_target_temperature", 360.5),
    ("set_target_temperature", 100.1), ("set_target_temperature", float("nan")),
    ("set_target_temperature", True), ("set_target_speed", 49),
    ("set_target_speed", 1801), ("set_target_speed", 50.5),
    ("set_target_speed", float("inf")), ("set_target_speed", False),
])
def test_invalid_settings_never_sent(method, value):
    device, transport = connected()
    transport.messages.clear()
    with pytest.raises((ValueError, TypeError)):
        getattr(device, method)(value)
    assert not transport.messages


def test_boundary_setpoints_and_readback():
    device, transport = connected()
    device.set_target_temperature(360)
    device.set_target_speed(50)
    device.set_target_speed(1800)
    assert device.target_temperature == 360
    assert device.target_speed == 1800
    assert "TARGET_TEMPERATURE 360" in transport.messages
    assert "TARGET_SPEED 50" in transport.messages


def test_rig_and_run_limits_do_not_replace_model_limits():
    device, transport = connected(limits=GuardianLimits(200, 1000))
    device.set_run_limits(GuardianLimits(150, 750))
    with pytest.raises(ValueError, match="rig or run"):
        device.set_target_temperature(151)
    with pytest.raises(ValueError, match="rig or run"):
        device.set_target_speed(751)
    assert device.limits.maximum_temperature == 200
    assert device.model_spec.maximum_temperature == 360
    transport.temperature = 160  # Front-panel change must prevent start.
    with pytest.raises(ValueError):
        device.start_heating()
    assert "START_HEAT" not in transport.messages
    device.enter_safe_state()  # Stops bypass limits, including failed state checks.


def test_start_stop_and_safe_state():
    device, transport = connected()
    device.start_heating()
    assert device.heating_enabled and not device.stirring_enabled
    device.start_stirring()
    assert device.heating_enabled and device.stirring_enabled
    device.stop_heating()
    assert not device.heating_enabled and device.stirring_enabled
    device.stop_stirring()
    assert device.operating_mode is OperatingMode.IDLE
    device.start_heating()
    device.start_stirring()
    device.enter_safe_state()
    assert device.operating_mode is OperatingMode.IDLE


def test_safe_state_attempts_stir_stop_after_heat_failure():
    device, transport = connected(mode=4)
    transport.failures["STOP_HEAT"] = TimeoutError("no reply")
    with pytest.raises(ExceptionGroup):
        device.enter_safe_state()
    assert "STOP_STIR" in transport.messages
    assert device.status is DeviceStatus.FAULTED


def test_disconnect_closes_and_clears_even_if_stop_fails():
    device, transport = connected(mode=4)
    transport.failures["STOP_HEAT"] = "L"
    with pytest.raises(ExceptionGroup):
        device.disconnect()
    assert not transport.is_open
    assert device.identity is None
    assert device.target_temperature is None
    assert device.heating_enabled is None


def test_measurements_include_plate_probe_and_rpm():
    device, _ = connected(mode=5)
    assert [(m.channel, m.measurement.value, m.measurement.unit) for m in device.read_measurements()] == [
        ("temperature", 95.5, "degC"), ("probe_temperature", 80.0, "degC"), ("stir_speed", 499, "rpm"),
    ]


def test_error_mode_and_invalid_measurement_are_not_good_data():
    device, transport = connected()
    transport.mode = 99
    with pytest.raises(RuntimeError, match="instrument error"):
        device.read_measurements()
    assert device.heating_enabled is None
    transport.mode = 2
    transport.failures["MEASURED_TEMPERATURE"] = "95"
    with pytest.raises(GuardianProtocolError, match="probe"):
        device.read_measurements()
    assert device.status is DeviceStatus.FAULTED


def test_rejected_setpoint_does_not_cache_success():
    device, transport = connected()
    transport.failures["TARGET_TEMPERATURE 120"] = "L"
    with pytest.raises(GuardianProtocolError):
        device.set_target_temperature(120)
    assert device.target_temperature is None
    assert device.status is DeviceStatus.FAULTED


def test_timer_and_error_readback():
    device, transport = connected()
    assert device.read_timer() == 600
    device.set_timer(3660)
    device.reset_timer()
    assert "TIMER 01:01:00" in transport.messages
    assert "TIMER_RESET" in transport.messages
    assert device.read_error_code() == "0"
    assert "PARAM 0" in transport.messages
    transport.failures["PARAM 0"] = "00:10:00,1,99,100,95.5,500,0,E3,"
    assert device.read_error_code() == "E3"
    assert device.status is DeviceStatus.FAULTED
    for value in (0, 59, 61, 359941):
        with pytest.raises(ValueError):
            device.set_timer(value)


def test_protocol_parsing_and_framing():
    assert parse_temperatures("100.5, 80") == (100.5, 80)
    assert parse_temperatures("100") == (100, None)
    assert parse_timer("99:59:00") == 359940
    assert GuardianProtocol.build("TARGET_TEMPERATURE", "120.5") == "TARGET_TEMPERATURE 120.5"
    with pytest.raises(ValueError):
        GuardianProtocol.build("TIMER", "00:01:00\rSTART_HEAT")
    for text in ("nan", "inf", "abc", "100 C"):
        with pytest.raises(GuardianProtocolError):
            parse_number(text)
    transport = Instrument()
    transport.open()
    protocol = GuardianProtocol(transport)
    transport.failures["MODEL"] = "MODEL e-G52HSRDA"
    assert protocol.query("MODEL") == "e-G52HSRDA"
    transport.failures["MODE"] = "MODE A"
    with pytest.raises(GuardianProtocolError):
        protocol.mode()
    transport.failures["PARAM 0"] = "garbage"
    with pytest.raises(GuardianProtocolError):
        protocol.error_code()


def profile(parameters=None, settings=None):
    return RigProfile("test", "Test", (
        DeviceRole("hotplate", "Hotplate", DeviceCapability.HOTPLATE_STIRRER,
                   "ohaus_guardian_5000", connection_id="hotplate_serial", settings=settings or {}),
    ), (ConnectionDefinition("hotplate_serial", "serial_text", parameters or {"port": "COM8"}),))


def test_profile_factory_constructs_disconnected_device(monkeypatch):
    arguments = {}
    transport = Instrument()
    def factory(*args, **kwargs):
        arguments.update(args=args, kwargs=kwargs)
        return transport
    monkeypatch.setattr("rig_control.device_factory.PySerialTextTransport", factory)
    manager = create_device_manager(profile(settings={"maximum_temperature": 250}))
    device = manager.get("hotplate")
    assert isinstance(device, OhausGuardian5000)
    assert device.status is DeviceStatus.DISCONNECTED
    assert arguments == {"args": ("COM8", 9600, 2.0), "kwargs": {"line_ending": "\r\n", "xonxoff": True}}
    assert device.limits.maximum_temperature == 250
    assert not transport.messages


@pytest.mark.parametrize("parameters", [{"port": ""}, {"port": "COM8", "baud_rate": 19200},
                                        {"port": "COM8", "timeout_seconds": float("nan")}])
def test_invalid_profile(parameters):
    with pytest.raises((ValueError, DeviceFactoryError)):
        create_device_manager(profile(parameters))


def test_profile_connection_overrides():
    configured = configuration_from_profile(profile(), "hotplate")
    assert configured.port == "COM8"


def test_connect_failure_clears_partial_state():
    transport = Instrument()
    transport.failures["TARGET_SPEED"] = TimeoutError("timeout")
    device = OhausGuardian5000("hotplate", transport)
    with pytest.raises(TimeoutError):
        device.connect()
    assert not transport.is_open
    assert device.identity is None
    assert device.target_temperature is None
    assert device.status is DeviceStatus.DISCONNECTED


def test_stop_acknowledgement_is_not_assumed_to_mean_stopped():
    device, transport = connected(mode=4)
    transport.failures["STOP_HEAT"] = "STOP_HEAT A"  # Acknowledged but still running.
    with pytest.raises(GuardianProtocolError, match="not confirmed"):
        device.stop_heating()
    assert device.heating_enabled is None


def test_polling_uses_standard_measurement_records():
    from rig_control.devices.manager import DeviceManager
    from rig_control.polling import PollingService

    device, _ = connected(mode=5)
    manager = DeviceManager()
    manager.register(device)
    batch = PollingService(manager).poll_once()
    assert not batch.failures
    assert [(r.device_id, r.channel) for r in batch.measurements] == [
        ("hotplate", "temperature"), ("hotplate", "probe_temperature"), ("hotplate", "stir_speed"),
    ]
