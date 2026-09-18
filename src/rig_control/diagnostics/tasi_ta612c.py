"""Opt-in TA612C protocol trial; raw frames and readings, no brand assumption."""

import argparse
from collections.abc import Callable

from rig_control.devices.tasi_ta612c.configuration import Ta612cConfiguration
from rig_control.devices.tasi_ta612c.driver import Ta612cTemperatureProbe
from rig_control.devices.tasi_ta612c.protocol import Ta612cProtocol
from rig_control.transports.pyserial_binary import PySerialBinaryTransport
from rig_control.transports.serial_binary import SerialBinaryTransport


def read_probe(configuration: Ta612cConfiguration,
               transport: SerialBinaryTransport | None = None,
               *, trace: Callable[[str, bytes], None] | None = None):
    selected = transport or PySerialBinaryTransport(configuration.port, 9600, configuration.timeout_seconds)
    device = Ta612cTemperatureProbe(configuration, Ta612cProtocol(selected, configuration.timeout_seconds, trace=trace))
    try:
        device.connect()
        return device.identity, device.read_measurements()
    finally:
        device.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--channels", default="tc1,tc2,tc3,tc4")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        identity, readings = read_probe(
            Ta612cConfiguration("probe", args.port, args.timeout, tuple(c.strip() for c in args.channels.split(","))),
            trace=lambda direction, data: print(f"{direction}: {data.hex(' ').upper()}", flush=True),
        )
        print(f"Identity: {identity}")
        for reading in readings:
            print(f"{reading.channel}: {reading.measurement.value:g} {reading.measurement.unit}")
        print("Protocol replies received; compare values/channel order with the instrument display.")
        return 0
    except Exception as error:
        print(f"Trial failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
