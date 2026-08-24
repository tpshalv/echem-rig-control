from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
import re

from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    configuration_from_profile as alicat_configuration_from_profile,
)
from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
    VisaScpiConfiguration,
    configuration_from_profile as keithley_configuration_from_profile,
)
from rig_control.diagnostics.alicat import (
    AlicatDiagnosticResult,
    DiscoveredAlicat,
    read_alicat_state,
    scan_alicat_bus,
)
from rig_control.diagnostics.keithley import identify_keithley
from rig_control.devices.keithley_2260b.protocol import KeithleyIdentity
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    ExpectedDeviceIdentity,
    RigProfile,
)
from rig_control.rig_profile_writing import write_rig_profile


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
    readiness: str


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


type SerialPortProvider = Callable[[], tuple[SerialPortInfo, ...]]
type AlicatChecker = Callable[
    [AlicatMfcConfiguration],
    AlicatDiagnosticResult,
]
type AlicatScanner = Callable[[str, int], tuple[DiscoveredAlicat, ...]]
type KeithleyChecker = Callable[
    [Keithley2260BConfiguration],
    KeithleyIdentity,
]
type ProfileWriter = Callable[[RigProfile, str | Path], Path | None]


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
        keithley_checker: KeithleyChecker = identify_keithley,
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
        self._profile_writer = profile_writer
        self._readiness: dict[str, str] = {}
        self._serial_port_error = ""

    @property
    def serial_port_error(self) -> str:
        return self._serial_port_error

    def device_rows(self) -> tuple[DeviceReadinessRow, ...]:
        return tuple(
            DeviceReadinessRow(
                device_id=role.device_id,
                friendly_name=role.friendly_name,
                device_type=(
                    f"{role.capability.value} ({role.driver})"
                ),
                connection=self._connection_description(role.device_id),
                readiness=self._readiness.get(
                    role.device_id,
                    "not checked",
                ),
            )
            for role in self._profile.enabled_roles
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
                device.address,
                device.raw_response,
                *(configured.get(device.address, (None, None))),
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
            elif role.driver == "keithley_2260b":
                result = self._check_keithley(device_id)
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
        """Identify a proposed Keithley, then save it on success."""

        try:
            candidate = self._profile_with_new_keithley(request)
            configuration = keithley_configuration_from_profile(
                candidate,
                request.device_id.strip(),
            )
            identity = self._keithley_checker(configuration)
            backup = self._profile_writer(candidate, self._profile_path)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Keithley was not added because its read-only "
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
            if isinstance(configuration.connection, VisaScpiConfiguration)
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
        device_id, hardware_label = self._validate_new_device_identity(
            request.device_id,
            request.hardware_label,
        )
        method = request.connection_method.strip().casefold()
        if method not in {"ethernet", "visa"}:
            raise ValueError("Keithley connection method must be Ethernet or VISA")
        host = request.host.strip()
        resource_name = request.resource_name.strip()
        if method == "visa":
            if not resource_name:
                raise ValueError("Keithley VISA resource name cannot be empty")
        else:
            if not host:
                raise ValueError("Keithley host name or IP address cannot be empty")
            if not isinstance(request.port, int) or isinstance(request.port, bool):
                raise TypeError("Keithley port must be an integer")
            if not 1 <= request.port <= 65535:
                raise ValueError("Keithley port must be between 1 and 65535")

        numeric_limits = {
            "timeout": request.timeout_seconds,
            "maximum voltage": request.maximum_voltage,
            "maximum current": request.maximum_current,
            "maximum power": request.maximum_power,
        }
        if not isinstance(request.visa_baud_rate, int) or isinstance(
            request.visa_baud_rate, bool
        ):
            raise TypeError("Keithley VISA baud rate must be an integer")
        if request.visa_baud_rate <= 0:
            raise ValueError("Keithley VISA baud rate must be greater than zero")
        for name, value in numeric_limits.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Keithley {name} must be numeric")
            if value <= 0:
                raise ValueError(f"Keithley {name} must be greater than zero")

        for role in self._profile.enabled_roles:
            if role.driver != "keithley_2260b" or role.connection_id is None:
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
                    f"Keithley target "
                    f"{resource_name if method == 'visa' else f'{host}:{request.port}'} "
                    "is already used "
                    f"by {role.device_id!r}"
                )

        target = resource_name if method == "visa" else host
        connection_id = self._unique_connection_id(
            f"keithley_{method}_" + re.sub(
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
            driver="keithley_2260b",
            backend=DeviceBackend.REAL,
            required=True,
            enabled=True,
            expected_identity=ExpectedDeviceIdentity(
                manufacturer="Keithley Instruments",
                model="2260B-30-108",
            ),
            connection_id=connection_id,
            settings=settings,
        )
        return replace(
            self._profile,
            connections=self._profile.connections + (connection,),
            device_roles=self._profile.device_roles + (role,),
        )

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
        configuration = keithley_configuration_from_profile(
            self._profile,
            device_id,
        )
        identity = self._keithley_checker(configuration)
        return ReadinessCheckResult(
            True,
            f"Keithley {device_id!r} identified as {identity.model}, "
            f"serial {identity.serial_number}.",
            f"Manufacturer: {identity.manufacturer}; firmware: "
            f"{identity.firmware_version}",
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
