import pytest

from rig_control.transports.pyserial_duplex_text import (
    PySerialDuplexTextTransport,
)


class FakeSerialPort:
    def __init__(self, response: bytes = b'{"message":"ok"}\n') -> None:
        self.is_open = True
        self.response = response
        self.writes: list[bytes] = []
        self.flush_count = 0
        self.write_size: int | None = None

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data) if self.write_size is None else self.write_size

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
    response: bytes = b'{"message":"ok"}\n',
) -> tuple[PySerialDuplexTextTransport, FakeSerialPort, RecordingFactory]:
    port = FakeSerialPort(response)
    factory = RecordingFactory(port)
    transport = PySerialDuplexTextTransport(
        "COM7",
        timeout_seconds=1.5,
        serial_factory=factory,
    )
    return transport, port, factory


def test_connect_uses_default_baud_and_configured_8n1_settings() -> None:
    transport, _, factory = make_transport()

    transport.connect()

    assert factory.arguments == {
        "port": "COM7",
        "baudrate": 115200,
        "timeout": 1.5,
        "write_timeout": 1.5,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "xonxoff": False,
        "rtscts": False,
        "dsrdtr": False,
    }


def test_send_adds_newline_and_uses_utf8() -> None:
    transport, port, _ = make_transport()
    transport.connect()

    transport.send('{"label":"temperature °C"}')

    assert port.writes == ['{"label":"temperature °C"}\n'.encode("utf-8")]
    assert port.flush_count == 1


def test_receive_decodes_utf8_and_removes_line_terminator() -> None:
    transport, _, _ = make_transport("status café\r\n".encode())
    transport.connect()

    assert transport.receive() == "status café"


def test_empty_read_is_timeout_with_port_context() -> None:
    transport, _, _ = make_transport(b"")
    transport.connect()

    with pytest.raises(TimeoutError, match="COM7.*1.5 seconds"):
        transport.receive()


def test_read_without_newline_is_reported_as_incomplete() -> None:
    transport, _, _ = make_transport(b"partial JSON")
    transport.connect()

    with pytest.raises(TimeoutError, match="Incomplete message.*COM7.*newline"):
        transport.receive()


def test_invalid_utf8_reports_port() -> None:
    transport, _, _ = make_transport(b"\xff\n")
    transport.connect()

    with pytest.raises(ValueError, match="UTF-8.*COM7"):
        transport.receive()


def test_partial_write_is_rejected() -> None:
    transport, port, _ = make_transport()
    transport.connect()
    port.write_size = 2

    with pytest.raises(OSError, match="Incomplete write.*COM7"):
        transport.send("message")


def test_disconnect_releases_port_and_is_idempotent() -> None:
    transport, port, _ = make_transport()
    transport.connect()

    transport.disconnect()
    transport.disconnect()

    assert port.is_open is False
    assert transport.is_connected is False


@pytest.mark.parametrize("message", ["", "message\r", "message\n"])
def test_invalid_outgoing_message_is_rejected(message: str) -> None:
    transport, _, _ = make_transport()
    transport.connect()

    with pytest.raises(ValueError):
        transport.send(message)


def test_send_and_receive_require_connection() -> None:
    transport, _, _ = make_transport()

    with pytest.raises(RuntimeError, match="COM7.*not connected"):
        transport.send("message")
    with pytest.raises(RuntimeError, match="COM7.*not connected"):
        transport.receive()


@pytest.mark.parametrize(
    ("arguments", "error_type"),
    [
        (("",), ValueError),
        (("COM7", True), TypeError),
        (("COM7", 0), ValueError),
        (("COM7", 115200, True), TypeError),
        (("COM7", 115200, 0), ValueError),
    ],
)
def test_constructor_rejects_invalid_serial_settings(
    arguments: tuple[object, ...],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        PySerialDuplexTextTransport(*arguments)  # type: ignore[arg-type]


def test_connect_rejects_already_connected_port() -> None:
    transport, _, _ = make_transport()
    transport.connect()

    with pytest.raises(RuntimeError, match="COM7.*already connected"):
        transport.connect()
