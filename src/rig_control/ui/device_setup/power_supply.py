

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
    configuration_from_profile as keithley_configuration_from_profile,
)
from rig_control.devices.keithley_2280s.configuration import (
    Keithley2280SConfiguration,
    configuration_from_profile as keithley_2280s_configuration_from_profile,
)
from rig_control.devices.keithley_2280s.protocol import (
    Keithley2280SProtocol,
)
from rig_control.devices.keithley_2260b.protocol import (
    Keithley2260BProtocol,
    KeithleyIdentity,
)
from rig_control.rig_profile import RigProfile
from rig_control.transports.pyvisa_scpi import PyVisaScpiTransport
from rig_control.transports.scpi import ScpiTransport
from rig_control.transports.socket_scpi import SocketScpiTransport


from rig_control.ui.device_setup.types import SCPI_POWER_SUPPLY_DRIVERS


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
