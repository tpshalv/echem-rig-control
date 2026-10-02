"""Dump an Alicat's replies to the read-only setup queries.

Double-click the built alicat-dump.exe, or run from a checkout:

    python scripts/alicat_dump.py                 (asks which COM port)
    python scripts/alicat_dump.py COM5 A C        (straight to those addresses)

Every command below is a query with no argument. Nothing here writes a
setpoint, selects a gas, or changes any instrument configuration.

The output is printed and also saved to a text file, so it can be sent on
when device setup reports that a control configuration could not be read.

Requires, in a checkout: pip install -e .[hardware]
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rig_control.app_paths import device_dumps_directory
from rig_control.devices.alicat.protocol import SETUP_QUERIES
from rig_control.transports.pyserial_text import PySerialTextTransport


#: The same read-only queries the application itself uses, plus two
#: space-separated variants kept here for firmware that needs them.
QUERIES: tuple[tuple[str, str], ...] = SETUP_QUERIES + (
    (" LR", "loop query, space-separated, in case this firmware needs it"),
    (" R20", "register 20, space-separated"),
)

DEFAULT_BAUD_RATE = 19200

#: Statistic IDs to sweep with FPF, which reports one statistic's full scale
#: and unit. The IDs are the ones the ??D* table lists per column: 002 is
#: absolute pressure, 005 mass flow, and so on. Sweeping them says which
#: measurements this instrument knows about, including any not currently in
#: its data frame. FPF with a single argument is a query: asking for
#: statistic 2 returns 160 PSIA, not a setting of 2.
STATISTIC_IDS = range(0, 64)

#: Registers to read. R<n> is not documented in the Serial Primer, so this
#: sweep stays behind --explore and is not part of a normal dump. The
#: documented queries above cover everything the primer describes, including
#: valve drive (VD), so there is rarely a reason to run it.
REGISTER_IDS = range(0, 128)

#: A reply to one of these arrives in tens of milliseconds, so a sweep waits
#: far less than a normal query before moving on. Otherwise the queries that
#: legitimately do not answer would make the sweep take minutes.
SWEEP_TIMEOUT_SECONDS = 0.3

#: Read before and after a sweep and compared. These are the settings that
#: decide how this instrument controls, so if a sweep disturbed anything that
#: matters, the comparison says so instead of us assuming it did not.
SETTINGS_TO_COMPARE = ("LR", "R20", "R122", "LS", "??D*", "FPF 2")
#: Tried in turn when nothing answers, so a double-clicked run does not have
#: to be repeated with a different setting. Polling an address is read-only
#: whatever the baud rate.
FALLBACK_BAUD_RATES = (38400, 57600, 9600, 115200)


class Report:
    """Collects everything printed so the whole run can be saved."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def line(self, text: str = "") -> None:
        print(text)
        self._lines.append(text)

    def save(self) -> Path | None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for directory in (device_dumps_directory(), Path.cwd()):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"alicat-dump-{stamp}.txt"
                path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
                return path
            except OSError:
                continue
        return None


def available_ports() -> tuple[tuple[str, str], ...]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return ()
    return tuple(
        (port.device, port.description or "Unknown device")
        for port in list_ports.comports()
    )


def choose_port(report: Report) -> str:
    """Ask which port to use, listing what Windows currently reports."""

    ports = available_ports()
    if ports:
        report.line("Serial ports on this machine:")
        for index, (device, description) in enumerate(ports, start=1):
            report.line(f"  {index}. {device}  ({description})")
    else:
        report.line("No serial ports could be listed on this machine.")
    report.line()
    answer = ask("Which port is the Alicat BB3 on? (e.g. COM5, or a number): ")
    if answer.isdigit() and 1 <= int(answer) <= len(ports):
        return ports[int(answer) - 1][0]
    return answer


