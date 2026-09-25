from collections.abc import Callable
from typing import Protocol

from rig_control.transports.scpi import ScpiTransport


class VisaResource(Protocol):
    timeout: int
    baud_rate: int
    read_termination: str | None
    write_termination: str | None

    def write(self, command: str) -> object: ...
    def query(self, command: str) -> str: ...
    def close(self) -> None: ...


class VisaResourceManager(Protocol):
    def open_resource(self, resource_name: str) -> VisaResource: ...
    def close(self) -> None: ...


type ResourceManagerFactory = Callable[[str], VisaResourceManager]


def _pure_python_resource_manager(backend: str = "@py") -> VisaResourceManager:
    try:
        import pyvisa
    except ImportError as error:
        raise RuntimeError(
            "VISA communication requires the hardware dependencies: "
            "pip install -e .[hardware]"
        ) from error
    return pyvisa.ResourceManager(backend)


class PyVisaScpiTransport(ScpiTransport):
    """SCPI text transport using PyVISA's portable pure-Python backend."""

    def __init__(
        self,
        resource_name: str,
        *,
        timeout_seconds: float = 5.0,
        baud_rate: int = 9600,
        backend: str = "@py",
        resource_manager_factory: ResourceManagerFactory = (
            _pure_python_resource_manager
        ),
    ) -> None:
        if not isinstance(resource_name, str) or not resource_name.strip():
            raise ValueError("VISA resource name cannot be empty")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise TypeError("VISA timeout must be an int or float")
        if timeout_seconds <= 0:
            raise ValueError("VISA timeout must be greater than zero")
        if not isinstance(baud_rate, int) or isinstance(baud_rate, bool):
            raise TypeError("VISA baud rate must be an integer")
        if baud_rate <= 0:
            raise ValueError("VISA baud rate must be greater than zero")
        if backend not in {"@py", "@ni"}:
            raise ValueError("VISA backend must be '@py' or '@ni'")
        self._resource_name = resource_name.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._baud_rate = baud_rate
        self._backend = backend
        self._resource_manager_factory = resource_manager_factory
        self._manager: VisaResourceManager | None = None
        self._resource: VisaResource | None = None

    @property
    def is_open(self) -> bool:
        return self._resource is not None

    def open(self) -> None:
        if self.is_open:
            raise RuntimeError(
                f"VISA resource {self._resource_name!r} is already open"
            )
        try:
            manager = self._resource_manager_factory(self._backend)  # type: ignore[call-arg]
        except TypeError:
            # Preserve injectable zero-argument factories used by existing tests.
            manager = self._resource_manager_factory()
        try:
            resource = manager.open_resource(self._resource_name)
            resource.timeout = round(self._timeout_seconds * 1000)
            resource.baud_rate = self._baud_rate
            resource.write_termination = "\n"
            resource.read_termination = "\n"
        except Exception as error:
            try:
                manager.close()
            except Exception:
                pass
            raise ConnectionError(
                f"Could not open VISA resource {self._resource_name!r} using "
                f"{self._backend}: {error}."
                + (" Install NI-VISA and select the NI-VISA backend for USBTMC."
                   if self._backend == "@ni" else "")
            ) from error
        self._manager = manager
        self._resource = resource

    def close(self) -> None:
        resource, manager = self._resource, self._manager
        self._resource = None
        self._manager = None
        try:
            if resource is not None:
                resource.close()
        finally:
            if manager is not None:
                manager.close()

    def write(self, command: str) -> None:
        resource = self._require_open()
        try:
            resource.write(_clean_command(command))
        except Exception as error:
            raise ConnectionError(
                f"Failed to write to VISA resource {self._resource_name!r}: "
                f"{error}"
            ) from error

    def query(self, command: str) -> str:
        resource = self._require_open()
        try:
            return str(resource.query(_clean_command(command))).strip()
        except Exception as error:
            raise ConnectionError(
                f"Failed to query VISA resource {self._resource_name!r}: "
                f"{error}"
            ) from error

    def _require_open(self) -> VisaResource:
        if self._resource is None:
            raise RuntimeError(
                f"VISA resource {self._resource_name!r} is not open"
            )
        return self._resource


def _clean_command(command: str) -> str:
    if not isinstance(command, str):
        raise TypeError("SCPI command must be text")
    cleaned = command.rstrip("\r\n")
    if not cleaned:
        raise ValueError("SCPI command cannot be empty")
    return cleaned
