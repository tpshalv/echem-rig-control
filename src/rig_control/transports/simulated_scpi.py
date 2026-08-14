from collections import defaultdict, deque

from rig_control.transports.scpi import ScpiTransport


class SimulatedScpiTransport(ScpiTransport):
    """Scriptable SCPI connection used without physical hardware."""

    def __init__(self) -> None:
        self._is_open = False
        self._written_commands: list[str] = []
        self._queried_commands: list[str] = []
        self._responses: dict[str, deque[str]] = defaultdict(deque)

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def written_commands(self) -> tuple[str, ...]:
        return tuple(self._written_commands)

    @property
    def queried_commands(self) -> tuple[str, ...]:
        return tuple(self._queried_commands)

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def write(self, command: str) -> None:
        self._require_open()
        self._written_commands.append(command)

    def query(self, command: str) -> str:
        self._require_open()
        self._queried_commands.append(command)

        if not self._responses[command]:
            raise RuntimeError(
                f"No simulated response is queued for {command!r}"
            )

        return self._responses[command].popleft()

    def queue_response(
        self,
        command: str,
        response: str,
    ) -> None:
        """Queue a response for a future matching query."""

        self._responses[command].append(response)

    def _require_open(self) -> None:
        if not self.is_open:
            raise RuntimeError("SCPI transport is not open")