def ask(prompt: str) -> str:
    """Read one answer, tolerating a console with no input attached."""

    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def responding_addresses(
    port: str,
    baud_rate: int,
    report: Report,
) -> tuple[str, ...]:
    """Poll A-Z read-only and return the addresses that answer for themselves."""

    transport = PySerialTextTransport(
        port=port, baud_rate=baud_rate, timeout_seconds=0.2
    )
    found: list[str] = []
    try:
        transport.open()
        for code in range(ord("A"), ord("Z") + 1):
            address = chr(code)
            try:
                reply = transport.request(address)
            except Exception:
                continue
            if reply.split(maxsplit=1)[0].upper() == address:
                found.append(address)
    finally:
        transport.close()
    report.line(
        f"Addresses answering on {port} at {baud_rate} baud: "
        + (", ".join(found) if found else "none")
    )
    return tuple(found)


def read_settings(
    transport: PySerialTextTransport,
    address: str,
) -> dict[str, str]:
    """Capture the settings that decide how this instrument controls."""

    captured: dict[str, str] = {}
    for suffix in SETTINGS_TO_COMPARE:
        try:
            captured[suffix] = transport.request(f"{address}{suffix}")
        except Exception as error:
            captured[suffix] = f"({type(error).__name__})"
    return captured


def sweep(
    transport: PySerialTextTransport,
    address: str,
    template: str,
    identifiers,
) -> dict[int, str]:
    answers: dict[int, str] = {}
    for identifier in identifiers:
        try:
            answers[identifier] = transport.request(template.format(a=address, n=identifier))
        except Exception:
            continue
    return answers


def explore_address(
    port: str,
    address: str,
    baud_rate: int,
    timeout_seconds: float,
    report: Report,
) -> None:
    """Sweep the statistics and registers this instrument answers for.

    Read-only. Queries that do not answer are counted rather than listed, so
    the useful lines are not buried.
    """

    transport = PySerialTextTransport(
        port=port,
        baud_rate=baud_rate,
        timeout_seconds=min(timeout_seconds, SWEEP_TIMEOUT_SECONDS),
    )
    try:
        transport.open()
        before = read_settings(transport, address)

        report.line()
        report.line(f"=== Address {address}: statistics this instrument reports ===")
        report.line(
            "(FPF <id> reports one statistic's full scale and unit. The ids "
            "are the ones the ??D* table lists per column.)"
        )
        statistics = sweep(transport, address, "{a}FPF {n}", STATISTIC_IDS)
        for identifier, reply in statistics.items():
            report.line(f"  statistic {identifier:>3} -> {reply!r}")
        report.line(
            f"  {len(statistics)} of {len(STATISTIC_IDS)} statistic ids answered."
        )

        report.line()
        report.line(f"=== Address {address}: registers ===")
        first = sweep(transport, address, "{a}R{n}", REGISTER_IDS)
        for identifier, reply in first.items():
            report.line(f"  register {identifier:>3} -> {reply!r}")
        report.line(f"  {len(first)} of {len(REGISTER_IDS)} registers answered.")

        # Read again straight away. A register that differs between two
        # back-to-back passes is a live value rather than a setting, which is
        # exactly what a valve position would look like.
        second = sweep(transport, address, "{a}R{n}", REGISTER_IDS)
        moving = [
            identifier
            for identifier, reply in first.items()
            if second.get(identifier) != reply
        ]
        report.line()
        report.line(f"=== Address {address}: registers that changed between passes ===")
        if moving:
            for identifier in moving:
                report.line(
                    f"  register {identifier:>3}: {first[identifier]!r} "
                    f"then {second.get(identifier)!r}"
                )
            report.line(
                "  These hold live values, not settings. With the valve still "
                "and no flow, one tracking valve position should stand out."
            )
        else:
            report.line("  none")

        after = read_settings(transport, address)
        changed = [key for key in SETTINGS_TO_COMPARE if before[key] != after[key]]
        report.line()
        report.line(f"=== Address {address}: did the sweep change any setting? ===")
        if changed:
            for key in changed:
                report.line(f"  !! {key}: was {before[key]!r}, now {after[key]!r}")
            report.line(
                "  !! Something changed. Stop and report this before using "
                "the instrument."
            )
        else:
            report.line(
                "  No. Control loop, register 20, register 122, setpoint, data "
                "frame and full scale all read the same after the sweep as before."
            )
    finally:
        transport.close()


