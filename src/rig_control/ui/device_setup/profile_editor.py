from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
import re

from rig_control.devices.tasi_ta612c.configuration import (
    configuration_from_profile as ta612c_configuration_from_profile,
)
from rig_control.devices.kamoer_m1_stp.configuration import (
    configuration_from_profile as kamoer_m1_stp_configuration_from_profile,
)

from rig_control.devices.alicat.configuration import (
    configuration_from_profile as alicat_configuration_from_profile,
)
from rig_control.diagnostics.esp32 import Esp32DiscoveryResult
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)
from rig_control.rig_profile_loading import load_rig_profile


from rig_control.ui.device_setup.types import (
    SCPI_POWER_SUPPLY_DRIVERS,
    EditDeviceRequest,
    ReadinessCheckResult,
    AddEsp32Request,
    ProfileWriter,
)
from rig_control.ui.device_setup.power_supply import (
    _configuration_from_profile_for_power_supply,
)

class ProfileEditor:
    """Own the editable profile, its destination, and saved changes."""

    def __init__(self, profile: RigProfile, path: str | Path, writer: ProfileWriter) -> None:
        self._profile = profile
        self._profile_path = Path(path)
        self._profile_writer = writer
        self.readiness: dict[str, str] = {}

    @property
    def profile(self) -> RigProfile:
        return self._profile

    @property
    def profile_path(self) -> Path:
        return self._profile_path

    def commit(self, candidate: RigProfile) -> Path | None:
        """Publish an edited profile only after it is successfully saved."""
        backup = self._profile_writer(candidate, self._profile_path)
        self._profile = candidate
        return backup

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
        self.readiness.clear()
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
        self.readiness.clear()
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
            updated_settings = dict(request.settings)
            protected = {"frame_fields", "pressure_unit", "flow_unit", "volumetric_flow_unit",
                         "temperature_unit", "totalized_flow_unit", "downstream_valve_confirmed"}
            if role.driver == "alicat" and any(updated_settings.get(key) != role.settings.get(key) for key in protected):
                updated_settings.pop("verified_frame_signature", None)
            updated_role = replace(
                role,
                friendly_name=friendly_name,
                enabled=request.enabled,
                required=request.required,
                system=system,
                poll_interval_seconds=request.poll_interval_seconds,
                connection_parameters=request.device_connection_parameters,
                settings=updated_settings,
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
        self.readiness.pop(role.device_id, None)
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
        elif role.driver == "kamoer_m1_stp":
            kamoer_m1_stp_configuration_from_profile(validation_profile, device_id)
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
        self.readiness.pop(device_id, None)
        return ReadinessCheckResult(
            True,
            f"Removed device {device_id!r}."
            + (f" Previous profile backed up to {backup}." if backup else ""),
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
