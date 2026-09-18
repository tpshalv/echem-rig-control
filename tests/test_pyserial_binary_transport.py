import pytest

from rig_control.transports.pyserial_binary import PySerialBinaryTransport


class Port:
    def __init__(self, **kwargs):
        self.arguments = kwargs
        self.timeout = kwargs["timeout"]
        self.is_open = True
        self.writes = []
        self.read_timeouts = []
        self.response = b"\x00\xff\x0a\x0d"
        self.short_write = False

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self.response = b""

    def write(self, data):
        self.writes.append(data)
        return 1 if self.short_write else len(data)

    def read(self, size):
        self.read_timeouts.append(self.timeout)
        return self.response[:size]


def test_binary_io_preserves_bytes_and_uses_bounded_8n1_no_flow_control():
    ports = []
    def factory(**kwargs):
        ports.append(Port(**kwargs))
        return ports[-1]
    transport = PySerialBinaryTransport("COM7", 9600, 2, serial_factory=factory)
    transport.open()
    port = ports[0]
    assert port.arguments == dict(port="COM7", baudrate=9600, timeout=2, write_timeout=2,
                                 bytesize=8, parity="N", stopbits=1,
                                 xonxoff=False, rtscts=False, dsrdtr=False)
    message = b"\xaa\x55\x01\x03\x03"
    transport.write(message)
    assert port.writes == [message]
    assert transport.read(4, timeout_seconds=0.3) == b"\x00\xff\x0a\x0d"
    assert port.read_timeouts == [0.3]
    assert port.timeout == 2
    transport.reset_input_buffer()
    assert transport.read(4, timeout_seconds=0.1) == b""
    port.short_write = True
    with pytest.raises(OSError):
        transport.write(message)
    transport.close()
    assert not port.is_open
    with pytest.raises(RuntimeError):
        transport.write(message)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_invalid_timeouts(timeout):
    with pytest.raises(ValueError):
        PySerialBinaryTransport("COM7", 9600, timeout)
