from collections.abc import Callable, Mapping

from rig_control.devices.base import Device
from rig_control.devices.alicat.bus import AlicatBus
from rig_control.devices.alicat.configuration import (
    AlicatSerialConfiguration,
    configuration_from_profile as alicat_configuration_from_profile,
)
from rig_control.devices.alicat.driver import AlicatMassFlowController
from rig_control.devices.alicat.meter import AlicatMassFlowMeter
from rig_control.devices.alicat.protocol import AlicatAsciiProtocolClient
from rig_control.devices.keithley_2260b.configuration import (
    configuration_from_profile as keithley_configuration_from_profile,
)
from rig_control.devices.keithley_2260b.driver import Keithley2260B
from rig_control.devices.manager import DeviceManager
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.devices.esp32_dht11 import Esp32Dht11
from rig_control.esp32.session import ControllerSession
from rig_control.devices.mass_flow_controller import (
    MassFlowControllerLimits,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_mfc import (
    SimulatedMassFlowController,
)
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.rig_profile import (
    ConfigurationValue,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
    ConnectionDefinition,
)
from rig_control.models import EventSink
from rig_control.transports.socket_scpi import SocketScpiTransport
from rig_control.transports.pyvisa_scpi import PyVisaScpiTransport
from rig_control.devices.keithley_2260b.configuration import VisaScpiConfiguration
from rig_control.transports.pyserial_text import PySerialTextTransport
from rig_control.transports.serial_text import SerialTextTransport
from rig_control.transports.pyserial_duplex_text import PySerialDuplexTextTransport
from rig_control.transports.duplex_text import DuplexTextTransport


type AlicatTransportFactory = Callable[
    [AlicatSerialConfiguration],
    SerialTextTransport,
]
type Esp32TransportFactory = Callable[[ConnectionDefinition], DuplexTextTransport]


class DeviceFactoryError(RuntimeError):
    """A rig profile device could not be constructed safely."""


_SENSOR_CAPABILITIES = {
    DeviceCapability.TEMPERATURE_SENSOR,
    DeviceCapability.PRESSURE_SENSOR_ABSOLUTE,
    DeviceCapability.PRESSURE_SENSOR_RELATIVE,
    DeviceCapability.PRESSURE_SENSOR_DIFFERENTIAL,
    DeviceCapability.HUMIDITY_SENSOR,
}


def create_device_manager(
    profile: RigProfile,
    *,
    alicat_transport_factory: AlicatTransportFactory | None = None,
    esp32_transport_factory: Esp32TransportFactory | None = None,
    event_sink: EventSink | None = None,
) -> DeviceManager:
    """Construct all enabled devices described by a rig profile."""

    if not isinstance(profile, RigProfile):
        raise TypeError("Profile must be a RigProfile")

    manager = DeviceManager(event_sink=event_sink)
    alicat_buses: dict[str, AlicatBus] = {}
    esp32_buses: dict[str, Esp32Bus] = {}
    selected_alicat_transport_factory = (
        alicat_transport_factory or _create_pyserial_transport
    )
    selected_esp32_transport_factory = (
        esp32_transport_factory or _create_esp32_transport
    )

    for role in profile.enabled_roles:
        manager.register(
            _create_device(
                profile,
                role,
                alicat_buses,
                selected_alicat_transport_factory,
                esp32_buses,
                selected_esp32_transport_factory,
                event_sink,
            )
        )

    return manager


def _create_device(
    profile: RigProfile,
    role: DeviceRole,
    alicat_buses: dict[str, AlicatBus],
    alicat_transport_factory: AlicatTransportFactory,
    esp32_buses: dict[str, Esp32Bus],
    esp32_transport_factory: Esp32TransportFactory,
    event_sink: EventSink | None,
) -> Device:
    if role.backend is DeviceBackend.REAL:
        if (
            role.capability is DeviceCapability.DC_POWER_SUPPLY
            and role.driver == "keithley_2260b"
        ):
            return _create_real_keithley(profile, role)

        if (
            role.capability in {
                DeviceCapability.MASS_FLOW_CONTROLLER,
                DeviceCapability.MASS_FLOW_METER,
            }
            and role.driver == "alicat"
        ):
            return _create_real_alicat(
                profile,
                role,
                alicat_buses,
                alicat_transport_factory,
            )

        if (
            role.capability is DeviceCapability.REMOTE_CONTROLLER
            and role.driver == "esp32_json"
        ):
            bus = _get_or_create_esp32_bus(
                profile, role, esp32_buses, esp32_transport_factory
            )
            heartbeat_interval = _optional_number(
                role.settings,
                "heartbeat_interval_seconds",
                2.0,
                role.device_id,
            )
            return Esp32Controller(
                role.device_id,
                bus,
                heartbeat_interval_seconds=heartbeat_interval,
                event_sink=event_sink,
            )

        if role.driver == "esp32_dht11" and role.capability in {
            DeviceCapability.TEMPERATURE_SENSOR,
            DeviceCapability.HUMIDITY_SENSOR,
        }:
            return Esp32Dht11(
                role.device_id,
                _get_or_create_esp32_bus(
                    profile, role, esp32_buses, esp32_transport_factory
                ),
            )

        raise DeviceFactoryError(
            f"Device {role.device_id!r} is configured as real "
            f"hardware using unsupported driver {role.driver!r}. "
            "No connection was attempted."
        )

    if role.backend is not DeviceBackend.SIMULATED:
        raise DeviceFactoryError(
            f"Device {role.device_id!r} has unsupported backend "
            f"{role.backend!r}"
        )

    try:
        if (
            role.capability
            is DeviceCapability.MASS_FLOW_CONTROLLER
        ):
            return _create_simulated_mfc(role)

        if role.capability is DeviceCapability.DC_POWER_SUPPLY:
            return _create_simulated_power_supply(role)

        if role.capability in _SENSOR_CAPABILITIES:
            return _create_simulated_sensor(role)

    except (TypeError, ValueError) as error:
        raise DeviceFactoryError(
            f"Invalid settings for simulated device "
            f"{role.device_id!r}: {type(error).__name__}: {error}"
        ) from error

    raise DeviceFactoryError(
        f"No simulated device factory is available for "
        f"{role.device_id!r} with capability "
        f"{role.capability.value!r}"
    )


def _create_real_keithley(
    profile: RigProfile,
    role: DeviceRole,
) -> Keithley2260B:
    """Construct a disconnected Keithley from its profile settings."""

    try:
        configuration = keithley_configuration_from_profile(
            profile,
            role.device_id,
        )
        connection = configuration.connection
        if isinstance(connection, VisaScpiConfiguration):
            transport = PyVisaScpiTransport(
                connection.resource_name,
                timeout_seconds=connection.timeout_seconds,
                baud_rate=connection.baud_rate,
            )
        else:
            transport = SocketScpiTransport(
                host=connection.host,
                port=connection.port,
                timeout_seconds=connection.timeout_seconds,
            )
        return Keithley2260B(
            device_id=configuration.device_id,
            limits=configuration.limits,
            transport=transport,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DeviceFactoryError(
            f"Invalid settings for real Keithley device "
            f"{role.device_id!r}: {type(error).__name__}: {error}. "
            "No connection was attempted."
        ) from error


def _create_real_alicat(
    profile: RigProfile,
    role: DeviceRole,
    buses: dict[str, AlicatBus],
    transport_factory: AlicatTransportFactory,
) -> AlicatMassFlowController | AlicatMassFlowMeter:
    """Construct a disconnected Alicat on its profile's shared bus."""

    try:
        configuration = alicat_configuration_from_profile(
            profile,
            role.device_id,
        )
        connection = configuration.connection
        bus = buses.get(connection.connection_id)
        if bus is None:
            bus = AlicatBus(
                connection.connection_id,
                transport_factory(connection),
            )
            buses[connection.connection_id] = bus

        protocol = AlicatAsciiProtocolClient(
            bus,
            configuration.frame_fields,
            configuration.engineering_units,
            requires_setpoint=configuration.is_controller,
        )
        if configuration.is_controller:
            return AlicatMassFlowController(configuration, protocol)
        return AlicatMassFlowMeter(configuration, protocol)
    except (KeyError, TypeError, ValueError) as error:
        raise DeviceFactoryError(
            f"Invalid settings for real Alicat device "
            f"{role.device_id!r}: {type(error).__name__}: {error}. "
            "No connection was attempted."
        ) from error


def _get_or_create_esp32_bus(
    profile: RigProfile,
    role: DeviceRole,
    buses: dict[str, Esp32Bus],
    transport_factory: Esp32TransportFactory,
) -> Esp32Bus:
    try:
        if role.connection_id is None:
            raise ValueError("ESP32 controller requires a connection_id")
        connection = profile.get_connection(role.connection_id)
        if connection.connection_type != "serial_json":
            raise ValueError("ESP32 controller connection must use 'serial_json'")
        existing = buses.get(connection.connection_id)
        if existing is not None:
            return existing
        controller_id_value = connection.parameters.get(
            "controller_id",
            role.settings.get("controller_id", "esp32_main_controller"),
        )
        if not isinstance(controller_id_value, str) or not controller_id_value.strip():
            raise TypeError("ESP32 controller_id must be non-empty text")
        bus = Esp32Bus(
            connection.connection_id,
            ControllerSession(controller_id_value, transport_factory(connection)),
        )
        buses[connection.connection_id] = bus
        return bus
    except (KeyError, TypeError, ValueError) as error:
        raise DeviceFactoryError(
            f"Invalid settings for real ESP32 controller {role.device_id!r}: "
            f"{type(error).__name__}: {error}. No connection was attempted."
        ) from error


def _create_esp32_transport(
    connection: ConnectionDefinition,
) -> PySerialDuplexTextTransport:
    return PySerialDuplexTextTransport(
        _require_text(connection.parameters, "port", connection.connection_id),
        baud_rate=int(
            _require_number(
                connection.parameters,
                "baud_rate",
                connection.connection_id,
            )
        ),
        timeout_seconds=_optional_number(
            connection.parameters,
            "timeout_seconds",
            2.0,
            connection.connection_id,
        ),
    )


def _create_pyserial_transport(
    configuration: AlicatSerialConfiguration,
) -> PySerialTextTransport:
    return PySerialTextTransport(
        port=configuration.port,
        baud_rate=configuration.baud_rate,
        timeout_seconds=configuration.timeout_seconds,
    )


def _create_simulated_mfc(
    role: DeviceRole,
) -> SimulatedMassFlowController:
    return SimulatedMassFlowController(
        device_id=role.device_id,
        limits=MassFlowControllerLimits(
            maximum_flow=_require_number(
                role.settings,
                "maximum_flow",
                role.device_id,
            ),
            flow_unit=_require_text(
                role.settings,
                "flow_unit",
                role.device_id,
            ),
        ),
    )


def _create_simulated_power_supply(
    role: DeviceRole,
) -> SimulatedPowerSupply:
    return SimulatedPowerSupply(
        device_id=role.device_id,
        limits=PowerSupplyLimits(
            maximum_voltage=_require_number(
                role.settings,
                "maximum_voltage",
                role.device_id,
            ),
            maximum_current=_require_number(
                role.settings,
                "maximum_current",
                role.device_id,
            ),
            maximum_power=_require_number(
                role.settings,
                "maximum_power",
                role.device_id,
            ),
        ),
    )


def _create_simulated_sensor(
    role: DeviceRole,
) -> SimulatedSensor:
    return SimulatedSensor(
        device_id=role.device_id,
        value=_optional_number(
            role.settings,
            "initial_value",
            0.0,
            role.device_id,
        ),
        unit=_require_text(
            role.settings,
            "unit",
            role.device_id,
        ),
    )


def _require_text(
    settings: Mapping[str, ConfigurationValue],
    key: str,
    device_id: str,
) -> str:
    if key not in settings:
        raise ValueError(
            f"Missing required setting "
            f"devices.{device_id}.settings.{key}"
        )

    value = settings[key]

    if not isinstance(value, str) or not value.strip():
        raise TypeError(
            f"Setting devices.{device_id}.settings.{key} "
            "must be non-empty text"
        )

    return value


def _require_number(
    settings: Mapping[str, ConfigurationValue],
    key: str,
    device_id: str,
) -> float:
    if key not in settings:
        raise ValueError(
            f"Missing required setting "
            f"devices.{device_id}.settings.{key}"
        )

    value = settings[key]

    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise TypeError(
            f"Setting devices.{device_id}.settings.{key} "
            "must be a number"
        )

    return float(value)


def _optional_number(
    settings: Mapping[str, ConfigurationValue],
    key: str,
    default: float,
    device_id: str,
) -> float:
    if key not in settings:
        return default

    return _require_number(settings, key, device_id)
