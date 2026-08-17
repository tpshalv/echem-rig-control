from collections.abc import Callable, Mapping

from rig_control.devices.base import Device
from rig_control.devices.alicat.bus import AlicatBus
from rig_control.devices.alicat.configuration import (
    AlicatSerialConfiguration,
    configuration_from_profile as alicat_configuration_from_profile,
)
from rig_control.devices.alicat.driver import AlicatMassFlowController
from rig_control.devices.alicat.protocol import AlicatAsciiProtocolClient
from rig_control.devices.keithley_2260b.configuration import (
    configuration_from_profile as keithley_configuration_from_profile,
)
from rig_control.devices.keithley_2260b.driver import Keithley2260B
from rig_control.devices.manager import DeviceManager
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
)
from rig_control.transports.socket_scpi import SocketScpiTransport
from rig_control.transports.pyserial_text import PySerialTextTransport
from rig_control.transports.serial_text import SerialTextTransport


type AlicatTransportFactory = Callable[
    [AlicatSerialConfiguration],
    SerialTextTransport,
]


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
) -> DeviceManager:
    """Construct all enabled devices described by a rig profile."""

    if not isinstance(profile, RigProfile):
        raise TypeError("Profile must be a RigProfile")

    manager = DeviceManager()
    alicat_buses: dict[str, AlicatBus] = {}
    selected_alicat_transport_factory = (
        alicat_transport_factory or _create_pyserial_transport
    )

    for role in profile.enabled_roles:
        manager.register(
            _create_device(
                profile,
                role,
                alicat_buses,
                selected_alicat_transport_factory,
            )
        )

    return manager


def _create_device(
    profile: RigProfile,
    role: DeviceRole,
    alicat_buses: dict[str, AlicatBus],
    alicat_transport_factory: AlicatTransportFactory,
) -> Device:
    if role.backend is DeviceBackend.REAL:
        if (
            role.capability is DeviceCapability.DC_POWER_SUPPLY
            and role.driver == "keithley_2260b"
        ):
            return _create_real_keithley(profile, role)

        if (
            role.capability is DeviceCapability.MASS_FLOW_CONTROLLER
            and role.driver == "alicat"
        ):
            return _create_real_alicat(
                profile,
                role,
                alicat_buses,
                alicat_transport_factory,
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
) -> AlicatMassFlowController:
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
        )
        return AlicatMassFlowController(configuration, protocol)
    except (KeyError, TypeError, ValueError) as error:
        raise DeviceFactoryError(
            f"Invalid settings for real Alicat device "
            f"{role.device_id!r}: {type(error).__name__}: {error}. "
            "No connection was attempted."
        ) from error


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
