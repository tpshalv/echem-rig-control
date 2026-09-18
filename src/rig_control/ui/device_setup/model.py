from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
import re

from rig_control.devices.tasi_ta612c.configuration import (
    Ta612cConfiguration,
    configuration_from_profile as ta612c_configuration_from_profile,
)
from rig_control.diagnostics.tasi_ta612c import read_probe

from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    configuration_from_profile as alicat_configuration_from_profile,
)
from rig_control.devices.ametek_asterion.configuration import (
    AmetekAsterionConfiguration,
    configuration_from_profile as ametek_asterion_configuration_from_profile,
)
from rig_control.devices.ametek_asterion.protocol import (
    AmetekAsterionIdentity,
    AmetekAsterionProtocol,
)
from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
    VisaScpiConfiguration,
    configuration_from_profile as keithley_configuration_from_profile,
)
from rig_control.devices.keithley_2280s.configuration import (
    Keithley2280SConfiguration,
    configuration_from_profile as keithley_2280s_configuration_from_profile,
)
from rig_control.devices.keithley_2280s.protocol import (
    Keithley2280SProtocol,
)
from rig_control.devices.ohaus_guardian_5000.configuration import (
    GuardianConfiguration,
    configuration_from_profile as guardian_configuration_from_profile,
)
from rig_control.diagnostics.ohaus_guardian import (
    GuardianDiagnosticResult,
    read_guardian_state,
)
from rig_control.diagnostics.alicat import (
    AlicatDiagnosticResult,
    DiscoveredAlicat,
    read_alicat_state,
    scan_alicat_bus,
)
from rig_control.diagnostics.esp32 import (
    Esp32DiscoveryResult,
    Esp32ReadinessResult,
    discover_esp32,
    read_esp32_state,
)
from rig_control.devices.keithley_2260b.protocol import (
    Keithley2260BProtocol,
    KeithleyIdentity,
)
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    ExpectedDeviceIdentity,
    RigProfile,
)
from rig_control.rig_profile_writing import write_rig_profile
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.transports.pyvisa_scpi import PyVisaScpiTransport
from rig_control.transports.scpi import ScpiTransport
from rig_control.transports.socket_scpi import SocketScpiTransport


SCPI_POWER_SUPPLY_DRIVERS = {
    "keithley_2260b": {
        "display_name": "Keithley 2260B",
        "connection_prefix": "keithley",
        "manufacturer": "Keithley Instruments",
        "model": "2260B-30-108",
        "default_port": 2268,
        "default_voltage": 30.0,
        "default_current": 108.0,
        "default_power": 1080.0,
    },
    "keithley_2280s": {
        "display_name": "Keithley 2280S-32-6",
        "connection_prefix": "keithley",
        "manufacturer": "Keithley Instruments",
        "model": "2280S-32-6",
        "default_port": 5025,
        "default_voltage": 32.0,
        "default_current": 6.0,
        "default_power": 192.0,
    },
    "ametek_asterion": {
        "display_name": "AMETEK Sorensen Asterion DC",
        "connection_prefix": "ametek_asterion",
        "manufacturer": "AMETEK",
        "model": "Asterion DC",
        "default_port": 5025,
        "default_voltage": 30.0,
        "default_current": 5.0,
        "default_power": 100.0,
    },
}

SCPI_POWER_SUPPLY_DRIVER_LABELS = tuple(
    str(metadata["display_name"])
    for metadata in SCPI_POWER_SUPPLY_DRIVERS.values()
)
SCPI_POWER_SUPPLY_LABEL_TO_DRIVER = {
    str(metadata["display_name"]): driver
    for driver, metadata in SCPI_POWER_SUPPLY_DRIVERS.items()
}


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str
    hardware_id: str


@dataclass(frozen=True, slots=True)
class DeviceReadinessRow:
    device_id: str
    friendly_name: str
    device_type: str
    connection: str
    measurement_interval: str
    readiness: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class EditDeviceRequest:
    device_id: str
    friendly_name: str
    enabled: bool
    required: bool
    system: str
    poll_interval_seconds: float | None
    connection_parameters: dict[str, object]
    device_connection_parameters: dict[str, object]
    settings: dict[str, object]
    channel_labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReadinessCheckResult:
    succeeded: bool
    summary: str
    technical_details: str = ""


@dataclass(frozen=True, slots=True)
class AlicatScanRow:
    address: str
    raw_response: str
    configured_device_id: str | None = None
    configured_kind: str | None = None
    model: str | None = None
    inferred_kind: str | None = None
    inferred_maximum_flow_sccm: float | None = None
    manufacturer_response: str = ""
    data_format_response: str = ""
    firmware_response: str = ""

    @property
    def configuration_status(self) -> str:
        return "Already added" if self.configured_device_id else "New device"


@dataclass(frozen=True, slots=True)
class AddAlicatRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    unit_address: str
    maximum_flow: float = 2000.0
    device_kind: str = "controller"
    flow_unit: str = "SCCM"
    poll_interval_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class AddKeithleyRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    host: str
    port: int = 2268
    timeout_seconds: float = 5.0
    maximum_voltage: float = 30.0
    maximum_current: float = 108.0
    maximum_power: float = 1080.0
    connection_method: str = "ethernet"
    resource_name: str = ""
    visa_baud_rate: int = 9600
    poll_interval_seconds: float = 0.1
    driver: str = "keithley_2260b"


@dataclass(frozen=True, slots=True)
class AddGuardianRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    timeout_seconds: float = 2.0
    maximum_temperature: float | None = None
    maximum_speed: float | None = None
    poll_interval_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class AddTemperatureProbeRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    channels: str = "tc1,tc2,tc3,tc4"
    timeout_seconds: float = 2.0
    poll_interval_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class AddEsp32Request:
    port: str
    baud_rate: int = 115200
    timeout_seconds: float = 2.0
    sensor_poll_interval_seconds: float = 1.5
    heartbeat_interval_seconds: float = 2.0


type SerialPortProvider = Callable[[], tuple[SerialPortInfo, ...]]
type AlicatChecker = Callable[
    [AlicatMfcConfiguration],
    AlicatDiagnosticResult,
]
type AlicatScanner = Callable[[str, int], tuple[DiscoveredAlicat, ...]]
type KeithleyChecker = Callable[
    [Keithley2260BConfiguration | Keithley2280SConfiguration | AmetekAsterionConfiguration],
    KeithleyIdentity | AmetekAsterionIdentity,
]
type GuardianChecker = Callable[
    [GuardianConfiguration],
    GuardianDiagnosticResult,
]
type Esp32Checker = Callable[[RigProfile, str], Esp32ReadinessResult]
type Esp32Scanner = Callable[[str, int, float], Esp32DiscoveryResult]
type ProfileWriter = Callable[[RigProfile, str | Path], Path | None]


