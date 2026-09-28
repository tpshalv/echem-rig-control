from dataclasses import replace
from math import isclose
from rig_control.diagnostics.alicat import read_alicat_configuration
from collections.abc import Callable, Mapping
from pathlib import Path

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
from rig_control.diagnostics.alicat import (
    probe_alicat_address,
    read_alicat_state,
    scan_alicat_bus,
)
from rig_control.diagnostics.esp32 import (
    Esp32DiscoveryResult,
    discover_esp32,
    read_esp32_state,
)
from rig_control.rig_profile import DeviceBackend, DeviceCapability, RigProfile
from rig_control.rig_profile_writing import write_rig_profile


from rig_control.ui.device_setup.types import (
    SCPI_POWER_SUPPLY_DRIVERS, SCPI_POWER_SUPPLY_DRIVER_LABELS,
    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER, SerialPortInfo, DeviceReadinessRow,
    BPR_INSTALLATION_WARNING,
    EditDeviceRequest, ReadinessCheckResult, AlicatScanRow, AddAlicatRequest,
    AddKeithleyRequest, AddGuardianRequest, AddTemperatureProbeRequest,
    AddEsp32Request, AddKamoerM1StpRequest, AddEzoHumRequest, SerialPortProvider, AlicatChecker,
    AlicatProbe, AlicatScanner, KeithleyChecker, GuardianChecker, Esp32Checker, Esp32Scanner,
    KamoerM1StpChecker, EzoHumChecker, ProfileWriter,
)
from rig_control.ui.device_setup.power_supply import (
    identify_scpi_power_supply,
    _configuration_from_profile_for_power_supply,
)
from rig_control.ui.device_setup.discovery import DeviceDiscovery
from rig_control.ui.device_setup.profile_editor import ProfileEditor
from rig_control.ui.device_setup.profile_builders import DeviceProfileBuilder


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
        alicat_probe: AlicatProbe = probe_alicat_address,
        keithley_checker: KeithleyChecker = identify_scpi_power_supply,
        guardian_checker: GuardianChecker = read_guardian_state,
        temperature_probe_checker: Callable = read_probe,
        esp32_checker: Esp32Checker = read_esp32_state,
        esp32_scanner: Esp32Scanner = discover_esp32,
        kamoer_m1_stp_checker: KamoerM1StpChecker = read_pump_state,
        ezo_hum_checker: EzoHumChecker = check_ezo_hum,
        profile_writer: ProfileWriter = write_rig_profile,
    ) -> None:
        if not isinstance(profile, RigProfile):
            raise TypeError("profile must be a RigProfile")
        self._profiles = ProfileEditor(profile, profile_path, profile_writer)
        self._discovery = DeviceDiscovery(
            lambda: self._profiles.profile, self._profiles.readiness,
            serial_port_provider=serial_port_provider,
            alicat_checker=alicat_checker, alicat_scanner=alicat_scanner,
            alicat_probe=alicat_probe,
            keithley_checker=keithley_checker, guardian_checker=guardian_checker,
            temperature_probe_checker=temperature_probe_checker,
            esp32_checker=esp32_checker, esp32_scanner=esp32_scanner,
            kamoer_m1_stp_checker=kamoer_m1_stp_checker,
            ezo_hum_checker=ezo_hum_checker,
        )

    @property
    def serial_port_error(self) -> str:
        return self._discovery.serial_port_error

    @property
    def profile(self) -> RigProfile:
        return self._profiles.profile

    @property
    def profile_path(self) -> Path:
        return self._profiles.profile_path

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
                readiness=self._profiles.readiness.get(
                    role.device_id,
                    "not checked",
                ),
                enabled=role.enabled,
            )
            for role in self._profiles.profile.device_roles
        )

    def inspect_alicat_configuration(self, device_id: str):
        """Re-read one saved Alicat's configuration without changing it."""

        configuration = alicat_configuration_from_profile(self.profile, device_id)
        if not configuration.is_controller:
            raise ValueError("Select an Alicat controller (MFC or BPR)")
        return read_alicat_configuration(configuration)

    def probe_alicat(self, port: str, address: str, baud_rate: int = 19200):
        """Read one address's live configuration before adding it."""

        return self._discovery.probe_alicat(port, address, baud_rate)

    def acknowledge_bpr_installation(self, device_id: str) -> ReadinessCheckResult:
        """Record the hardware acknowledgement for one already-saved BPR.

        Adding a BPR records this automatically. This exists for devices saved
        by an earlier version, so they never need a commissioning procedure.
        """

        try:
            role = self._profiles.profile.get_role(device_id)
            if role.driver != "alicat" or role.capability is not (
                DeviceCapability.BACK_PRESSURE_CONTROLLER
            ):
                raise ValueError(f"{device_id!r} is not a saved Alicat BPR")
            observed = self.inspect_alicat_configuration(device_id)
            if observed.detected_role != "bpr":
                raise ValueError(
                    f"This device is saved as a BPR but now reports "
                    f"{observed.description}. Acknowledgement was not recorded."
                )
            updated = replace(
                role,
                settings=dict(role.settings, downstream_valve_confirmed=True),
            )
            candidate = replace(
                self.profile,
                device_roles=tuple(
                    updated if item.device_id == device_id else item
                    for item in self.profile.device_roles
                ),
            )
            self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                f"The back-pressure installation acknowledgement was not saved "
                f"for {device_id!r}.",
                f"{type(error).__name__}: {error}",
            )
        return ReadinessCheckResult(
            True,
            f"Recorded the back-pressure installation acknowledgement for "
            f"{device_id!r}. It will not be asked for again on reconnect.",
        )

    def add_alicat_and_check(
        self,
        request: AddAlicatRequest,
    ) -> ReadinessCheckResult:
        """Detect one Alicat's role, check it read-only, then save it.

        The operator never chooses MFC, MFM or BPR: the role comes from the
        instrument's control variable and inverse-control register.
        """

        try:
            detected = self._discovery.probe_alicat(
                request.port.strip(),
                request.unit_address.strip().upper(),
            )
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Alicat's configuration could not be read, so nothing was "
                "saved. Check the COM port, address and cabling, then try again.",
                f"{type(error).__name__}: {error}",
            )

        if not detected.usable:
            return ReadinessCheckResult(
                False,
                "The Alicat was not added because its configuration could not "
                "be interpreted.",
                detected.configuration_error
                or "The control variable and inverse setting were not readable.",
            )

        if detected.detected_role == "bpr" and not request.downstream_valve_acknowledged:
            return ReadinessCheckResult(
                False,
                "This Alicat reports back-pressure control and needs one "
                "hardware acknowledgement before it is saved.",
                detected.control_description,
                requires_acknowledgement=BPR_INSTALLATION_WARNING,
            )

        try:
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_alicat(
                request, detected
            )
            configuration = alicat_configuration_from_profile(
                candidate,
                request.device_id.strip(),
            )
            diagnostic = self._discovery.identify_alicat(configuration)
            self._confirm_alicat_frame(configuration, diagnostic, detected)
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Alicat was not added because its read-only check "
                "or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profiles.readiness[configuration.device_id] = "ready"
        backup_text = (
            f" Previous profile backed up to {backup}." if backup else ""
        )
        return ReadinessCheckResult(
            True,
            f"Added {configuration.friendly_name!r} as "
            f"{configuration.device_id!r} on {configuration.connection.port}, "
            f"address {configuration.unit_address}, detected automatically as "
            f"{detected.control_description}. It is ready to use."
            + backup_text,
            f"Raw response: {diagnostic.raw_response}; frame "
            f"{','.join(field.value for field in configuration.frame_fields)}",
        )

    @staticmethod
    def _confirm_alicat_frame(configuration, diagnostic, detected) -> None:
        """Confirm the saved frame layout really decodes this instrument.

        The setpoint column is cross-checked against the instrument's own LS
        readback, so a misread data-frame description cannot silently put the
        wrong measurement, or the wrong unit, against a channel.
        """

        if not configuration.is_controller:
            return
        observed = diagnostic.control_configuration
        if observed is None:
            raise ValueError(
                "The control configuration could not be re-read during the "
                "read-only check: " + (diagnostic.verification_error or "no detail")
            )
        if observed.detected_role != detected.detected_role:
            raise ValueError(
                f"The instrument's configuration changed during setup: it now "
                f"reports {observed.description}"
            )
        readback = diagnostic.setpoint_readback
        if readback is None:
            return
        value, unit = readback
        expected_unit = configuration.engineering_units.setpoint
        if unit.strip().casefold() != expected_unit.strip().casefold():
            raise ValueError(
                f"The instrument reports setpoints in {unit!r} but the saved "
                f"frame expects {expected_unit!r}"
            )
        if not isclose(
            value,
            diagnostic.state.setpoint,
            rel_tol=1e-3,
            abs_tol=max(abs(value), 1.0) * 1e-3,
        ):
            raise ValueError(
                "The data frame could not be confirmed: its setpoint column "
                f"reads {diagnostic.state.setpoint:g} but the instrument "
                f"reports a setpoint of {value:g} {unit}. The reported frame "
                "layout does not match the live frame."
            )

    def add_keithley_and_check(
        self,
        request: AddKeithleyRequest,
    ) -> ReadinessCheckResult:
        """Identify a proposed SCPI power supply, then save it on success."""

        try:
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_keithley(request)
            configuration = _configuration_from_profile_for_power_supply(
                candidate,
                request.device_id.strip(),
            )
            identity = self._discovery.identify_keithley(configuration)
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The power supply was not added because its read-only "
                "identification or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profiles.readiness[configuration.device_id] = "ready"
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
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_guardian(request)
            configuration = guardian_configuration_from_profile(
                candidate,
                request.device_id.strip(),
            )
            diagnostic = self._discovery.identify_guardian(configuration)
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Guardian hotplate was not added because its "
                "read-only check or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profiles.readiness[configuration.device_id] = "ready"
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
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_temperature_probe(request)
            configuration = ta612c_configuration_from_profile(candidate, request.device_id.strip())
            identity, readings = self._discovery.identify_temperature_probe(configuration)
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(False, "The temperature probe was not added because its read-only check or validation failed.", f"{type(error).__name__}: {error}")
        self._profiles.readiness[configuration.device_id] = "ready"
        label = candidate.get_role(configuration.device_id).friendly_name
        values = ", ".join(f"{r.channel}={r.measurement.value:g} degC" for r in readings)
        return ReadinessCheckResult(True, f"Added {label!r} as {configuration.device_id!r} on {configuration.port}.", f"Identity: {identity}; readings: {values}" + (f" Previous profile backed up to {backup}." if backup else ""))

    def add_kamoer_m1_stp_and_check(
        self, request: AddKamoerM1StpRequest,
    ) -> ReadinessCheckResult:
        try:
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_kamoer_m1_stp(request)
            configuration = kamoer_m1_stp_configuration_from_profile(candidate, request.device_id.strip())
            diagnostic = self._discovery.identify_kamoer_m1_stp(configuration)
            if diagnostic.fault_status:
                raise ValueError(f"Pump reports fault status {diagnostic.fault_status}")
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(False, "The peristaltic pump was not added because its read-only check or validation failed.", f"{type(error).__name__}: {error}")
        self._profiles.readiness[configuration.device_id] = "ready"
        label = candidate.get_role(configuration.device_id).friendly_name
        return ReadinessCheckResult(
            True, f"Added {label!r} as {configuration.device_id!r} on {configuration.port}. Read-only query confirmed the pump is present and reports no fault.",
            f"Running: {diagnostic.running}; direction: {diagnostic.direction.value}; "
            f"speed setpoint: {diagnostic.speed_setpoint_rpm:g} rpm."
            + (f" Previous profile backed up to {backup}." if backup else ""),
        )

    def add_ezo_hum_and_check(self, request: AddEzoHumRequest) -> ReadinessCheckResult:
        try:
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_ezo_hum(request)
            configuration = ezo_hum_configuration_from_profile(candidate, request.device_id.strip())
            diagnostic = self._discovery.identify_ezo_hum(configuration)
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The EZO-HUM probe was not added because configuration or verification failed.",
                f"{type(error).__name__}: {error}",
            )
        self._profiles.readiness[configuration.device_id] = "ready"
        readings = ", ".join(
            f"{item.channel}={item.measurement.value:g} {item.measurement.unit}"
            for item in diagnostic.readings
        )
        return ReadinessCheckResult(
            True,
            f"Added {candidate.get_role(configuration.device_id).friendly_name!r} "
            f"as {configuration.device_id!r} on {configuration.port}.",
            f"EZO-HUM firmware {diagnostic.identity.firmware_version}; {readings}"
            + (f" Previous profile backed up to {backup}." if backup else ""),
        )

    def _connection_description(self, device_id: str) -> str:
        role = self._profiles.profile.get_role(device_id)
        if role.backend is DeviceBackend.SIMULATED:
            return "Simulation"
        if role.connection_id is None:
            return "Not configured"

        connection = self._profiles.profile.get_connection(role.connection_id)
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

    def switch_profile(self, path: str | Path) -> ReadinessCheckResult:
        return self._profiles.switch_profile(path)

    def save_profile_as(self, path: str | Path) -> ReadinessCheckResult:
        return self._profiles.save_profile_as(path)

    def update_profile_identity(
        self,
        profile_id: str,
        friendly_name: str,
    ) -> ReadinessCheckResult:
        return self._profiles.update_profile_identity(profile_id, friendly_name)

    def create_new_profile(
        self,
        profile_id: str,
        friendly_name: str,
        path: str | Path,
    ) -> ReadinessCheckResult:
        return self._profiles.create_new_profile(profile_id, friendly_name, path)

    def device_edit_values(self, device_id: str) -> EditDeviceRequest:
        return self._profiles.device_edit_values(device_id)

    def update_device(self, request: EditDeviceRequest) -> ReadinessCheckResult:
        return self._profiles.update_device(request)

    def remove_device(self, device_id: str) -> ReadinessCheckResult:
        return self._profiles.remove_device(device_id)

    def add_discovered_esp32(
        self,
        request: AddEsp32Request,
        discovery: Esp32DiscoveryResult,
        *,
        controller_friendly_name: str | None = None,
        selected_device_names: Mapping[str, str] | None = None,
        selected_device_intervals: Mapping[str, float] | None = None,
    ) -> ReadinessCheckResult:
        return self._profiles.add_discovered_esp32(request, discovery, controller_friendly_name=controller_friendly_name, selected_device_names=selected_device_names, selected_device_intervals=selected_device_intervals)

    def device_poll_interval(self, device_id: str) -> float | None:
        return self._profiles.device_poll_interval(device_id)

    def update_measurement_interval(
        self,
        device_id: str,
        interval_seconds: float | None,
    ) -> ReadinessCheckResult:
        return self._profiles.update_measurement_interval(device_id, interval_seconds)

    def scan_esp32(self, request: AddEsp32Request) -> Esp32DiscoveryResult:
        return self._discovery.scan_esp32(request)

    def serial_ports(self) -> tuple[SerialPortInfo, ...]:
        return self._discovery.serial_ports()

    def scan_alicats(
        self,
        port: str,
        baud_rate: int = 19200,
    ) -> tuple[AlicatScanRow, ...]:
        return self._discovery.scan_alicats(port, baud_rate)

    def check_device(self, device_id: str) -> ReadinessCheckResult:
        return self._discovery.check_device(device_id)
