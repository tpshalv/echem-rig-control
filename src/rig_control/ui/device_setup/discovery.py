from rig_control.devices.alicat.verification import verify_role
from collections.abc import Callable

from rig_control.devices.tasi_ta612c.configuration import (
    configuration_from_profile as ta612c_configuration_from_profile,
)
from rig_control.diagnostics.tasi_ta612c import read_probe
from rig_control.devices.atlas_ezo_hum.configuration import (
    configuration_from_profile as ezo_hum_configuration_from_profile,
)
from rig_control.diagnostics.atlas_ezo_hum import check_ezo_hum

from rig_control.devices.kamoer_m1_stp.configuration import (
    configuration_from_profile as kamoer_m1_stp_configuration_from_profile,
)
from rig_control.diagnostics.kamoer_m1_stp import read_pump_state

from rig_control.devices.alicat.configuration import (
    configuration_from_profile as alicat_configuration_from_profile,
)
from rig_control.devices.ohaus_guardian_5000.configuration import (
    configuration_from_profile as guardian_configuration_from_profile,
)
from rig_control.diagnostics.ohaus_guardian import read_guardian_state
from rig_control.diagnostics.alicat import read_alicat_state, scan_alicat_bus
from rig_control.diagnostics.esp32 import (
    Esp32DiscoveryResult,
    discover_esp32,
    read_esp32_state,
)
from rig_control.rig_profile import DeviceBackend, DeviceCapability, RigProfile


from rig_control.ui.device_setup.types import (
    SCPI_POWER_SUPPLY_DRIVERS,
    SerialPortInfo,
    ReadinessCheckResult,
    AlicatScanRow,
    AddEsp32Request,
    SerialPortProvider,
    AlicatChecker,
    AlicatScanner,
    KeithleyChecker,
    GuardianChecker,
    Esp32Checker,
    Esp32Scanner,
    KamoerM1StpChecker,
    EzoHumChecker,
)
from rig_control.ui.device_setup.power_supply import (
    identify_scpi_power_supply,
    _configuration_from_profile_for_power_supply,
)

class DeviceDiscovery:
    """Read-only discovery and readiness checks, independently injectable for tests."""

    def __init__(
        self, profile_provider: Callable[[], RigProfile], readiness: dict[str, str], *,
        serial_port_provider: SerialPortProvider | None = None,
        alicat_checker: AlicatChecker = read_alicat_state,
        alicat_scanner: AlicatScanner = scan_alicat_bus,
        keithley_checker: KeithleyChecker = identify_scpi_power_supply,
        guardian_checker: GuardianChecker = read_guardian_state,
        temperature_probe_checker: Callable = read_probe,
        esp32_checker: Esp32Checker = read_esp32_state,
        esp32_scanner: Esp32Scanner = discover_esp32,
        kamoer_m1_stp_checker: KamoerM1StpChecker = read_pump_state,
        ezo_hum_checker: EzoHumChecker = check_ezo_hum,
    ) -> None:
        self._profile_provider = profile_provider
        self._readiness = readiness
        self._serial_port_provider = serial_port_provider or _list_windows_serial_ports
        self.identify_alicat = alicat_checker
        self._alicat_scanner = alicat_scanner
        self.identify_keithley = keithley_checker
        self.identify_guardian = guardian_checker
        self.identify_temperature_probe = temperature_probe_checker
        self._esp32_checker = esp32_checker
        self._esp32_scanner = esp32_scanner
        self.identify_kamoer_m1_stp = kamoer_m1_stp_checker
        self.identify_ezo_hum = ezo_hum_checker
        self.serial_port_error = ""

    @property
    def _profile(self) -> RigProfile:
        return self._profile_provider()

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

    def serial_ports(self) -> tuple[SerialPortInfo, ...]:
        try:
            ports = self._serial_port_provider()
        except Exception as error:
            self.serial_port_error = (
                f"{type(error).__name__}: {error}"
            )
            return ()

        if not isinstance(ports, tuple) or any(
            not isinstance(port, SerialPortInfo) for port in ports
        ):
            self.serial_port_error = (
                "Serial-port provider returned invalid information"
            )
            return ()

        self.serial_port_error = ""
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
                    "BPR" if role.capability is DeviceCapability.BACK_PRESSURE_CONTROLLER else
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
                control_description=device.control_description,
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
                identity, readings = self.identify_temperature_probe(
                    ta612c_configuration_from_profile(self._profile, device_id)
                )
                result = ReadinessCheckResult(
                    True, f"{role.friendly_name} responded using the selected temperature-probe protocol.",
                    f"Identity: {identity}; " + ", ".join(
                        f"{reading.channel}={reading.measurement.value:g} degC" for reading in readings
                    ),
                )
            elif role.driver == "kamoer_m1_stp":
                diagnostic = self.identify_kamoer_m1_stp(
                    kamoer_m1_stp_configuration_from_profile(self._profile, device_id)
                )
                if diagnostic.fault_status:
                    raise ValueError(f"Pump reports fault status {diagnostic.fault_status}")
                result = ReadinessCheckResult(
                    True, f"{role.friendly_name} responded and reports no fault.",
                    f"Running: {diagnostic.running}; direction: {diagnostic.direction.value}; "
                    f"speed setpoint: {diagnostic.speed_setpoint_rpm:g} rpm.",
                )
            elif role.driver == "atlas_ezo_hum":
                diagnostic = self.identify_ezo_hum(
                    ezo_hum_configuration_from_profile(self._profile, device_id)
                )
                result = ReadinessCheckResult(
                    True,
                    f"{role.friendly_name} responded as an EZO-HUM.",
                    f"Firmware: {diagnostic.identity.firmware_version}; "
                    + ", ".join(
                        f"{item.channel}={item.measurement.value:g} {item.measurement.unit}"
                        for item in diagnostic.readings
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

    def _check_alicat(self, device_id: str) -> ReadinessCheckResult:
        configuration = alicat_configuration_from_profile(
            self._profile,
            device_id,
        )
        diagnostic = self.identify_alicat(configuration)
        state = diagnostic.state
        if configuration.is_controller:
            observed = diagnostic.control_configuration
            if observed is None:
                raise ValueError("Alicat control remains unverified: " + diagnostic.verification_error)
            verify_role(observed, bpr=configuration.is_bpr, expected_serial=configuration.expected_serial,
                        expected_frame_signature=configuration.verified_frame_signature,
                        flow_unit=configuration.limits.flow_unit, downstream_confirmed=configuration.downstream_valve_confirmed)
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
        identity = self.identify_keithley(configuration)
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
        diagnostic = self.identify_guardian(configuration)
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
