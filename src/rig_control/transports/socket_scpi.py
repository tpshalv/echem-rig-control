import socket
from collections.abc import Callable

from rig_control.transports.scpi import ScpiTransport


SocketFactory = Callable[
    [tuple[str, int], float],
    socket.socket,
]


class SocketScpiTransport(ScpiTransport):
    """Send SCPI commands through a raw TCP socket."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_seconds: float = 5.0,
        socket_factory: SocketFactory = socket.create_connection,
    ) -> None:
        if not host.strip():
            raise ValueError("SCPI host name or IP address cannot be empty")

        if not 1 <= port <= 65535:
            raise ValueError("SCPI port must be between 1 and 65535")

        if timeout_seconds <= 0:
            raise ValueError("SCPI timeout must be greater than zero")

        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._receive_buffer = b""

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    def open(self) -> None:
        if self.is_open:
            raise RuntimeError(
                f"SCPI connection to {self._host}:{self._port} "
                "is already open"
            )

        try:
            connection = self._socket_factory(
                (self._host, self._port),
                self._timeout_seconds,
            )
            connection.settimeout(self._timeout_seconds)
        except OSError as error:
            raise ConnectionError(
                f"Could not connect to SCPI instrument at "
                f"{self._host}:{self._port}: {error}"
            ) from error

        self._socket = connection
        self._receive_buffer = b""

    def close(self) -> None:
        if self._socket is None:
            return

        try:
            self._socket.close()
        finally:
            self._socket = None
            self._receive_buffer = b""

    def write(self, command: str) -> None:
        connection = self._require_open()
        message = self._encode_command(command)

        try:
            connection.sendall(message)
        except OSError as error:
            raise ConnectionError(
                f"Failed to send SCPI command to "
                f"{self._host}:{self._port}: {error}"
            ) from error

    def query(self, command: str) -> str:
        self.write(command)
        return self._read_response()

    def _read_response(self) -> str:
        connection = self._require_open()
        maximum_response_bytes = 1_000_000

        while b"\n" not in self._receive_buffer:
            try:
                chunk = connection.recv(4096)
            except TimeoutError as error:
                raise TimeoutError(
                    f"Timed out waiting for a SCPI response from "
                    f"{self._host}:{self._port} after "
                    f"{self._timeout_seconds} seconds"
                ) from error
            except OSError as error:
                raise ConnectionError(
                    f"Failed while receiving a SCPI response from "
                    f"{self._host}:{self._port}: {error}"
                ) from error

            if not chunk:
                raise ConnectionError(
                    f"SCPI instrument at {self._host}:{self._port} "
                    "closed the connection before completing its response"
                )

            self._receive_buffer += chunk

            if len(self._receive_buffer) > maximum_response_bytes:
                raise RuntimeError(
                    "SCPI response exceeded the maximum permitted size"
                )

        response, self._receive_buffer = self._receive_buffer.split(
            b"\n",
            maxsplit=1,
        )

        try:
            return response.rstrip(b"\r").decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError(
                "SCPI instrument returned a response that was not "
                "valid ASCII text"
            ) from error

    def _require_open(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError(
                f"SCPI connection to {self._host}:{self._port} "
                "is not open"
            )

        return self._socket

    @staticmethod
    def _encode_command(command: str) -> bytes:
        if not isinstance(command, str):
            raise TypeError("SCPI command must be text")

        cleaned_command = command.rstrip("\r\n")

        if not cleaned_command:
            raise ValueError("SCPI command cannot be empty")

        try:
            return f"{cleaned_command}\n".encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError(
                "SCPI command must contain only ASCII characters"
            ) from error