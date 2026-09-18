from rig_control.devices.ohaus_guardian_5000.configuration import GuardianConfiguration, GuardianLimits
from rig_control.devices.ohaus_guardian_5000.protocol import OperatingMode
from rig_control.diagnostics.ohaus_guardian import read_guardian_state
from rig_control.transports.serial_text import SerialTextTransport


class FakeGuardian(SerialTextTransport):
    """Idle e-G52HSRDA fixture; response grammar matches test_ohaus_guardian_5000.py."""

    def __init__(self):
        self._is_open = False
        self.requests = []

    @property
    def is_open(self):
        return self._is_open

    def open(self):
        self._is_open = True

    def close(self):
        self._is_open = False

    def request(self, message):
        assert self.is_open
        self.requests.append(message)
        return {
            "MODEL": "e-G52HSRDA", "SERIAL": "123456", "VERSION": "1.01",
            "MODE": "0", "TARGET_TEMPERATURE": "100.0", "TARGET_SPEED": "500",
            "MEASURED_TEMPERATURE": "95.5", "MEASURED_SPEED": "499",
        }[message]


def test_read_guardian_state_identifies_and_closes_without_stopping():
    transport = FakeGuardian()
    configuration = GuardianConfiguration("hotplate", "COM8", 2.0, GuardianLimits())

    result = read_guardian_state(configuration, transport)

    assert result.identity.model == "e-G52HSRDA"
    assert result.identity.serial_number == "123456"
    assert result.mode is OperatingMode.IDLE
    assert result.temperature == 95.5
    assert result.stir_speed == 499
    assert transport.is_open is False
    assert "STOP_HEAT" not in transport.requests
    assert "STOP_STIR" not in transport.requests
