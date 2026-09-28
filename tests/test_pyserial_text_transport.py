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


class QueuedLinesPort(FakeSerialPort):
    def __init__(self, lines: list[bytes]) -> None:
        super().__init__()
        self.lines = lines

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


def test_a_padding_blank_line_is_not_taken_as_the_next_reply() -> None:
    # Captured from a Guardian G52: SERIAL's reply carries an extra CRLF.
    port = QueuedLinesPort([b"SERIAL A 0110109779810031\r\n", b"\r\n",
                            b"VERSION A V1.01\r\n"])
    transport = PySerialTextTransport("COM9", 9600, 2, line_ending="\r\n",
                                      serial_factory=RecordingFactory(port))
    transport.open()
    assert transport.request("SERIAL") == "SERIAL A 0110109779810031"
    assert transport.request("VERSION") == "VERSION A V1.01"


def test_only_blank_lines_is_reported_as_timeout() -> None:
    port = QueuedLinesPort([b"\r\n"])
    transport = PySerialTextTransport("COM9", 9600, 2, line_ending="\r\n",
                                      serial_factory=RecordingFactory(port))
    transport.open()
    with pytest.raises(TimeoutError):
        transport.request("VERSION")


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


class CarriageReturnPort:
    """A device that ends every reply with a carriage return, as Alicats do.

    read_until() returns at the requested terminator; readline() would block
    until the read timeout because there is no line feed to find.
    """

    def __init__(self, replies: list[bytes]) -> None:
        self.replies = replies
        self.is_open = True
        self.written: list[bytes] = []
        self.terminators: list[bytes] = []
        self.readline_calls = 0

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    def read_until(self, expected: bytes) -> bytes:
        self.terminators.append(expected)
        if not self.replies:
            return b""
        reply = self.replies.pop(0)
        head, separator, _ = reply.partition(expected)
        return head + separator if separator else reply

    def readline(self) -> bytes:
        # Standing in for a blocking wait on a line feed that never arrives.
        self.readline_calls += 1
        return b""


def test_a_carriage_return_reply_is_read_without_waiting_for_a_line_feed():
    port = CarriageReturnPort([b"A +014.81 +021.13 +0.0000 SLPM\r"])
    transport = PySerialTextTransport(
        "COM5", 19200, 1.0, serial_factory=lambda **_: port
    )
    transport.open()

    reply = transport.request("A")

    assert reply == "A +014.81 +021.13 +0.0000 SLPM"
    # The terminator asked for is the one this device was configured with,
    # and readline() -- which would have waited out the timeout -- is unused.
    assert port.terminators == [b"\r"]
    assert port.readline_calls == 0


def test_a_crlf_device_still_reads_whole_lines():
    port = CarriageReturnPort([b"12.5 g\r\n"])
    transport = PySerialTextTransport(
        "COM5", 19200, 1.0, serial_factory=lambda **_: port, line_ending="\r\n"
    )
    transport.open()

    assert transport.request("IP") == "12.5 g"
    assert port.terminators == [b"\r\n"]


def test_a_port_without_read_until_still_works():
    class OlderPort(CarriageReturnPort):
        read_until = None

    port = OlderPort([])
    transport = PySerialTextTransport(
        "COM5", 19200, 1.0, serial_factory=lambda **_: port
    )
    transport.open()

    with pytest.raises(TimeoutError):
        transport.request("A")
    assert port.readline_calls == 1
