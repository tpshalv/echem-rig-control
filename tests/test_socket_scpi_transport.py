from collections import deque

import pytest

from rig_control.transports.socket_scpi import SocketScpiTransport


class FakeSocket:
    """Socket substitute that performs no real network communication."""

    def __init__(
        self,
        received_items: list[bytes | BaseException] | None = None,
    ) -> None:
        self.received_items = deque(received_items or [])
        self.sent_messages: list[bytes] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, message: bytes) -> None:
        if self.closed:
            raise OSError("socket is closed")

        self.sent_messages.append(message)

    def recv(self, size: int) -> bytes:
        if self.closed:
            raise OSError("socket is closed")

        if not self.received_items:
            return b""

        item = self.received_items.popleft()

        if isinstance(item, BaseException):
            raise item

        return item

    def close(self) -> None:
        self.closed = True


class FakeSocketFactory:
    def __init__(
        self,
        fake_socket: FakeSocket | None = None,
        error: OSError | None = None,
    ) -> None:
        self.fake_socket = fake_socket or FakeSocket()
        self.error = error
        self.address: tuple[str, int] | None = None
        self.timeout: float | None = None

    def __call__(
        self,
        address: tuple[str, int],
        timeout: float,
    ) -> FakeSocket:
        self.address = address
        self.timeout = timeout

        if self.error is not None:
            raise self.error

        return self.fake_socket


def make_transport(
    fake_socket: FakeSocket | None = None,
) -> tuple[SocketScpiTransport, FakeSocketFactory]:
    factory = FakeSocketFactory(fake_socket)

    transport = SocketScpiTransport(
        host="192.168.1.50",
        port=2268,
        timeout_seconds=2.5,
        socket_factory=factory,
    )

    return transport, factory


def test_empty_host_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        SocketScpiTransport("", 2268)


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_invalid_port_is_rejected(port: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        SocketScpiTransport("192.168.1.50", port)


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        SocketScpiTransport(
            "192.168.1.50",
            2268,
            timeout_seconds=0,
        )


def test_open_uses_configured_address_and_timeout() -> None:
    transport, factory = make_transport()

    transport.open()

    assert transport.is_open is True
    assert factory.address == ("192.168.1.50", 2268)
    assert factory.timeout == pytest.approx(2.5)
    assert factory.fake_socket.timeout == pytest.approx(2.5)


def test_connection_error_contains_instrument_address() -> None:
    factory = FakeSocketFactory(
        error=OSError("network unavailable")
    )
    transport = SocketScpiTransport(
        "192.168.1.50",
        2268,
        socket_factory=factory,
    )

    with pytest.raises(
        ConnectionError,
        match=r"192\.168\.1\.50:2268",
    ):
        transport.open()

    assert transport.is_open is False


def test_open_rejects_second_connection_attempt() -> None:
    transport, _ = make_transport()
    transport.open()

    with pytest.raises(RuntimeError, match="already open"):
        transport.open()


def test_write_adds_single_line_terminator() -> None:
    fake_socket = FakeSocket()
    transport, _ = make_transport(fake_socket)
    transport.open()

    transport.write("OUTP:STAT:IMM OFF\r\n")

    assert fake_socket.sent_messages == [
        b"OUTP:STAT:IMM OFF\n"
    ]


def test_query_reads_response_delivered_in_multiple_chunks() -> None:
    fake_socket = FakeSocket(
        [
            b"Keithley Instruments,",
            b"2260B-30-108,1234567,1.00\r\n",
        ]
    )
    transport, _ = make_transport(fake_socket)
    transport.open()

    response = transport.query("*IDN?")

    assert fake_socket.sent_messages == [b"*IDN?\n"]
    assert response == (
        "Keithley Instruments,2260B-30-108,1234567,1.00"
    )


def test_extra_received_response_is_saved_for_next_query() -> None:
    fake_socket = FakeSocket([b"12.5\n4.2\n"])
    transport, _ = make_transport(fake_socket)
    transport.open()

    first_response = transport.query("MEAS:VOLT:DC?")
    second_response = transport.query("MEAS:CURR:DC?")

    assert first_response == "12.5"
    assert second_response == "4.2"
    assert fake_socket.sent_messages == [
        b"MEAS:VOLT:DC?\n",
        b"MEAS:CURR:DC?\n",
    ]


def test_operations_are_rejected_while_closed() -> None:
    transport, _ = make_transport()

    with pytest.raises(RuntimeError, match="is not open"):
        transport.write("*IDN?")

    with pytest.raises(RuntimeError, match="is not open"):
        transport.query("*IDN?")


def test_timeout_produces_informative_error() -> None:
    fake_socket = FakeSocket(
        [TimeoutError("simulated timeout")]
    )
    transport, _ = make_transport(fake_socket)
    transport.open()

    with pytest.raises(
        TimeoutError,
        match="2.5 seconds",
    ):
        transport.query("*IDN?")


def test_remote_disconnect_produces_informative_error() -> None:
    fake_socket = FakeSocket([b""])
    transport, _ = make_transport(fake_socket)
    transport.open()

    with pytest.raises(
        ConnectionError,
        match="closed the connection",
    ):
        transport.query("*IDN?")


def test_empty_command_is_rejected() -> None:
    fake_socket = FakeSocket()
    transport, _ = make_transport(fake_socket)
    transport.open()

    with pytest.raises(ValueError, match="cannot be empty"):
        transport.write("\r\n")

    assert fake_socket.sent_messages == []


def test_close_releases_socket_and_can_be_called_twice() -> None:
    fake_socket = FakeSocket()
    transport, _ = make_transport(fake_socket)
    transport.open()

    transport.close()
    transport.close()

    assert fake_socket.closed is True
    assert transport.is_open is False