import pytest

from rig_control.transports.pyserial_text import PySerialTextTransport


class FakeSerialPort:
    def __init__(self, response: bytes = b"A 0.0\r") -> None:
        self.is_open = True
        self.response = response
        self.writes: list[bytes] = []
        self.flush_count = 0

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1

    def readline(self) -> bytes:
        return self.response


class RecordingFactory:
    def __init__(self, serial_port: FakeSerialPort) -> None:
        self.serial_port = serial_port
        self.arguments: dict[str, object] | None = None

    def __call__(self, **kwargs: object) -> FakeSerialPort:
        self.arguments = kwargs
        return self.serial_port


def make_transport(
    response: bytes = b"A 0.0\r",
) -> tuple[PySerialTextTransport, FakeSerialPort, RecordingFactory]:
    port = FakeSerialPort(response)
    factory = RecordingFactory(port)
    transport = PySerialTextTransport(
        "COM5",
        19200,
        1.5,
        serial_factory=factory,
    )
    return transport, port, factory


def test_open_uses_configured_8n1_serial_settings() -> None:
    transport, _, factory = make_transport()

    transport.open()

    assert factory.arguments == {
        "port": "COM5",
        "baudrate": 19200,
        "timeout": 1.5,
        "write_timeout": 1.5,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "xonxoff": False,
        "rtscts": False,
        "dsrdtr": False,
    }


def test_request_adds_carriage_return_and_decodes_response() -> None:
    transport, port, _ = make_transport(b"A 14.7 22.5\r\n")
    transport.open()

    response = transport.request("A")

    assert response == "A 14.7 22.5"
    assert port.writes == [b"A\r"]
    assert port.flush_count == 1


def test_empty_read_is_reported_as_timeout_with_context() -> None:
    transport, _, _ = make_transport(b"")
    transport.open()

    with pytest.raises(TimeoutError) as captured:
        transport.request("A")

    assert "COM5" in str(captured.value)
    assert "1.5 seconds" in str(captured.value)
    assert "'A'" in str(captured.value)


def test_close_releases_port() -> None:
    transport, port, _ = make_transport()
    transport.open()

    transport.close()

    assert port.is_open is False
    assert transport.is_open is False


def test_crlf_and_software_flow_control_for_guardian() -> None:
    port = FakeSerialPort(b"MODEL e-G52HSRDA\r\n")
    factory = RecordingFactory(port)
    transport = PySerialTextTransport("COM8", 9600, 2, serial_factory=factory,
                                      line_ending="\r\n", xonxoff=True)
    transport.open()
    assert transport.request("MODEL") == "MODEL e-G52HSRDA"
    assert port.writes == [b"MODEL\r\n"]
    assert factory.arguments["xonxoff"] is True
    assert factory.arguments["baudrate"] == 9600
    port.response = b"truncated"
    with pytest.raises(TimeoutError, match="Incomplete"):
        transport.request("MODEL")


def test_request_requires_open_port() -> None:
    transport, _, _ = make_transport()

    with pytest.raises(RuntimeError, match="COM5.*not open"):
        transport.request("A")


@pytest.mark.parametrize("message", ["", "   ", "A\r", "A\n"])
def test_invalid_request_is_rejected(message: str) -> None:
    transport, _, _ = make_transport()
    transport.open()

    with pytest.raises(ValueError):
        transport.request(message)