def identify_scpi_power_supply(
    configuration: (
        Keithley2260BConfiguration
        | Keithley2280SConfiguration
        | AmetekAsterionConfiguration
    ),
    transport: ScpiTransport | None = None,
) -> KeithleyIdentity | AmetekAsterionIdentity:
    """Connect, request identification, and disconnect without control."""

    driver, protocol = _driver_and_protocol_for_configuration(configuration)
    connection = configuration.connection

    if transport is None:
        if hasattr(connection, "resource_name"):
            transport = PyVisaScpiTransport(
                connection.resource_name,
                timeout_seconds=connection.timeout_seconds,
                baud_rate=connection.baud_rate,
            )
        elif connection.host.strip().upper() == "CHANGE_ME":
            raise ValueError(
                "The power-supply IP address has not been configured. "
                "Replace CHANGE_ME with the instrument's IP address."
            )
        else:
            transport = SocketScpiTransport(
                host=connection.host,
                port=connection.port,
                timeout_seconds=connection.timeout_seconds,
            )

    try:
        transport.open()
        identity = protocol.parse_identity(
            transport.query(protocol.IDENTIFY_QUERY)
        )
    finally:
        transport.close()

    if not _identity_matches_driver(driver, identity):
        display_name = SCPI_POWER_SUPPLY_DRIVERS[driver]["display_name"]
        raise RuntimeError(
            f"The connected instrument did not identify as {display_name}. "
            f"Reported manufacturer={identity.manufacturer!r}, "
            f"model={identity.model!r}, "
            f"serial_number={identity.serial_number!r}."
        )

    return identity


def _driver_and_protocol_for_configuration(
    configuration: (
        Keithley2260BConfiguration
        | Keithley2280SConfiguration
        | AmetekAsterionConfiguration
    ),
) -> tuple[str, object]:
    if isinstance(configuration, Keithley2280SConfiguration):
        return "keithley_2280s", Keithley2280SProtocol
    if isinstance(configuration, AmetekAsterionConfiguration):
        return "ametek_asterion", AmetekAsterionProtocol
    return "keithley_2260b", Keithley2260BProtocol


def _identity_matches_driver(
    driver: str,
    identity: KeithleyIdentity | AmetekAsterionIdentity,
) -> bool:
    manufacturer = identity.manufacturer.upper()
    model = identity.model.upper().removeprefix("MODEL ").strip()
    if driver == "keithley_2260b":
        return "KEITHLEY" in manufacturer and "2260B" in model
    if driver == "keithley_2280s":
        return "KEITHLEY" in manufacturer and model == "2280S-32-6"
    if driver == "ametek_asterion":
        vendor_ok = "AMETEK" in manufacturer or "SORENSEN" in manufacturer
        model_ok = "ASTERION" in model or model.startswith("AST")
        return vendor_ok and model_ok
    return False


def _configuration_from_profile_for_power_supply(
    profile: RigProfile,
    device_id: str,
):
    role = profile.get_role(device_id)
    if role.driver == "keithley_2280s":
        return keithley_2280s_configuration_from_profile(profile, device_id)
    if role.driver == "ametek_asterion":
        return ametek_asterion_configuration_from_profile(profile, device_id)
    return keithley_configuration_from_profile(profile, device_id)


