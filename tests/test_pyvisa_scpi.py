import pytest

from rig_control.transports.pyvisa_scpi import PyVisaScpiTransport


class FakeResource:
    def __init__(self) -> None:
        self.timeout = 0
        self.baud_rate = 0
        self.read_termination = None
        self.write_termination = None
        self.writes = []
        self.closed = False

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query(self, command: str) -> str:
        self.writes.append(command)
        return "KEITHLEY INSTRUMENTS,2260B-30-108,1234,1.00\n"

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self) -> None:
        self.resource = FakeResource()
        self.opened_name = None
        self.closed = False

    def open_resource(self, resource_name: str) -> FakeResource:
        self.opened_name = resource_name
        return self.resource

    def close(self) -> None:
        self.closed = True


def test_visa_transport_configures_and_uses_portable_resource() -> None:
    manager = FakeManager()
    transport = PyVisaScpiTransport(
        "ASRL4::INSTR",
        timeout_seconds=5,
        resource_manager_factory=lambda: manager,
    )

    transport.open()
    response = transport.query("*IDN?\n")
    transport.write("*CLS\n")
    transport.close()

    assert manager.opened_name == "ASRL4::INSTR"
    assert manager.resource.timeout == 5000
    assert manager.resource.baud_rate == 9600
    assert manager.resource.read_termination == "\n"
    assert manager.resource.write_termination == "\n"
    assert manager.resource.writes == ["*IDN?", "*CLS"]
    assert response.startswith("KEITHLEY INSTRUMENTS")
    assert manager.resource.closed is True
    assert manager.closed is True


def test_visa_transport_rejects_empty_resource() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        PyVisaScpiTransport(" ")
