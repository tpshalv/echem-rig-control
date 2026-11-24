"""How the v2 Operation screen arranges channel rows: quantities down, devices across.

The same quantity read by different devices sits on one line (mass flow for
every MFC, pressure for every gauge) so they can be compared at a glance,
and each setpoint is shown with the reading it controls rather than on a
line of its own.  Nothing here knows about Tk, so it is tested directly.

Channels this module has never heard of still appear: they get a line of
their own, after the known ones, so a new sensor is visible straight away.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from rig_control.calibrated_channels import CURRENT_DENSITY_CHANNEL
from rig_control.ui.operation.model import OperationChannelRow

#: A setpoint channel and the reading it controls on the same device.
CONTROL_FOR_READING = {
    "setpoint": "mass_flow",
    "pressure_setpoint_absolute": "absolute_pressure",
    "current_limit": "current",
    "current_density_setpoint": CURRENT_DENSITY_CHANNEL,
    "voltage_setpoint": "voltage",
    "target_temperature": "temperature",
    "target_speed": "stir_speed",
    "target_setpoint": "process_value",
}

#: The order lines appear in; anything not listed follows, by name.
LINE_ORDER = (
    # Electrical
    "voltage", "current", CURRENT_DENSITY_CHANNEL, "power", "output_enabled",
    "operating_mode", "regulation_mode",
    # Gas
    "mass_flow", "volumetric_flow", "absolute_pressure", "pressure", "measurement",
    "gas_temperature", "valve_drive_percent", "totalized_flow", "gas",
    "pressure_ceiling", "resume_pressure_control",
    # Thermal
    "temperature", "probe_temperature", "process_value", "active_setpoint",
    "humidity", "heating_enabled", "stir_speed", "stirring_enabled",
    "operating_mode_readback",
    # Pumps
    "speed_setpoint", "flow_setpoint", "direction", "running",
    # Controller
    "safe_state_active", "watchdog_tripped", "watchdog_rearm",
)

#: Line names for channels whose per-device names differ only in wording.
LINE_LABELS = {
    "voltage": "Voltage",
    "current": "Current",
    CURRENT_DENSITY_CHANNEL: "Current density",
    "output_enabled": "Output",
    "operating_mode": "Mode",
    "mass_flow": "Mass flow",
    "volumetric_flow": "Volumetric flow",
    "absolute_pressure": "Pressure (abs)",
    "gas_temperature": "Gas temperature",
    "valve_drive_percent": "Valve drive",
    "totalized_flow": "Totalised flow",
    "gas": "Gas",
    "humidity": "Humidity",
    "process_value": "Temperature",
    "stir_speed": "Stir speed",
    "speed_setpoint": "Pump speed",
    "flow_setpoint": "Pump flow (cal.)",
}

#: The few lines worth seeing on the Overview tab, from every system.
OVERVIEW_LINES = frozenset({
    "voltage", "current", CURRENT_DENSITY_CHANNEL, "output_enabled",
    "mass_flow", "absolute_pressure", "pressure", "measurement",
    "temperature", "process_value", "humidity", "stir_speed",
    "speed_setpoint", "running",
})

#: Text values longer than this are notes under the grid, not cells in it.
NOTE_LENGTH = 22
#: Channels that are always notes (long, explanatory text).
NOTE_CHANNELS = frozenset({"control_verification"})


@dataclass(frozen=True, slots=True)
class Cell:
    """One device's part of a line: what it reads, and the setting that drives it."""

    reading: OperationChannelRow | None = None
    control: OperationChannelRow | None = None

    @property
    def rows(self) -> tuple[OperationChannelRow, ...]:
        return tuple(row for row in (self.reading, self.control) if row is not None)


@dataclass(frozen=True, slots=True)
class Line:
    """One quantity across the devices that have it."""

    key: str
    label: str
    #: The unit every reading on the line shares, shown once by the label;
    #: empty when they differ, and each cell then shows its own.
    unit: str
    cells: dict[str, Cell] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SystemLayout:
    #: (device_id, device_name) in display order.
    devices: tuple[tuple[str, str], ...]
    lines: tuple[Line, ...]
    #: Long text rows, such as a control-mode explanation, shown under the grid.
    notes: tuple[OperationChannelRow, ...] = ()

    @property
    def shape(self) -> tuple:
        """Changes only when lines or devices come or go, i.e. when to rebuild."""
        return (
            self.devices,
            tuple((line.key, line.label, line.unit, tuple(
                (device_id, cell.reading is not None, cell.control is not None,
                 None if cell.control is None else cell.control.editor)
                for device_id, cell in line.cells.items()
            )) for line in self.lines),
            tuple(row.key for row in self.notes),
        )

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.notes


def severity(row: OperationChannelRow | None) -> str:
    """"" when fine (or not read yet), "warning" or "error" when it needs a look.

    Only a *reading* can be in error.  A setting marked bad is one that cannot
    be used yet (a density with no area entered, a BPR not ready); it is shown
    greyed out, not as an alarm.
    """
    if row is None or row.quality in ("", "good"):
        return ""
    if row.quality in ("warning", "uncertain"):
        return "warning"
    return "error" if row.timestamp is not None else ""