class DeviceSetupViewModel:
    """GUI-independent logic for read-only device setup checks."""

    def __init__(
        self,
        profile: RigProfile,
        *,
        profile_path: str | Path = "device-library.toml",
        serial_port_provider: SerialPortProvider | None = None,
        alicat_checker: AlicatChecker = read_alicat_state,
        alicat_scanner: AlicatScanner = scan_alicat_bus,
        keithley_checker: KeithleyChecker = identify_scpi_power_supply,
        guardian_checker: GuardianChecker = read_guardian_state,
        temperature_probe_checker: Callable = read_probe,
        esp32_checker: Esp32Checker = read_esp32_state,
        esp32_scanner: Esp32Scanner = discover_esp32,
        profile_writer: ProfileWriter = write_rig_profile,
    ) -> None:
        if not isinstance(profile, RigProfile):
            raise TypeError("profile must be a RigProfile")
        self._profile = profile
        self._profile_path = Path(profile_path)
        self._serial_port_provider = (
            serial_port_provider or _list_windows_serial_ports
        )
        self._alicat_checker = alicat_checker
        self._alicat_scanner = alicat_scanner
        self._keithley_checker = keithley_checker
        self._guardian_checker = guardian_checker
        self._temperature_probe_checker = temperature_probe_checker
        self._esp32_checker = esp32_checker
        self._esp32_scanner = esp32_scanner
        self._profile_writer = profile_writer
        self._readiness: dict[str, str] = {}
        self._serial_port_error = ""

    @property
    def serial_port_error(self) -> str:
        return self._serial_port_error

    @property
    def profile(self) -> RigProfile:
        return self._profile

    @property
    def profile_path(self) -> Path:
        return self._profile_path

    def device_rows(self) -> tuple[DeviceReadinessRow, ...]:
        return tuple(
            DeviceReadinessRow(
                device_id=role.device_id,
                friendly_name=role.friendly_name,
                device_type=(
                    f"{role.capability.value} ({role.driver})"
                ),
                connection=self._connection_description(role.device_id),
                measurement_interval=(
                    f"{role.poll_interval_seconds:g} s"
                    if role.poll_interval_seconds is not None
                    else "App default"
                ),
                readiness=self._readiness.get(
                    role.device_id,
                    "not checked",
                ),
                enabled=role.enabled,
            )
            for role in self._profile.device_roles
        )

    def switch_profile(self, path: str | Path) -> ReadinessCheckResult:
        try:
            selected = Path(path)
            profile = load_rig_profile(selected)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The selected rig profile could not be loaded.",
                f"{type(error).__name__}: {error}",
            )
        self._profile = profile
        self._profile_path = selected
        self._readiness.clear()
        return ReadinessCheckResult(
            True,
            f"Loaded rig profile {profile.friendly_name!r} from {selected}.",
        )

    def save_profile_as(self, path: str | Path) -> ReadinessCheckResult:
        try:
            selected = Path(path)
            backup = self._profile_writer(self._profile, selected)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The rig profile could not be saved to the selected file.",
                f"{type(error).__name__}: {error}",
            )
        self._profile_path = selected
        return ReadinessCheckResult(
            True,
            f"Saved rig profile to {selected}."
            + (f" Previous file backed up to {backup}." if backup else ""),
        )

    def update_profile_identity(
        self,
        profile_id: str,
        friendly_name: str,
    ) -> ReadinessCheckResult:
        try:
            selected_id = profile_id.strip()
            selected_name = friendly_name.strip()
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", selected_id):
                raise ValueError(
                    "Internal ID must start with a lowercase letter and use "
                    "only lowercase letters, numbers, underscores or hyphens"
                )
            if not selected_name:
                raise ValueError("Display name cannot be empty")
            candidate = replace(
                self._profile,
                profile_id=selected_id,
                friendly_name=selected_name,
            )
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The profile details were not changed.",
                f"{type(error).__name__}: {error}",
            )
        self._profile = candidate
        return ReadinessCheckResult(
            True,
            "Updated rig profile details."
            + (f" Previous profile backed up to {backup}." if backup else ""),
        )

    def create_new_profile(
        self,
        profile_id: str,
        friendly_name: str,
        path: str | Path,
    ) -> ReadinessCheckResult:
        """Create, save and select a blank rig profile."""

        try:
            selected_id = profile_id.strip()
            selected_name = friendly_name.strip()
            selected_path = Path(path)
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", selected_id):
                raise ValueError(
                    "Internal ID must start with a lowercase letter and use "
                    "only lowercase letters, numbers, underscores or hyphens"
                )
            if not selected_name:
                raise ValueError("Display name cannot be empty")
            if not str(path).strip():
                raise ValueError("Profile file cannot be empty")
            if selected_path.suffix.casefold() != ".toml":
                selected_path = selected_path.with_name(
                    selected_path.name + ".toml"
                )
            selected_path.parent.mkdir(parents=True, exist_ok=True)
            candidate = RigProfile(
                profile_id=selected_id,
                friendly_name=selected_name,
                device_roles=(),
                connections=(),
            )
            backup = self._profile_writer(candidate, selected_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The new rig profile could not be created.",
                f"{type(error).__name__}: {error}",
            )
        self._profile = candidate
        self._profile_path = selected_path
        self._readiness.clear()
        return ReadinessCheckResult(
            True,
            f"Created blank rig profile {selected_name!r} at {selected_path}."
            + (f" Previous file backed up to {backup}." if backup else ""),
        )

    def device_edit_values(self, device_id: str) -> EditDeviceRequest:
        role = self._profile.get_role(device_id)
        connection_values: dict[str, object] = {}
        if role.connection_id is not None:
            connection_values = dict(
                self._profile.get_connection(role.connection_id).parameters
            )
        return EditDeviceRequest(
            role.device_id,
            role.friendly_name,
            role.enabled,
            role.required,
            role.system or "",
            role.poll_interval_seconds,
            connection_values,
            dict(role.connection_parameters),
            dict(role.settings),
            dict(role.channel_labels),
        )

    def update_device(self, request: EditDeviceRequest) -> ReadinessCheckResult:
        try:
            role = self._profile.get_role(request.device_id)
            friendly_name = request.friendly_name.strip()
            if not friendly_name:
                raise ValueError("Device friendly name cannot be empty")
            system = request.system.strip() or None
            updated_role = replace(
                role,
                friendly_name=friendly_name,
                enabled=request.enabled,
                required=request.required,
                system=system,
                poll_interval_seconds=request.poll_interval_seconds,
                connection_parameters=request.device_connection_parameters,
                settings=request.settings,
                channel_labels=request.channel_labels,
            )
            connections = self._profile.connections
            if role.connection_id is not None:
                current = self._profile.get_connection(role.connection_id)
                updated_connection = replace(
                    current,
                    parameters=request.connection_parameters,
                )
                connections = tuple(
                    updated_connection
                    if item.connection_id == current.connection_id
                    else item
                    for item in connections
                )
            candidate = replace(
                self._profile,
                connections=connections,
                device_roles=tuple(
                    updated_role if item.device_id == role.device_id else item
                    for item in self._profile.device_roles
                ),
            )
            self._validate_device_configuration(candidate, role.device_id)
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                f"Device {request.device_id!r} was not changed.",
                f"{type(error).__name__}: {error}",
            )
        self._profile = candidate
        self._readiness.pop(role.device_id, None)
        return ReadinessCheckResult(
            True,
            f"Updated device {role.device_id!r}. Changes take effect when "
            "Device Setup is closed."
            + (f" Previous profile backed up to {backup}." if backup else ""),
        )

    @staticmethod
    def _validate_device_configuration(
        profile: RigProfile,
        device_id: str,
    ) -> None:
        role = profile.get_role(device_id)
        validation_profile = profile
        if not role.enabled:
            enabled_role = replace(role, enabled=True)
            validation_profile = replace(
                profile,
                device_roles=tuple(
                    enabled_role if item.device_id == device_id else item
                    for item in profile.device_roles
                ),
            )
        if role.driver == "tasi_ta612c":
            ta612c_configuration_from_profile(validation_profile, device_id)
        elif role.driver == "alicat":
            alicat_configuration_from_profile(validation_profile, device_id)
        elif role.driver in SCPI_POWER_SUPPLY_DRIVERS:
            _configuration_from_profile_for_power_supply(
                validation_profile,
                device_id,
            )
        elif role.driver in {"esp32_json", "esp32_dht11"}:
            if role.connection_id is None:
                raise ValueError("ESP32 device requires a connection")
            connection = profile.get_connection(role.connection_id)
            port = connection.parameters.get("port")
            baud_rate = connection.parameters.get("baud_rate")
            timeout = connection.parameters.get("timeout_seconds")
            if not isinstance(port, str) or not port.strip():
                raise ValueError("ESP32 COM port cannot be empty")
            if (
                not isinstance(baud_rate, int)
                or isinstance(baud_rate, bool)
                or baud_rate <= 0
            ):
                raise ValueError("ESP32 baud rate must be a positive integer")
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or timeout <= 0
            ):
                raise ValueError("ESP32 timeout must be positive")

    def remove_device(self, device_id: str) -> ReadinessCheckResult:
        try:
            role = self._profile.get_role(device_id)
            remaining = tuple(
                item for item in self._profile.device_roles
                if item.device_id != device_id
            )
            connection_still_used = any(
                item.connection_id == role.connection_id for item in remaining
            )
            connections = (
                self._profile.connections
                if role.connection_id is None or connection_still_used
                else tuple(
                    item for item in self._profile.connections
                    if item.connection_id != role.connection_id
                )
            )
            candidate = replace(
                self._profile,
                device_roles=remaining,
                connections=connections,
            )
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                f"Device {device_id!r} was not removed.",
                f"{type(error).__name__}: {error}",
            )
        self._profile = candidate
        self._readiness.pop(device_id, None)
        return ReadinessCheckResult(
            True,
            f"Removed device {device_id!r}."
            + (f" Previous profile backed up to {backup}." if backup else ""),
        )

    def scan_esp32(self, request: AddEsp32Request) -> Esp32DiscoveryResult:
        port = request.port.strip()
        if not port:
            raise ValueError("ESP32 COM port cannot be empty")
        if request.baud_rate <= 0:
            raise ValueError("ESP32 baud rate must be positive")
        if request.timeout_seconds <= 0:
            raise ValueError("ESP32 timeout must be positive")
        return self._esp32_scanner(
            port,
            request.baud_rate,
            request.timeout_seconds,
        )

    def add_discovered_esp32(
        self,
        request: AddEsp32Request,
        discovery: Esp32DiscoveryResult,
        *,
        controller_friendly_name: str | None = None,
        selected_device_names: Mapping[str, str] | None = None,
        selected_device_intervals: Mapping[str, float] | None = None,
    ) -> ReadinessCheckResult:
        """Add a discovered controller and all supported logical devices."""

        try:
            controller_id = discovery.identity.get("controller_id")
            if not isinstance(controller_id, str) or not controller_id.strip():
                raise ValueError("ESP32 identity has no controller ID")
            controller_id = controller_id.strip()
            selected_controller_name = (
                controller_friendly_name.strip()
                if controller_friendly_name is not None
                else f"ESP32 controller ({controller_id})"
            )
            if not selected_controller_name:
                raise ValueError("Controller display name cannot be empty")
            if request.sensor_poll_interval_seconds <= 0:
                raise ValueError("Sensor interval must be positive")
            if request.heartbeat_interval_seconds <= 0:
                raise ValueError("Heartbeat interval must be positive")

            existing_controller = next(
                (
                    role
                    for role in self._profile.device_roles
                    if role.device_id == controller_id
                ),
                None,
            )
            connection: ConnectionDefinition | None
            if existing_controller is not None:
                if (
                    existing_controller.driver != "esp32_json"
                    or existing_controller.connection_id is None
                ):
                    raise ValueError(
                        f"Existing device {controller_id!r} is not a valid ESP32 controller"
                    )
                connection_id = existing_controller.connection_id
                existing_connection = self._profile.get_connection(connection_id)
                configured_port = existing_connection.parameters.get("port")
                if (
                    isinstance(configured_port, str)
                    and configured_port.casefold() != request.port.strip().casefold()
                ):
                    raise ValueError(
                        f"ESP32 controller {controller_id!r} is configured on "
                        f"{configured_port}, not {request.port.strip()}"
                    )
                connection = None
                roles: list[DeviceRole] = []
            else:
                connection_id = f"{controller_id}_serial"
                used_connections = {
                    item.connection_id for item in self._profile.connections
                }
                suffix = 2
                base_connection_id = connection_id
                while connection_id in used_connections:
                    connection_id = f"{base_connection_id}_{suffix}"
                    suffix += 1
                connection = ConnectionDefinition(
                    connection_id=connection_id,
                    connection_type="serial_json",
                    parameters={
                        "port": request.port.strip(),
                        "baud_rate": request.baud_rate,
                        "timeout_seconds": request.timeout_seconds,
                        "controller_id": controller_id,
                    },
                )
                roles = [DeviceRole(
                    device_id=controller_id,
                    friendly_name=selected_controller_name,
                    capability=DeviceCapability.REMOTE_CONTROLLER,
                    driver="esp32_json",
                    backend=DeviceBackend.REAL,
                    required=True,
                    enabled=True,
                    connection_id=connection_id,
                    settings={
                        "controller_id": controller_id,
                        "heartbeat_interval_seconds": (
                            request.heartbeat_interval_seconds
                        ),
                    },
                )]
            devices = discovery.capabilities.get("devices")
            if not isinstance(devices, list):
                raise ValueError("ESP32 capability response has no device list")
            existing_ids = {role.device_id for role in self._profile.device_roles}
            added_sensor_ids: list[str] = []
            unsupported_kinds: list[str] = []
            for item in devices:
                if not isinstance(item, dict):
                    raise TypeError("ESP32 capability devices must be objects")
                kind = item.get("kind")
                if kind not in {"dht11", "lumel_re72"}:
                    unsupported_kinds.append(str(kind or "unknown"))
                    continue
                device_id = item.get("id")
                label = item.get("label", "Discovered ESP32 device")
                if not isinstance(device_id, str) or not device_id.strip():
                    raise ValueError("Discovered ESP32 device has no ID")
                if not isinstance(label, str) or not label.strip():
                    raise ValueError(
                        f"Discovered ESP32 device {device_id!r} has no label"
                    )
                device_id = device_id.strip()
                if (
                    selected_device_names is not None
                    and device_id not in selected_device_names
                ):
                    continue
                selected_label = (
                    selected_device_names[device_id].strip()
                    if selected_device_names is not None
                    else label.strip()
                )
                if not selected_label:
                    raise ValueError(
                        f"Display name for discovered device {device_id!r} "
                        "cannot be empty"
                    )
                interval = (
                    selected_device_intervals[device_id]
                    if selected_device_intervals is not None
                    and device_id in selected_device_intervals
                    else request.sensor_poll_interval_seconds
                )
                if (
                    isinstance(interval, bool)
                    or not isinstance(interval, (int, float))
                    or interval <= 0
                ):
                    raise ValueError(
                        f"Measurement interval for {device_id!r} must be positive"
                    )
                if device_id in existing_ids:
                    continue
                if any(role.device_id == device_id for role in roles):
                    continue
                settings: dict[str, str | int | float | bool] = {
                    "controller_id": controller_id
                }
                if kind == "lumel_re72":
                    slave = item.get("slave")
                    if isinstance(slave, bool) or not isinstance(slave, int):
                        raise ValueError(
                            f"Discovered RE72 {device_id!r} has no integer slave address"
                        )
                    settings["slave"] = slave
                roles.append(
                    DeviceRole(
                        device_id=device_id,
                        friendly_name=selected_label,
                        capability=(
                            DeviceCapability.TEMPERATURE_CONTROLLER
                            if kind == "lumel_re72"
                            else DeviceCapability.TEMPERATURE_SENSOR
                        ),
                        driver=(
                            "lumel_re72"
                            if kind == "lumel_re72"
                            else "esp32_dht11"
                        ),
                        backend=DeviceBackend.REAL,
                        required=True,
                        enabled=True,
                        poll_interval_seconds=float(interval),
                        connection_id=connection_id,
                        settings=settings,
                    )
                )
                added_sensor_ids.append(device_id)
            candidate = replace(
                self._profile,
                connections=(
                    self._profile.connections
                    if connection is None
                    else (*self._profile.connections, connection)
                ),
                device_roles=(*self._profile.device_roles, *roles),
            )
            backup = (
                self._profile_writer(candidate, self._profile_path)
                if roles
                else None
            )
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The discovered ESP32 was not added.",
                f"{type(error).__name__}: {error}",
            )
        self._profile = candidate
        details = (
            f" Added {len(added_sensor_ids)} supported sensor device(s)."
        )
        if unsupported_kinds:
            details += " Unsupported types were left out: " + ", ".join(
                sorted(set(unsupported_kinds))
            ) + "."
        return ReadinessCheckResult(
            True,
            f"Added ESP32 controller {controller_id!r}.{details}"
            + (f" Previous profile backed up to {backup}." if backup else ""),
        )

    def serial_ports(self) -> tuple[SerialPortInfo, ...]:
        try:
            ports = self._serial_port_provider()
        except Exception as error:
            self._serial_port_error = (
                f"{type(error).__name__}: {error}"
            )
            return ()

        if not isinstance(ports, tuple) or any(
            not isinstance(port, SerialPortInfo) for port in ports
        ):
            self._serial_port_error = (
                "Serial-port provider returned invalid information"
            )
            return ()

        self._serial_port_error = ""
        return ports

    def device_poll_interval(self, device_id: str) -> float | None:
        """Return the configured interval, or None for the app default."""

        return self._profile.get_role(device_id).poll_interval_seconds

    def update_measurement_interval(
        self,
        device_id: str,
        interval_seconds: float | None,
    ) -> ReadinessCheckResult:
        """Save a device's acquisition rate without reconnecting hardware."""

        try:
            role = self._profile.get_role(device_id)
            updated_role = replace(
                role,
                poll_interval_seconds=interval_seconds,
            )
            candidate = replace(
                self._profile,
                device_roles=tuple(
                    updated_role if item.device_id == device_id else item
                    for item in self._profile.device_roles
                ),
            )
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                f"Measurement interval was not changed for {device_id!r}.",
                f"{type(error).__name__}: {error}",
            )

        self._profile = candidate
        interval_text = (
            f"{updated_role.poll_interval_seconds:g} seconds"
            if updated_role.poll_interval_seconds is not None
            else "the application default"
        )
        backup_text = (
            f" Previous profile backed up to {backup}." if backup else ""
        )
        return ReadinessCheckResult(
            True,
            f"Set {role.friendly_name!r} measurement interval to "
            f"{interval_text}. The new rate takes effect when Device Setup "
            "is closed."
            + backup_text,
        )

    def scan_alicats(
        self,
        port: str,
        baud_rate: int = 19200,
    ) -> tuple[AlicatScanRow, ...]:
        """Find uniquely addressed Alicats without changing device state."""

        if not isinstance(port, str) or not port.strip():
            raise ValueError("Select or enter an Alicat COM port")
        if not isinstance(baud_rate, int) or isinstance(baud_rate, bool):
            raise TypeError("Alicat baud rate must be an integer")
        if baud_rate <= 0:
            raise ValueError("Alicat baud rate must be greater than zero")
        selected_port = port.strip()
        configured: dict[str, tuple[str, str]] = {}
        for role in self._profile.enabled_roles:
            if role.driver != "alicat" or role.connection_id is None:
                continue
            connection = self._profile.get_connection(role.connection_id)
            configured_port = connection.parameters.get("port")
            address = role.connection_parameters.get("address")
            if (
                isinstance(configured_port, str)
                and configured_port.casefold() == selected_port.casefold()
                and isinstance(address, str)
            ):
                kind = (
                    "Controller"
                    if role.capability is DeviceCapability.MASS_FLOW_CONTROLLER
                    else "Meter"
                )
                configured[address.strip().upper()] = (role.device_id, kind)
        return tuple(
            AlicatScanRow(
                address=device.address,
                raw_response=device.raw_response,
                configured_device_id=configured.get(device.address, (None, None))[0],
                configured_kind=configured.get(device.address, (None, None))[1],
                model=device.model,
                inferred_kind=device.inferred_kind,
                inferred_maximum_flow_sccm=(
                    device.inferred_maximum_flow_sccm
                ),
                manufacturer_response=device.manufacturer_response,
                data_format_response=device.data_format_response,
                firmware_response=device.firmware_response,
            )
            for device in self._alicat_scanner(selected_port, baud_rate)
        )

    def check_device(self, device_id: str) -> ReadinessCheckResult:
        try:
            role = self._profile.get_role(device_id)

            if role.backend is DeviceBackend.SIMULATED:
                result = ReadinessCheckResult(
                    True,
                    f"{role.friendly_name} is simulated; no physical "
                    "connection check is needed.",
                )
            elif role.driver == "alicat":
                result = self._check_alicat(device_id)
            elif role.driver in SCPI_POWER_SUPPLY_DRIVERS:
                result = self._check_keithley(device_id)
            elif role.driver == "ohaus_guardian_5000":
                result = self._check_guardian(device_id)
            elif role.driver == "tasi_ta612c":
                identity, readings = self._temperature_probe_checker(
                    ta612c_configuration_from_profile(self._profile, device_id)
                )
                result = ReadinessCheckResult(
                    True, f"{role.friendly_name} responded using the selected temperature-probe protocol.",
                    f"Identity: {identity}; " + ", ".join(
                        f"{reading.channel}={reading.measurement.value:g} degC" for reading in readings
                    ),
                )
            elif role.driver in {"esp32_json", "esp32_dht11", "lumel_re72"}:
                diagnostic = self._esp32_checker(self._profile, device_id)
                sensor_text = (
                    "; readings "
                    + ", ".join(
                        f"{name}={value:g} {unit}"
                        for name, value, unit in diagnostic.sensors
                    )
                    if diagnostic.sensors
                    else ""
                )
                result = ReadinessCheckResult(
                    True,
                    f"{role.friendly_name} responded correctly{sensor_text}.",
                    f"Identity: {diagnostic.identity}; status: "
                    f"{diagnostic.status}",
                )
            else:
                raise ValueError(
                    f"No read-only readiness check is available for "
                    f"driver {role.driver!r}"
                )

        except Exception as error:
            self._readiness[device_id] = "check failed"
            return ReadinessCheckResult(
                False,
                f"Read-only check failed for {device_id!r}.",
                f"{type(error).__name__}: {error}",
            )

        self._readiness[device_id] = "ready"
        return result

    def add_alicat_and_check(
        self,
        request: AddAlicatRequest,
    ) -> ReadinessCheckResult:
        """Read-only check a proposed Alicat, then save it on success."""

        try:
            candidate = self._profile_with_new_alicat(request)
            configuration = alicat_configuration_from_profile(
                candidate,
                request.device_id.strip(),
            )
            diagnostic = self._alicat_checker(configuration)
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Alicat was not added because its read-only check "
                "or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profile = candidate
        self._readiness[configuration.device_id] = "ready"
        backup_text = (
            f" Previous profile backed up to {backup}." if backup else ""
        )
        return ReadinessCheckResult(
            True,
            f"Added {configuration.friendly_name!r} as "
            f"{configuration.device_id!r} on {configuration.connection.port}, "
            f"address {configuration.unit_address}. Read-only response "
            f"confirmed gas {diagnostic.state.gas or 'not reported'}."
            + backup_text,
            f"Raw response: {diagnostic.raw_response}",
        )

    def add_keithley_and_check(
        self,
        request: AddKeithleyRequest,
    ) -> ReadinessCheckResult:
        """Identify a proposed SCPI power supply, then save it on success."""

        try:
            candidate = self._profile_with_new_keithley(request)
            configuration = _configuration_from_profile_for_power_supply(
                candidate,
                request.device_id.strip(),
            )
            identity = self._keithley_checker(configuration)
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The power supply was not added because its read-only "
                "identification or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profile = candidate
        self._readiness[configuration.device_id] = "ready"
        backup_text = (
            f" Previous profile backed up to {backup}." if backup else ""
        )
        connection_target = (
            configuration.connection.resource_name
            if hasattr(configuration.connection, "resource_name")
            else f"{configuration.connection.host}:"
            f"{configuration.connection.port}"
        )
        return ReadinessCheckResult(
            True,
            f"Added {request.hardware_label.strip()!r} as "
            f"{configuration.device_id!r} at "
            f"{connection_target}. Read-only identity "
            f"confirmed model {identity.model}, serial "
            f"{identity.serial_number}."
            + backup_text,
            f"Manufacturer: {identity.manufacturer}; firmware: "
            f"{identity.firmware_version}",
        )

    def add_guardian_and_check(
        self,
        request: AddGuardianRequest,
    ) -> ReadinessCheckResult:
        """Read-only check a proposed Guardian hotplate, then save it on success."""

        try:
            candidate = self._profile_with_new_guardian(request)
            configuration = guardian_configuration_from_profile(
                candidate,
                request.device_id.strip(),
            )
            diagnostic = self._guardian_checker(configuration)
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Guardian hotplate was not added because its "
                "read-only check or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profile = candidate
        self._readiness[configuration.device_id] = "ready"
        backup_text = (
            f" Previous profile backed up to {backup}." if backup else ""
        )
        hardware_label = candidate.get_role(configuration.device_id).friendly_name
        return ReadinessCheckResult(
            True,
            f"Added {hardware_label!r} as {configuration.device_id!r} on "
            f"{configuration.port}. Read-only query confirmed model "
            f"{diagnostic.identity.model}, serial "
            f"{diagnostic.identity.serial_number}."
            + backup_text,
            f"Firmware: {diagnostic.identity.firmware_version}; mode: "
            f"{diagnostic.mode.name}",
        )

    def add_temperature_probe_and_check(
        self, request: AddTemperatureProbeRequest,
    ) -> ReadinessCheckResult:
        try:
            candidate = self._profile_with_new_temperature_probe(request)
            configuration = ta612c_configuration_from_profile(candidate, request.device_id.strip())
            identity, readings = self._temperature_probe_checker(configuration)
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(False, "The temperature probe was not added because its read-only check or validation failed.", f"{type(error).__name__}: {error}")
        self._profile = candidate
        self._readiness[configuration.device_id] = "ready"
        label = candidate.get_role(configuration.device_id).friendly_name
        values = ", ".join(f"{r.channel}={r.measurement.value:g} degC" for r in readings)
        return ReadinessCheckResult(True, f"Added {label!r} as {configuration.device_id!r} on {configuration.port}.", f"Identity: {identity}; readings: {values}" + (f" Previous profile backed up to {backup}." if backup else ""))

    def _profile_with_new_alicat(
        self,
        request: AddAlicatRequest,
    ) -> RigProfile:
        if not isinstance(request, AddAlicatRequest):
            raise TypeError("request must be an AddAlicatRequest")
        device_id, hardware_label = self._validate_new_device_identity(
            request.device_id,
            request.hardware_label,
        )
        port = request.port.strip()
        if not port:
            raise ValueError("COM port cannot be empty")
        address = request.unit_address.strip().upper()
        if len(address) != 1 or not "A" <= address <= "Z":
            raise ValueError("Alicat address must be one letter A-Z")
        if isinstance(request.maximum_flow, bool) or not isinstance(
            request.maximum_flow,
            (int, float),
        ):
            raise TypeError("Maximum flow must be numeric")
        if request.maximum_flow <= 0:
            raise ValueError("Maximum flow must be greater than zero")
        device_kind = request.device_kind.strip().casefold()
        if device_kind not in {"controller", "meter"}:
            raise ValueError("Alicat device kind must be controller or meter")
        flow_unit = request.flow_unit.strip()
        if not flow_unit:
            raise ValueError("Alicat flow unit cannot be empty")

        connection = self._find_alicat_connection(port)
        connections = self._profile.connections
        if connection is None:
            connection_id = self._unique_connection_id(
                f"alicat_bus_{port}"
            )
            connection = ConnectionDefinition(
                connection_id=connection_id,
                connection_type="serial_text",
                parameters={
                    "port": port,
                    "baud_rate": 19200,
                    "timeout_seconds": 1.0,
                },
            )
            connections += (connection,)

        for role in self._profile.enabled_roles:
            existing_address = role.connection_parameters.get("address")
            if (
                role.driver == "alicat"
                and role.connection_id == connection.connection_id
                and isinstance(existing_address, str)
                and existing_address.strip().upper() == address
            ):
                raise ValueError(
                    f"Address {address!r} is already used by "
                    f"{role.device_id!r} on {port}"
                )

        settings = {
            "maximum_flow": float(request.maximum_flow),
            "flow_unit": flow_unit,
            "volumetric_flow_unit": (
                "LPM" if flow_unit.casefold() == "slpm" else flow_unit
            ),
            "pressure_unit": "psia",
            "temperature_unit": "degC",
            "frame_fields": (
                "absolute_pressure,gas_temperature,volumetric_flow,mass_flow,"
                + ("setpoint," if device_kind == "controller" else "")
                + "gas"
            ),
            "hardware_label": hardware_label,
        }
        purpose = request.purpose_label.strip()
        if purpose:
            settings["purpose_label"] = purpose

        role = DeviceRole(
            device_id=device_id,
            friendly_name=hardware_label,
            capability=(
                DeviceCapability.MASS_FLOW_CONTROLLER
                if device_kind == "controller"
                else DeviceCapability.MASS_FLOW_METER
            ),
            driver="alicat",
            backend=DeviceBackend.REAL,
            required=True,
            enabled=True,
            expected_identity=ExpectedDeviceIdentity(
                manufacturer="Alicat",
            ),
            connection_id=connection.connection_id,
            connection_parameters={"address": address},
            settings=settings,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        return replace(
            self._profile,
            connections=connections,
            device_roles=self._profile.device_roles + (role,),
        )

    def _profile_with_new_keithley(
        self,
        request: AddKeithleyRequest,
    ) -> RigProfile:
        if not isinstance(request, AddKeithleyRequest):
            raise TypeError("request must be an AddKeithleyRequest")
        if request.driver not in SCPI_POWER_SUPPLY_DRIVERS:
            raise ValueError(
                f"Unsupported power-supply driver {request.driver!r}"
            )
        device_id, hardware_label = self._validate_new_device_identity(
            request.device_id,
            request.hardware_label,
        )
        method = request.connection_method.strip().casefold()
        if method not in {"ethernet", "visa"}:
            raise ValueError(
                "Power-supply connection method must be Ethernet or VISA"
            )
        host = request.host.strip()
        resource_name = request.resource_name.strip()
        if method == "visa":
            if not resource_name:
                raise ValueError(
                    "Power-supply VISA resource name cannot be empty"
                )
        else:
            if not host:
                raise ValueError(
                    "Power-supply host name or IP address cannot be empty"
                )
            if not isinstance(request.port, int) or isinstance(request.port, bool):
                raise TypeError("Power-supply port must be an integer")
            if not 1 <= request.port <= 65535:
                raise ValueError("Power-supply port must be between 1 and 65535")

        numeric_limits = {
            "timeout": request.timeout_seconds,
            "maximum voltage": request.maximum_voltage,
            "maximum current": request.maximum_current,
            "maximum power": request.maximum_power,
        }
        if not isinstance(request.visa_baud_rate, int) or isinstance(
            request.visa_baud_rate, bool
        ):
            raise TypeError("Power-supply VISA baud rate must be an integer")
        if request.visa_baud_rate <= 0:
            raise ValueError(
                "Power-supply VISA baud rate must be greater than zero"
            )
        for name, value in numeric_limits.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Power-supply {name} must be numeric")
            if value <= 0:
                raise ValueError(
                    f"Power-supply {name} must be greater than zero"
                )

        for role in self._profile.enabled_roles:
            if (
                role.driver not in SCPI_POWER_SUPPLY_DRIVERS
                or role.connection_id is None
            ):
                continue
            connection = self._profile.get_connection(role.connection_id)
            existing_resource = connection.parameters.get("resource_name")
            existing_host = connection.parameters.get("host")
            existing_port = connection.parameters.get("port")
            duplicate = (
                method == "visa"
                and isinstance(existing_resource, str)
                and existing_resource.casefold() == resource_name.casefold()
            ) or (
                method == "ethernet"
                and
                isinstance(existing_host, str)
                and existing_host.casefold() == host.casefold()
                and existing_port == request.port
            )
            if duplicate:
                raise ValueError(
                    f"Power-supply target "
                    f"{resource_name if method == 'visa' else f'{host}:{request.port}'} "
                    "is already used "
                    f"by {role.device_id!r}"
                )

        target = resource_name if method == "visa" else host
        connection_id = self._unique_connection_id(
            f"{SCPI_POWER_SUPPLY_DRIVERS[request.driver]['connection_prefix']}_{method}_"
            + re.sub(
                r"[^a-z0-9]+",
                "_",
                target.casefold(),
            ).strip("_")
        )
        connection = ConnectionDefinition(
            connection_id=connection_id,
            connection_type=("visa_scpi" if method == "visa" else "socket_scpi"),
            parameters=(
                {
                    "resource_name": resource_name,
                    "baud_rate": request.visa_baud_rate,
                    "timeout_seconds": float(request.timeout_seconds),
                }
                if method == "visa"
                else {
                    "host": host,
                    "port": request.port,
                    "timeout_seconds": float(request.timeout_seconds),
                }
            ),
        )
        settings = {
            "maximum_voltage": float(request.maximum_voltage),
            "maximum_current": float(request.maximum_current),
            "maximum_power": float(request.maximum_power),
            "hardware_label": hardware_label,
        }
        purpose = request.purpose_label.strip()
        if purpose:
            settings["purpose_label"] = purpose

        role = DeviceRole(
            device_id=device_id,
            friendly_name=hardware_label,
            capability=DeviceCapability.DC_POWER_SUPPLY,
            driver=request.driver,
            backend=DeviceBackend.REAL,
            required=True,
            enabled=True,
            expected_identity=ExpectedDeviceIdentity(
                manufacturer=str(
                    SCPI_POWER_SUPPLY_DRIVERS[request.driver]["manufacturer"]
                ),
                model=str(SCPI_POWER_SUPPLY_DRIVERS[request.driver]["model"]),
            ),
            connection_id=connection_id,
            settings=settings,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        return replace(
            self._profile,
            connections=self._profile.connections + (connection,),
            device_roles=self._profile.device_roles + (role,),
        )

    def _profile_with_new_guardian(
        self,
        request: AddGuardianRequest,
    ) -> RigProfile:
        if not isinstance(request, AddGuardianRequest):
            raise TypeError("request must be an AddGuardianRequest")
        device_id, hardware_label = self._validate_new_device_identity(
            request.device_id,
            request.hardware_label,
        )
        port = request.port.strip()
        if not port:
            raise ValueError("Guardian requires a serial port")
        timeout = request.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("Guardian timeout must be numeric")
        if timeout <= 0:
            raise ValueError("Guardian timeout must be greater than zero")
        for name, value in (
            ("maximum temperature", request.maximum_temperature),
            ("maximum speed", request.maximum_speed),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Guardian {name} must be numeric")
            if value <= 0:
                raise ValueError(f"Guardian {name} must be greater than zero")

        for role in self._profile.enabled_roles:
            if role.driver != "ohaus_guardian_5000" or role.connection_id is None:
                continue
            connection = self._profile.get_connection(role.connection_id)
            existing_port = connection.parameters.get("port")
            if (
                isinstance(existing_port, str)
                and existing_port.casefold() == port.casefold()
            ):
                raise ValueError(
                    f"Port {port!r} is already used by {role.device_id!r}"
                )

        connection_id = self._unique_connection_id(f"guardian_serial_{port}")
        connection = ConnectionDefinition(
            connection_id=connection_id,
            connection_type="serial_text",
            parameters={
                "port": port,
                "baud_rate": 9600,
                "timeout_seconds": float(timeout),
            },
        )

        settings: dict[str, object] = {"hardware_label": hardware_label}
        if request.maximum_temperature is not None:
            settings["maximum_temperature"] = float(request.maximum_temperature)
        if request.maximum_speed is not None:
            settings["maximum_speed"] = float(request.maximum_speed)
        purpose = request.purpose_label.strip()
        if purpose:
            settings["purpose_label"] = purpose

        role = DeviceRole(
            device_id=device_id,
            friendly_name=hardware_label,
            capability=DeviceCapability.HOTPLATE_STIRRER,
            driver="ohaus_guardian_5000",
            backend=DeviceBackend.REAL,
            required=True,
            enabled=True,
            expected_identity=ExpectedDeviceIdentity(manufacturer="OHAUS"),
            connection_id=connection_id,
            settings=settings,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        return replace(
            self._profile,
            connections=self._profile.connections + (connection,),
            device_roles=self._profile.device_roles + (role,),
        )

    def _profile_with_new_temperature_probe(self, request: AddTemperatureProbeRequest) -> RigProfile:
        if not isinstance(request, AddTemperatureProbeRequest):
            raise TypeError("request must be an AddTemperatureProbeRequest")
        device_id, hardware_label = self._validate_new_device_identity(request.device_id, request.hardware_label)
        port = request.port.strip()
        if not port:
            raise ValueError("Temperature probe requires a serial port")
        timeout = float(request.timeout_seconds)
        if timeout <= 0:
            raise ValueError("Temperature probe timeout must be positive")
        channels = tuple(c.strip() for c in request.channels.split(","))
        configuration = Ta612cConfiguration(device_id, port, timeout, channels)
        for role in self._profile.enabled_roles:
            if role.connection_id is None:
                continue
            connection = self._profile.get_connection(role.connection_id)
            if role.driver == "tasi_ta612c" and str(connection.parameters.get("port", "")).casefold() == port.casefold():
                raise ValueError(f"Port {port!r} is already used by {role.device_id!r}")
        connection_id = self._unique_connection_id(f"thermocouple_serial_{port}")
        connection = ConnectionDefinition(connection_id, "serial_binary", {"port": port, "baud_rate": 9600, "timeout_seconds": timeout})
        settings = {"channels": ",".join(configuration.channels), "hardware_label": hardware_label}
        if request.purpose_label.strip():
            settings["purpose_label"] = request.purpose_label.strip()
        role = DeviceRole(device_id, hardware_label, DeviceCapability.TEMPERATURE_SENSOR, "tasi_ta612c", connection_id=connection_id, settings=settings, system="Thermal", poll_interval_seconds=request.poll_interval_seconds)
        return replace(self._profile, connections=self._profile.connections + (connection,), device_roles=self._profile.device_roles + (role,))

    def _find_alicat_connection(
        self,
        port: str,
    ) -> ConnectionDefinition | None:
        for connection in self._profile.connections:
            configured_port = connection.parameters.get("port")
            if (
                connection.connection_type == "serial_text"
                and isinstance(configured_port, str)
                and configured_port.casefold() == port.casefold()
                and any(
                    role.driver == "alicat"
                    and role.connection_id == connection.connection_id
                    for role in self._profile.device_roles
                )
            ):
                return connection
        return None

    def _unique_connection_id(self, value: str) -> str:
        base = re.sub(
            r"[^a-z0-9]+",
            "_",
            value.casefold(),
        ).strip("_")
        existing = {
            connection.connection_id for connection in self._profile.connections
        }
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _validate_new_device_identity(
        self,
        device_id_value: str,
        hardware_label_value: str,
    ) -> tuple[str, str]:
        device_id = device_id_value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", device_id):
            raise ValueError(
                "Device ID must start with a lowercase letter and contain "
                "only lowercase letters, numbers, and underscores"
            )
        if any(
            role.device_id == device_id for role in self._profile.device_roles
        ):
            raise ValueError(f"Device ID {device_id!r} already exists")
        hardware_label = hardware_label_value.strip()
        if not hardware_label:
            raise ValueError("Hardware label cannot be empty")
        return device_id, hardware_label

    def _check_alicat(self, device_id: str) -> ReadinessCheckResult:
        configuration = alicat_configuration_from_profile(
            self._profile,
            device_id,
        )
        diagnostic = self._alicat_checker(configuration)
        state = diagnostic.state
        return ReadinessCheckResult(
            True,
            f"Alicat {device_id!r} responded at address "
            f"{configuration.unit_address!r}: mass flow "
            f"{state.mass_flow:g} {state.mass_flow_unit}, gas "
            f"{state.gas or 'not reported'}.",
            f"Raw response: {diagnostic.raw_response}",
        )

    def _check_keithley(self, device_id: str) -> ReadinessCheckResult:
        role = self._profile.get_role(device_id)
        configuration = _configuration_from_profile_for_power_supply(
            self._profile,
            device_id,
        )
        identity = self._keithley_checker(configuration)
        display_name = SCPI_POWER_SUPPLY_DRIVERS[role.driver]["display_name"]
        return ReadinessCheckResult(
            True,
            f"{display_name} {device_id!r} identified as {identity.model}, "
            f"serial {identity.serial_number}.",
            f"Manufacturer: {identity.manufacturer}; firmware: "
            f"{identity.firmware_version}",
        )

    def _check_guardian(self, device_id: str) -> ReadinessCheckResult:
        configuration = guardian_configuration_from_profile(
            self._profile,
            device_id,
        )
        diagnostic = self._guardian_checker(configuration)
        readings = ", ".join(
            f"{label} {value:g}{unit}"
            for label, value, unit in (
                ("plate", diagnostic.temperature, " degC"),
                ("probe", diagnostic.probe_temperature, " degC"),
                ("stir", diagnostic.stir_speed, " rpm"),
            )
            if value is not None
        ) or "no channels reported"
        return ReadinessCheckResult(
            True,
            f"Guardian {device_id!r} identified as {diagnostic.identity.model} "
            f"(serial {diagnostic.identity.serial_number}): {readings}.",
            f"Firmware: {diagnostic.identity.firmware_version}; mode: "
            f"{diagnostic.mode.name}",
        )

    def _connection_description(self, device_id: str) -> str:
        role = self._profile.get_role(device_id)
        if role.backend is DeviceBackend.SIMULATED:
            return "Simulation"
        if role.connection_id is None:
            return "Not configured"

        connection = self._profile.get_connection(role.connection_id)
        parameters = connection.parameters
        if connection.connection_type == "serial_text":
            port = parameters.get("port", "not configured")
            address = role.connection_parameters.get("address")
            suffix = f", address {address}" if address else ""
            return f"{port}{suffix}"
        if connection.connection_type == "socket_scpi":
            host = parameters.get("host", "not configured")
            port = parameters.get("port", "not configured")
            return f"{host}:{port}"
        if connection.connection_type == "visa_scpi":
            return str(parameters.get("resource_name", "not configured"))
        return connection.connection_id


def _list_windows_serial_ports() -> tuple[SerialPortInfo, ...]:
    try:
        from serial.tools import list_ports
    except ImportError as error:
        raise RuntimeError(
            "Serial-port discovery requires the optional hardware "
            "dependency: pip install -e .[hardware]"
        ) from error

    return tuple(
        SerialPortInfo(
            device=port.device,
            description=port.description or "Unknown device",
            hardware_id=port.hwid or "Unknown",
        )
        for port in list_ports.comports()
    )
