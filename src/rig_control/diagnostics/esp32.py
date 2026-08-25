import argparse
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from rig_control.devices.esp32_dht11 import Esp32Dht11
from rig_control.esp32.client import ControllerClient
from rig_control.esp32.session import ControllerSession
from rig_control.esp32.client import ControllerCommandError
from rig_control.esp32.protocol import PROTOCOL_VERSION
from rig_control.rig_profile import RigProfile
from rig_control.transports.pyserial_duplex_text import (
    PySerialDuplexTextTransport,
)


DEFAULT_CONTROLLER_ID = "esp32_main_controller"
DEFAULT_OUTPUT_NAME = "led"
DEFAULT_BAUD_RATE = 115200
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_WATCHDOG_TIMEOUT_SECONDS = 15.0
WATCHDOG_MARGIN_SECONDS = 1.0
DEFAULT_RECOVERY_HOLD_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class Esp32ReadinessResult:
    identity: dict[str, Any]
    status: dict[str, Any]
    sensors: tuple[tuple[str, float, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Esp32DiscoveryResult:
    identity: dict[str, Any]
    capabilities: dict[str, Any]


def discover_esp32(
    port: str,
    baud_rate: int = DEFAULT_BAUD_RATE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Esp32DiscoveryResult:
    """Identify an ESP32 and query its read-only capability description."""

    transport = PySerialDuplexTextTransport(
        port.strip(),
        baud_rate=baud_rate,
        timeout_seconds=timeout_seconds,
    )
    client = ControllerClient(transport)
    transport.connect()
    try:
        identity = client.identify()
        if identity.get("protocol_version") != PROTOCOL_VERSION:
            raise RuntimeError(
                "ESP32 protocol version is incompatible with this application"
            )
        return Esp32DiscoveryResult(identity, client.describe_capabilities())
    finally:
        transport.disconnect()


def read_esp32_state(
    profile: RigProfile,
    device_id: str,
) -> Esp32ReadinessResult:
    """Perform read-only identity/status and optional sensor queries."""

    role = profile.get_role(device_id)
    if role.driver not in {"esp32_json", "esp32_dht11"}:
        raise ValueError(f"Device {device_id!r} is not an ESP32 device")
    if role.connection_id is None:
        raise ValueError(f"ESP32 device {device_id!r} has no connection")
    connection = profile.get_connection(role.connection_id)
    if connection.connection_type != "serial_json":
        raise ValueError("ESP32 readiness checks require a serial_json connection")
    parameters = connection.parameters
    port = parameters.get("port")
    if not isinstance(port, str) or not port.strip():
        raise ValueError("ESP32 serial port is missing")
    baud_rate = parameters.get("baud_rate", DEFAULT_BAUD_RATE)
    timeout = parameters.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(baud_rate, bool) or not isinstance(baud_rate, int):
        raise TypeError("ESP32 baud rate must be an integer")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("ESP32 timeout must be numeric")
    expected_id = parameters.get("controller_id", "")
    if not isinstance(expected_id, str) or not expected_id.strip():
        controller_roles = tuple(
            candidate
            for candidate in profile.enabled_roles
            if candidate.connection_id == role.connection_id
            and candidate.driver == "esp32_json"
        )
        if len(controller_roles) != 1:
            raise ValueError("ESP32 connection has no unambiguous controller ID")
        expected_id = controller_roles[0].device_id

    transport = PySerialDuplexTextTransport(
        port.strip(),
        baud_rate=baud_rate,
        timeout_seconds=float(timeout),
    )
    client = ControllerClient(transport)
    transport.connect()
    try:
        identity = client.identify()
        if identity.get("controller_id") != expected_id:
            raise RuntimeError(
                f"Expected controller {expected_id!r}, received "
                f"{identity.get('controller_id')!r}"
            )
        if identity.get("protocol_version") != PROTOCOL_VERSION:
            raise RuntimeError(
                "ESP32 protocol version is incompatible with this application"
            )
        status = client.get_status()
        sensors: tuple[tuple[str, float, str], ...] = ()
        if role.driver == "esp32_dht11":
            validated = Esp32Dht11._validate_channels(client.read_sensors())
            sensors = tuple(
                (name, float(validated[name]["value"]), str(validated[name]["unit"]))
                for name in ("temperature", "humidity")
            )
        return Esp32ReadinessResult(identity, status, sensors)
    finally:
        transport.disconnect()


class _ControllerClient(Protocol):
    def identify(self) -> dict[str, Any]: ...

    def heartbeat(self) -> None: ...

    def get_status(self) -> dict[str, Any]: ...

    def set_output(self, name: str, enabled: bool) -> None: ...

    def apply_safe_state(self) -> None: ...

    def rearm(self) -> None: ...


class _ControllerSession(Protocol):
    @property
    def client(self) -> _ControllerClient: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...


type SessionFactory = Callable[[str, PySerialDuplexTextTransport], _ControllerSession]


def _print_pass(label: str, details: object | None = None) -> None:
    suffix = f": {details}" if details is not None else ""
    print(f"[PASS] {label}{suffix}")


def _run_step(label: str, action: Callable[[], Any]) -> Any:
    try:
        result = action()
    except Exception as error:
        print(f"[FAIL] {label}: {type(error).__name__}: {error}")
        raise
    _print_pass(label, result)
    return result


def _require_output_state(
    client: _ControllerClient,
    output_name: str,
    expected: bool,
) -> dict[str, Any]:
    status = client.get_status()
    outputs = status.get("outputs")
    if not isinstance(outputs, dict) or outputs.get(output_name) is not expected:
        raise RuntimeError(
            f"Status did not report {output_name!r} as "
            f"{'ON' if expected else 'OFF'}; received {status!r}"
        )
    return status


def _require_watchdog_trip(
    client: _ControllerClient,
    output_name: str,
) -> dict[str, Any]:
    status = _require_output_state(client, output_name, False)
    if status.get("watchdog_tripped") is not True:
        raise RuntimeError(f"Status did not report watchdog_tripped=true: {status!r}")
    if status.get("safe_state_active") is not True:
        raise RuntimeError(f"Status did not report safe_state_active=true: {status!r}")
    return status


def _require_output_rejected(client: _ControllerClient, output_name: str) -> None:
    try:
        client.set_output(output_name, True)
    except ControllerCommandError:
        return
    raise RuntimeError("Controller accepted set_output while watchdog was tripped")


def run_bring_up(
    port: str,
    *,
    controller_id: str = DEFAULT_CONTROLLER_ID,
    output_name: str = DEFAULT_OUTPUT_NAME,
    baud_rate: int = DEFAULT_BAUD_RATE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    watchdog_timeout_seconds: float = DEFAULT_WATCHDOG_TIMEOUT_SECONDS,
    recovery_hold_seconds: float = DEFAULT_RECOVERY_HOLD_SECONDS,
    session_factory: SessionFactory = ControllerSession,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Run the explicit first-hardware checklist and leave the output safe."""

    transport = PySerialDuplexTextTransport(
        port,
        baud_rate=baud_rate,
        timeout_seconds=timeout_seconds,
    )
    session = session_factory(controller_id, transport)
    connected = False
    output_on_attempted = False
    sequence_passed = False

    print("ESP32 hardware bring-up")
    print(f"Port: {port}")
    print(f"Controller: {controller_id}")
    print(f"Output: {output_name}")
    print(f"Serial: {baud_rate} baud, {timeout_seconds:g} second timeout")
    print()

    try:
        _run_step("Connected and verified controller", session.connect)
        connected = True
        client = session.client
        _run_step("Identify", client.identify)
        _run_step("Heartbeat", client.heartbeat)
        _run_step("Initial status", client.get_status)
        _run_step("Rearm", client.rearm)
        output_on_attempted = True
        _run_step(f"Set {output_name} ON", lambda: client.set_output(output_name, True))
        _run_step(
            f"Status reports {output_name} ON",
            lambda: _require_output_state(client, output_name, True),
        )
        _run_step(f"Set {output_name} OFF", lambda: client.set_output(output_name, False))
        output_on_attempted = False
        _run_step("Apply safe state", client.apply_safe_state)

        _run_step("Watchdog test heartbeat", client.heartbeat)
        _run_step("Watchdog test rearm", client.rearm)
        output_on_attempted = True
        _run_step(
            f"Set {output_name} ON for watchdog test",
            lambda: client.set_output(output_name, True),
        )
        wait_seconds = watchdog_timeout_seconds + WATCHDOG_MARGIN_SECONDS
        print(f"[WAIT] Stop heartbeats for {wait_seconds:g} seconds")
        sleep(wait_seconds)
        _run_step(
            "Watchdog forced safe state and latched",
            lambda: _require_watchdog_trip(client, output_name),
        )
        _run_step(
            "Watchdog latch rejects set_output",
            lambda: _require_output_rejected(client, output_name),
        )
        _run_step("Recovery heartbeat", client.heartbeat)
        _run_step(
            "Heartbeat does not clear watchdog latch",
            lambda: _require_watchdog_trip(client, output_name),
        )
        _run_step("Recovery rearm", client.rearm)
        _run_step(
            f"Set {output_name} ON after recovery",
            lambda: client.set_output(output_name, True),
        )
        _run_step(
            f"Status reports {output_name} ON after recovery",
            lambda: _require_output_state(client, output_name, True),
        )
        if recovery_hold_seconds > 0:
            print(f"[WAIT] Hold recovered {output_name} ON for {recovery_hold_seconds:g} seconds")
            sleep(recovery_hold_seconds)
        _run_step(
            f"Set {output_name} OFF after recovery",
            lambda: client.set_output(output_name, False),
        )
        output_on_attempted = False
        sequence_passed = True
    except Exception:
        pass
    finally:
        if connected and output_on_attempted:
            client = session.client
            try:
                client.apply_safe_state()
                _print_pass("Cleanup apply safe state")
            except Exception as error:
                print(
                    "[FAIL] Cleanup apply safe state: "
                    f"{type(error).__name__}: {error}"
                )
        if connected:
            try:
                session.disconnect()
                _print_pass("Disconnect")
            except Exception as error:
                sequence_passed = False
                print(f"[FAIL] Disconnect: {type(error).__name__}: {error}")

    print()
    print("BRING-UP PASSED" if sequence_passed else "BRING-UP FAILED")
    return sequence_passed


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the ESP32 output-control and safety bring-up checklist."
    )
    parser.add_argument("port", help="Windows serial port, for example COM7")
    parser.add_argument("--baud-rate", type=int, default=DEFAULT_BAUD_RATE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--watchdog-timeout",
        type=float,
        default=DEFAULT_WATCHDOG_TIMEOUT_SECONDS,
        help="firmware watchdog timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--recovery-hold",
        type=float,
        default=DEFAULT_RECOVERY_HOLD_SECONDS,
        help="seconds to keep the LED on after watchdog recovery (default: 2)",
    )
    parser.add_argument("--controller-id", default=DEFAULT_CONTROLLER_ID)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_NAME)
    parsed = parser.parse_args(arguments)
    return 0 if run_bring_up(
        parsed.port,
        controller_id=parsed.controller_id,
        output_name=parsed.output,
        baud_rate=parsed.baud_rate,
        timeout_seconds=parsed.timeout,
        watchdog_timeout_seconds=parsed.watchdog_timeout,
        recovery_hold_seconds=parsed.recovery_hold,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
