import argparse
from collections.abc import Sequence

from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
    configuration_from_profile,
)

from rig_control.rig_profile_loading import load_rig_profile

from rig_control.devices.keithley_2260b.protocol import (
    Keithley2260BProtocol,
    KeithleyIdentity,
)
from rig_control.transports.scpi import ScpiTransport
from rig_control.transports.socket_scpi import SocketScpiTransport


def identify_keithley(
    configuration: Keithley2260BConfiguration,
    transport: ScpiTransport | None = None,
) -> KeithleyIdentity:
    """Connect, request identification, and disconnect without control."""

    connection = configuration.connection

    if transport is None:
        if connection.host.strip().upper() == "CHANGE_ME":
            raise ValueError(
                "The Keithley IP address has not been configured. "
                "Copy rig-profile.example.toml to rig-profile.toml and "
                "replace CHANGE_ME with the instrument's IP address."
            )

        transport = SocketScpiTransport(
            host=connection.host,
            port=connection.port,
            timeout_seconds=connection.timeout_seconds,
        )

    try:
        transport.open()
        response = transport.query(
            Keithley2260BProtocol.IDENTIFY_QUERY
        )
        identity = Keithley2260BProtocol.parse_identity(response)
    finally:
        transport.close()

    if "2260B" not in identity.model.upper():
        raise RuntimeError(
            "The connected instrument did not identify as a "
            "Keithley 2260B. "
            f"Reported manufacturer={identity.manufacturer!r}, "
            f"model={identity.model!r}, "
            f"serial_number={identity.serial_number!r}."
        )

    return identity


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the read-only Keithley connection diagnostic."""

    parser = argparse.ArgumentParser(
        description=(
            "Perform a read-only identification check on the "
            "configured Keithley 2260B."
        )
    )
    parser.add_argument(
        "configuration",
        nargs="?",
        default="rig-profile.toml",
        help=(
            "Path to the rig profile TOML  file "
            "(default: rig-profile.toml)"
        ),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        profile = load_rig_profile(
            parsed_arguments.configuration
        )
        configuration = configuration_from_profile(
            profile,
            "main_power_supply",
        )
        connection = configuration.connection

        print("Keithley 2260B read-only diagnostic")
        print(f"Device ID: {configuration.device_id}")
        print(
            f"Target: {connection.host}:{connection.port}"
        )
        print(
            f"Timeout: {connection.timeout_seconds} seconds"
        )
        print("Sending identification query only: *IDN?")

        identity = identify_keithley(configuration)

    except Exception as error:
        print()
        print("DIAGNOSTIC FAILED")
        print(f"Error type: {type(error).__name__}")
        print(f"Details: {error}")
        print()
        print("No output-enable or setpoint command was requested.")
        return 1

    print()
    print("DIAGNOSTIC PASSED")
    print(f"Manufacturer: {identity.manufacturer}")
    print(f"Model: {identity.model}")
    print(f"Serial number: {identity.serial_number}")
    print(f"Firmware version: {identity.firmware_version}")
    print()
    print("The instrument responded correctly.")
    print("No output-enable or setpoint command was requested.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())