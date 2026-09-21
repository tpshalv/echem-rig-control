from dataclasses import replace
import re

from rig_control.devices.tasi_ta612c.configuration import Ta612cConfiguration

from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    ExpectedDeviceIdentity,
    RigProfile,
)


from rig_control.ui.device_setup.types import (
    SCPI_POWER_SUPPLY_DRIVERS,
    AddAlicatRequest,
    AddKeithleyRequest,
    AddGuardianRequest,
    AddTemperatureProbeRequest,
)

class DeviceProfileBuilder:
    """Build validated candidate profiles without saving or probing hardware."""

    def __init__(self, profile: RigProfile) -> None:
        self._profile = profile

    def with_new_alicat(
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

    def with_new_keithley(
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

    def with_new_guardian(
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

    def with_new_temperature_probe(self, request: AddTemperatureProbeRequest) -> RigProfile:
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