def dump_address(
    port: str,
    address: str,
    baud_rate: int,
    timeout_seconds: float,
    report: Report,
) -> None:
    transport = PySerialTextTransport(
        port=port,
        baud_rate=baud_rate,
        timeout_seconds=timeout_seconds,
    )
    report.line()
    report.line(f"=== Address {address} on {port} at {baud_rate} baud ===")
    try:
        transport.open()
        for suffix, purpose in QUERIES:
            command = f"{address}{suffix}"
            try:
                reply = transport.request(command)
            except Exception as error:
                report.line(
                    f"{command:<10} -> {type(error).__name__}: {error}   ({purpose})"
                )
                continue
            report.line(f"{command:<10} -> {reply!r}   ({purpose})")
    finally:
        transport.close()


def find_bus(
    port: str,
    preferred_baud_rate: int,
    report: Report,
) -> tuple[int, tuple[str, ...]]:
    """Find the baud rate at which this bus answers, and who answers on it."""

    rates = (preferred_baud_rate, *(
        rate for rate in FALLBACK_BAUD_RATES if rate != preferred_baud_rate
    ))
    for rate in rates:
        addresses = responding_addresses(port, rate, report)
        if addresses:
            if rate != preferred_baud_rate:
                report.line(
                    f"Note: this bus answers at {rate} baud, not the "
                    f"{preferred_baud_rate} the app uses by default. The COM "
                    "port settings in Device Setup need to match."
                )
            return rate, addresses
    return preferred_baud_rate, ()


def run(parsed: argparse.Namespace, report: Report) -> int:
    port = parsed.port or choose_port(report)
    if not port:
        report.line("No port given; nothing was opened.")
        return 1

    baud_rate = parsed.baud_rate
    addresses = [item.strip().upper() for item in parsed.addresses]
    if addresses:
        report.line(f"Using port {port} at {baud_rate} baud.")
    else:
        baud_rate, found = find_bus(port, baud_rate, report)
        addresses = list(found)
    if not addresses:
        report.line(
            "No Alicat answered at any tried baud rate. Check the BB3 cable "
            "and that the units are in polling mode rather than streaming."
        )
        return 1

    for address in addresses:
        if len(address) != 1 or not "A" <= address <= "Z":
            report.line(f"Skipping {address!r}: an address is one letter A-Z")
            continue
        dump_address(port, address, baud_rate, parsed.timeout, report)
        if parsed.explore:
            explore_address(port, address, baud_rate, parsed.timeout, report)
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", nargs="?", help="COM port, for example COM5")
    parser.add_argument(
        "addresses",
        nargs="*",
        help="Alicat addresses, for example A C. Omit to use every address "
        "that answers.",
    )
    parser.add_argument("--baud-rate", type=int, default=DEFAULT_BAUD_RATE)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument(
        "--explore",
        action="store_true",
        help="Also sweep every statistic and register, read-only. Slower.",
    )
    parsed = parser.parse_args(arguments)
    interactive = parsed.port is None

    if interactive and not parsed.explore:
        parsed.explore = ask(
            "Also sweep all statistics and registers? Slower, still read-only "
            "[y/N]: "
        ).lower().startswith("y")

    report = Report()
    report.line("Alicat read-only query dump")
    report.line(f"Run at {datetime.now().isoformat(timespec='seconds')}")
    report.line("No command sent below changes any instrument setting.")
    report.line()

    try:
        exit_code = run(parsed, report)
    except Exception as error:
        report.line(f"\nFAILED: {type(error).__name__}: {error}")
        exit_code = 1

    # The report is saved whatever happened: a failed run is exactly the one
    # worth sending on.
    saved = report.save()
    if saved is not None:
        print(f"\nSaved this dump to:\n  {saved}\nSend that file on.")
    else:
        print("\nThe dump could not be saved to a file; copy the text above.")
    if interactive:
        ask("\nPress Enter to close.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
