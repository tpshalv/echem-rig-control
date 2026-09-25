from dataclasses import replace
from rig_control.devices.alicat.verification import frame_signature, verify_role
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
from rig_control.diagnostics.alicat import read_alicat_state, scan_alicat_bus
from rig_control.diagnostics.esp32 import (
    Esp32DiscoveryResult,
    discover_esp32,
    read_esp32_state,
)
from rig_control.rig_profile import DeviceBackend, RigProfile
from rig_control.rig_profile_writing import write_rig_profile


from rig_control.ui.device_setup.types import (
    SCPI_POWER_SUPPLY_DRIVERS, SCPI_POWER_SUPPLY_DRIVER_LABELS,
    SCPI_POWER_SUPPLY_LABEL_TO_DRIVER, SerialPortInfo, DeviceReadinessRow,
    EditDeviceRequest, ReadinessCheckResult, AlicatScanRow, AddAlicatRequest,
    AddKeithleyRequest, AddGuardianRequest, AddTemperatureProbeRequest,
    AddEsp32Request, AddKamoerM1StpRequest, AddEzoHumRequest, SerialPortProvider, AlicatChecker,
    AlicatScanner, KeithleyChecker, GuardianChecker, Esp32Checker, Esp32Scanner,
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

    def inspect_alicat_configuration(self, device_id):
        configuration = alicat_configuration_from_profile(self.profile, device_id)
        if not configuration.is_controller:
            raise ValueError("Select an Alicat controller (MFC or BPR)")
        return read_alicat_configuration(configuration)

    def commission_alicat(self, device_id, observed, *, frame_confirmed, downstream_confirmed,
                          frame_fields, pressure_unit, flow_unit, volumetric_flow_unit, temperature_unit,
                          maximum_pressure_bara=2.5):
        """Save expectations only; instrument configuration is always read-only."""
        from rig_control.rig_profile import ExpectedDeviceIdentity
        try:
            if not frame_confirmed:
                raise ValueError("Confirm the field order and units against the displayed instrument table")
            current = self.inspect_alicat_configuration(device_id)
            if (current.serial_number, current.loop_variable, current.inverse, current.setpoint_unit,
                frame_signature(current.frame_description)) != (
                observed.serial_number, observed.loop_variable, observed.inverse, observed.setpoint_unit,
                frame_signature(observed.frame_description)):
                raise ValueError("Instrument changed since inspection; inspect it again")
            role = self.profile.get_role(device_id)
            settings = dict(role.settings)
            settings.update(verified_frame_signature=frame_signature(current.frame_description),
                            downstream_valve_confirmed=downstream_confirmed, frame_fields=frame_fields,
                            pressure_unit=pressure_unit, flow_unit=flow_unit,
                            volumetric_flow_unit=volumetric_flow_unit, temperature_unit=temperature_unit,
                            maximum_pressure_bara=maximum_pressure_bara)
            updated = replace(role, settings=settings, expected_identity=ExpectedDeviceIdentity(
                manufacturer="Alicat", model=current.model, serial_number=current.serial_number))
            candidate = replace(self.profile, device_roles=tuple(updated if r.device_id == device_id else r
                                                                  for r in self.profile.device_roles))
            configuration = alicat_configuration_from_profile(candidate, device_id)
            verify_role(current, bpr=configuration.is_bpr, expected_serial=configuration.expected_serial,
                        expected_frame_signature=configuration.verified_frame_signature, flow_unit=flow_unit,
                        downstream_confirmed=downstream_confirmed)
            if configuration.is_bpr:
                from rig_control.devices.pressure_controller import absolute_unit_factor
                absolute_unit_factor(pressure_unit)
                absolute_unit_factor(current.setpoint_unit)
                if current.maximum_setpoint is None:
                    raise ValueError("BPR control needs verified instrument bounds; this firmware's range query is not yet supported")
            self._profiles.commit(candidate)
            self._profiles.readiness[device_id] = "verified"
            return ReadinessCheckResult(True, f"Commissioned {device_id}: {current.description}. No instrument settings changed.")
        except Exception as error:
            return ReadinessCheckResult(False, "Alicat commissioning failed", str(error))

    def add_alicat_and_check(
        self,
        request: AddAlicatRequest,
    ) -> ReadinessCheckResult:
        """Read-only check a proposed Alicat, then save it on success."""

        try:
            candidate = DeviceProfileBuilder(self._profiles.profile).with_new_alicat(request)
            configuration = alicat_configuration_from_profile(
                candidate,
                request.device_id.strip(),
            )
            diagnostic = self._discovery.identify_alicat(configuration)
            backup = self._profiles.commit(candidate)
        except Exception as error:
            return ReadinessCheckResult(
                False,
                "The Alicat was not added because its read-only check "
                "or validation failed.",
                f"{type(error).__name__}: {error}",
            )

        self._profiles.readiness[configuration.device_id] = "unverified" if configuration.is_controller else "ready"
        backup_text = (
            f" Previous profile backed up to {backup}." if backup else ""
        )
        return ReadinessCheckResult(
            True,
            f"Added {configuration.friendly_name!r} as "
            f"{configuration.device_id!r} on {configuration.connection.port}, "
            f"address {configuration.unit_address}. Read-only response "
            f"confirmed gas {diagnostic.state.gas or 'not reported'}."
            + (" Use Verify Alicat role before operating this controller." if configuration.is_controller else "")
            + backup_text,
            f"Raw response: {diagnostic.raw_response}",
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
