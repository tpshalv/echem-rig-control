"""Capture what a WTW Multi 3620 IDS sends on its USB-B (virtual COM) port.

    python scripts/wtw_multi_capture.py COM7
    python scripts/wtw_multi_capture.py COM7 --baud 9600 --seconds 120

The meter has no documented command set: it only sends. Per the operating
manual (ba77169e01, sections 11.6.1, 13.3, 13.5, 16.1) it transmits:

- the displayed values of all connected sensors on a short <PRT> press,
- the same at an interval after a long <PRT__> press,
- every dataset stored with <STO> or by automatic storage,
- a calibration record at the end of each calibration.

Serial settings are 1200-19200 baud (meter default 4800), 8 data bits, no
parity, RTS/CTS handshake. The manual gives 1 stop bit in 13.3 and 2 in
16.1; receiving with 1 copes with either. This script writes nothing to the
port. It records every byte with a host timestamp so the record format
(ASCII vs CSV, line endings, separators) can be read off exactly.

Requires, in a checkout: pip install -e .[hardware]
"""

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import serial

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rig_control.app_paths import device_dumps_directory


DEFAULT_BAUD_RATE = 4800


def _output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for directory in (device_dumps_directory(), Path.cwd()):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return directory / f"wtw-multi-capture-{stamp}.txt"
        except OSError:
            continue
    raise OSError("no writable directory for the capture file")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", help="virtual COM port of the meter, e.g. COM7")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD_RATE)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument(
        "--no-rtscts",
        action="store_true",
        help="disable RTS/CTS handshake (try this if nothing arrives)",
    )
    options = parser.parse_args(arguments)

    output_path = _output_path()
    print(f"Listening on {options.port} at {options.baud} baud for {options.seconds:g} s.")
    print("Press PRT on the meter (short for one record, long for interval output).")
    print(f"Saving to {output_path}\n")

    connection = serial.Serial(
        port=options.port,
        baudrate=options.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=not options.no_rtscts,
        timeout=0.2,
    )
    raw = bytearray()
    deadline = time.monotonic() + options.seconds
    with connection, output_path.open("w", encoding="utf-8") as log:
        log.write(
            f"# port={options.port} baud={options.baud} "
            f"rtscts={not options.no_rtscts} started={datetime.now().isoformat()}\n"
        )
        try:
            while time.monotonic() < deadline:
                chunk = connection.read(4096)
                if not chunk:
                    continue
                raw.extend(chunk)
                line = f"{datetime.now().isoformat(timespec='milliseconds')} {chunk!r}"
                print(line)
                log.write(line + "\n")
                log.flush()
        except KeyboardInterrupt:
            print("\nStopped.")
        log.write("\n# decoded (latin-1)\n")
        log.write(raw.decode("latin-1"))

    print(f"\n{len(raw)} bytes captured. Saved to {output_path}")
    if not raw:
        print("Nothing arrived: check the meter's baud rate (System / Interface) or retry with --no-rtscts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