def needs_attention(rows: Iterable[OperationChannelRow]) -> str:
    """The worst severity among ``rows``."""
    worst = ""
    for row in rows:
        level = severity(row)
        if level == "error":
            return "error"
        worst = worst or level
    return worst


def _is_note(row: OperationChannelRow) -> bool:
    return row.channel in NOTE_CHANNELS or (
        isinstance(row.value, str) and row.editor != "action" and len(row.value) > NOTE_LENGTH
    )


def _order(key: str, label: str) -> tuple:
    return (LINE_ORDER.index(key), "") if key in LINE_ORDER else (len(LINE_ORDER), label.casefold())


def system_layout(
    rows: Iterable[OperationChannelRow], *, only: frozenset[str] | None = None,
) -> SystemLayout:
    """Arrange one system's rows; ``only`` keeps just those lines (for Overview)."""

    rows = tuple(rows)
    devices: dict[str, str] = {}
    by_key = {row.key: row for row in rows}
    notes: list[OperationChannelRow] = []
    paired: set[tuple[str, str]] = set()
    for row in rows:
        reading = CONTROL_FOR_READING.get(row.channel)
        if reading is not None and (row.device_id, reading) in by_key:
            paired.add(row.key)

    controlled = {(row.device_id, CONTROL_FOR_READING[row.channel]) for row in rows
                  if row.key in paired}
    # A device reporting under other channels than expected leaves an
    # expected-but-empty line behind; once it sends anything, drop those.
    reporting = {row.device_id for row in rows if row.timestamp is not None}

    lines: dict[str, dict[str, Cell]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        if (row.value is None and row.timestamp is None and not row.writable
                and row.editor != "action" and row.device_id in reporting
                and row.key not in controlled):
            continue
        if _is_note(row):
            if only is None:
                notes.append(row)
                devices.setdefault(row.device_id, row.device_name)
            continue
        if row.key in paired:
            continue  # Shown with its reading below.
        if only is not None and row.channel not in only:
            continue
        devices.setdefault(row.device_id, row.device_name)
        control = None
        reading: OperationChannelRow | None = row
        for control_channel, reading_channel in CONTROL_FOR_READING.items():
            if reading_channel == row.channel and (row.device_id, control_channel) in paired:
                control = by_key[(row.device_id, control_channel)]
                break
        if row.writable or row.editor == "action":
            # A setting with no reading of its own: the cell is the control.
            reading, control = None, row
        lines.setdefault(row.channel, {})[row.device_id] = Cell(reading, control)
        labels.setdefault(row.channel, LINE_LABELS.get(row.channel, row.channel_name))

    built = []
    for key, cells in lines.items():
        units = {cell.reading.unit for cell in cells.values() if cell.reading is not None}
        if not units:
            units = {cell.control.unit for cell in cells.values() if cell.control is not None}
        unit = units.pop() if len(units) == 1 else ""
        ordered = {device_id: cells[device_id] for device_id in devices if device_id in cells}
        built.append(Line(key, labels[key], unit, ordered))
    built.sort(key=lambda line: _order(line.key, line.label))
    used = {device_id for line in built for device_id in line.cells} | {row.device_id for row in notes}
    return SystemLayout(
        tuple((device_id, name) for device_id, name in devices.items() if device_id in used),
        tuple(built),
        tuple(notes),
    )


def format_value(row: OperationChannelRow | None) -> str:
    """The value as the screen shows it, without its unit."""
    if row is None or row.value is None:
        return "—"
    value = row.value
    if isinstance(value, bool):
        if row.channel == "watchdog_tripped":
            return "Tripped" if value else "OK"
        return "On" if value else "Off"
    if isinstance(value, str):
        if row.editor == "action":
            return value.split(" (")[0]
        return value.replace("_", " ").capitalize()
    number = float(value)
    if number != 0 and (abs(number) >= 1e6 or abs(number) < 1e-3):
        return f"{number:.3g}"
    return f"{number:.4g}" if abs(number) < 1000 else f"{number:,.0f}"


#: Units as they read best on screen; recordings keep the originals.
_UNIT_TEXT = {"degC": "°C", "degF": "°F"}


def unit_text(unit: str) -> str:
    return _UNIT_TEXT.get(unit, unit)


def control_prefix(row: OperationChannelRow) -> str:
    """"limit" for a supply limit (it is not what the supply regulates to), else "set"."""
    return "limit" if "limit" in row.channel_name.casefold() else "set"


def details(row: OperationChannelRow) -> str:
    """Hover text: what the value is, how good it is, and when it was read."""
    lines = [f"{row.device_name} · {row.channel_name}"]
    if row.equivalent:
        lines.append(f"= {row.equivalent}")
    if row.quality:
        lines.append(f"Quality: {row.quality}")
    if isinstance(row.timestamp, datetime):
        lines.append(f"Updated {row.timestamp.astimezone():%H:%M:%S}")
    elif not row.writable:
        lines.append("No reading yet")
    if row.writable and row.editor != "action":
        lines.append("Click to change")
    if row.minimum is not None or row.maximum is not None:
        low = "" if row.minimum is None else f"{row.minimum:g}"
        high = "" if row.maximum is None else f"{row.maximum:g}"
        lines.append(f"Range {low} – {high} {row.unit}".rstrip())
    return "\n".join(lines)
