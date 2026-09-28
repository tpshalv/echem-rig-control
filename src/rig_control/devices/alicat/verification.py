"""Read-only Alicat configuration reading.

Sources: Alicat Serial Communications Primer, February 2023 rev. 2,
pp. 15, 19, 21; Alicat inverse-pressure-control tutorial (register 20).
No configuration write commands belong in this module.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
import re

from rig_control.devices.alicat.protocol_fields import AlicatFrameField


#: Loop variables the software can operate. Gauge (38) and differential (39)
#: control need their own native-unit mapping and are deliberately excluded.
MASS_FLOW_VARIABLE = 37
ABSOLUTE_PRESSURE_VARIABLE = 34

#: Register 20, bit 32768: inverse (back-pressure) regulation. Alicat's own
#: tutorial enables it by adding 32768 to whatever register 20 already holds
#: (its worked example goes from 276 to 33044), so only this bit is read and
#: the rest of the register is left alone.
#:
#: That firmware setting follows a physical change: the valve must already be
#: downstream of the sensing section. Nothing on the wire can confirm that,
#: which is why adding a BPR asks the operator to acknowledge it.
INVERSE_CONTROL_BIT = 32768

_VARIABLE_NAMES = {
    ABSOLUTE_PRESSURE_VARIABLE: "absolute pressure",
    MASS_FLOW_VARIABLE: "mass flow",
    38: "gauge pressure",
    39: "differential pressure",
}


def role_from_loop(loop_variable: int, inverse: bool) -> str | None:
    """Return the software role implied by one live control configuration."""

    if loop_variable == ABSOLUTE_PRESSURE_VARIABLE and inverse:
        return "bpr"
    if loop_variable == MASS_FLOW_VARIABLE and not inverse:
        return "mfc"
    return None


def describe_loop(loop_variable: int, inverse: bool, setpoint_unit: str) -> str:
    variable = _VARIABLE_NAMES.get(loop_variable, f"variable {loop_variable}")
    return f"{variable}; {'inverse' if inverse else 'forward'}; {setpoint_unit}"


@dataclass(frozen=True, slots=True)
class AlicatControlMode:
    """The small live control state needed before every operating write."""

    loop_variable: int
    setpoint_unit: str
    inverse: bool
    minimum_setpoint: float | None = None
    maximum_setpoint: float | None = None

    @property
    def detected_role(self) -> str | None:
        return role_from_loop(self.loop_variable, self.inverse)

    @property
    def description(self) -> str:
        return describe_loop(self.loop_variable, self.inverse, self.setpoint_unit)


@dataclass(frozen=True, slots=True)
class AlicatControlConfiguration:
    """Everything read once during setup, including informational identity."""

    serial_number: str | None
    model: str | None
    firmware: str
    loop_variable: int
    setpoint_unit: str
    inverse: bool
    minimum_setpoint: float | None
    maximum_setpoint: float | None
    frame_description: str
    checked_at: datetime

    @property
    def mode(self) -> AlicatControlMode:
        return AlicatControlMode(
            self.loop_variable,
            self.setpoint_unit,
            self.inverse,
            self.minimum_setpoint,
            self.maximum_setpoint,
        )

    @property
    def detected_role(self) -> str | None:
        """Return the safe software role implied by live controller settings."""

        return role_from_loop(self.loop_variable, self.inverse)

    @property
    def description(self) -> str:
        identity = f"; serial {self.serial_number}" if self.serial_number else ""
        return describe_loop(self.loop_variable, self.inverse, self.setpoint_unit) + identity


def addressed_tokens(address: str, response: str) -> list[str]:
    tokens = response.split()
    if not tokens or tokens[0].upper() != address.upper():
        raise ValueError(f"Unexpected Alicat response address: {response!r}")
    return tokens[1:]


def parse_control_mode(address: str, loop: str, register: str) -> AlicatControlMode:
    """Decode the LR loop reply and register 20 into a live control mode.

    A 10v22 reply looks like ``A 37 +0.0000 +2.0000 7 SLPM``: the control
    variable, then the setpoint range, then the engineering-unit code and its
    label. The range is read by position between the variable and the unit,
    so a reply that omits it is still understood.

    Every failure quotes the instrument's own reply. Firmware differs in what
    it answers here, and an error that hides the text cannot be acted on.
    """

    parts = addressed_tokens(address, loop)
    if len(parts) < 3:
        raise ValueError(
            f"the loop query reply has {len(parts)} fields, too few to read a "
            f"control variable, unit code and unit: {loop!r}"
        )
    try:
        variable = int(parts[0])
        int(parts[-2])  # Engineering-unit code, even though the label is used.
    except ValueError as error:
        raise ValueError(
            f"the loop query reply is not <variable> [range] <unit code> "
            f"<unit>: {loop!r} ({error})"
        ) from error
    unit = parts[-1]
    bounds = parts[1:-2]
    if not bounds:
        minimum, maximum = None, None
    elif len(bounds) == 2:
        try:
            minimum, maximum = float(bounds[0]), float(bounds[1])
        except ValueError as error:
            raise ValueError(
                f"the loop query reported a non-numeric range: {loop!r}"
            ) from error
        if not isfinite(minimum) or not isfinite(maximum) or minimum >= maximum:
            raise ValueError(f"the loop query reported an invalid range: {loop!r}")
    else:
        raise ValueError(
            f"the loop query reply has {len(bounds)} unexpected fields between "
            f"the control variable and its unit: {loop!r}"
        )
    return AlicatControlMode(
        variable,
        unit,
        inverse_control_enabled(address, register),
        minimum,
        maximum,
    )


def inverse_control_enabled(address: str, register: str) -> bool:
    """Read the inverse (back-pressure) regulation bit from register 20.

    A 10v22 reply looks like ``A   020 = 9239``; the register number may be
    zero-padded, and older firmware answers with the value alone.
    """

    text = " ".join(addressed_tokens(address, register))
    found = re.fullmatch(r"(?:R?0*20\s*(?:=|:)\s*)?(\d+)", text.strip(), re.I)
    if found is None or not 0 <= int(found[1]) <= 65535:
        raise ValueError(f"unrecognised register 20 reply: {register!r}")
    return bool(int(found[1]) & INVERSE_CONTROL_BIT)


def parse_identity(identity: str) -> tuple[str | None, str | None]:
    """Extract optional model and serial labels; never raise on odd text.

    Identity is informational. Alicat firmware reports it in several shapes
    and some instruments answer the manufacturer query not at all, so this
    must never prevent the LR/R20 mode detection that follows.
    """

    if not isinstance(identity, str):
        return None, None

    def field(pattern: str) -> str | None:
        found = re.search(pattern, identity, re.I | re.M)
        return found[1].strip() if found is not None else None

    serial = field(r"\bSerial\s*(?:Number|No\.?|#)\s*[:=]?\s*([\w-]+)")
    model = field(r"\bModel\s*[:=]?\s*(M[CW][\w./-]*)")
    if model is None:
        compact = re.search(r"\b(M[CW][-\w./]*\d[-\w./]*)\b", identity, re.I)
        model = compact[1].strip() if compact is not None else None
    if serial is None:
        compact = re.search(r"\b(?:SN|S/N)\s*[:#=]?\s*([\w-]+)", identity, re.I)
        serial = compact[1].strip() if compact is not None else None
    return serial, model


def parse_control_configuration(address: str, firmware: str, identity: str,
                                loop: str, register: str, frame: str) -> AlicatControlConfiguration:
    version = " ".join(addressed_tokens(address, firmware))
    match = re.search(r"\b(\d+)v(\d+)", version, re.I)
    if match is None or int(match[1]) < 9:
        raise ValueError(
            "Reading the control configuration needs documented 9v00+ queries; "
            "this instrument reports older firmware"
        )
    serial, model = parse_identity(identity)
    mode = parse_control_mode(address, loop, register)
    if not frame.strip() or frame.strip() == "?":
        raise ValueError("Data-frame description unavailable")
    return AlicatControlConfiguration(
        serial, model, version, mode.loop_variable, mode.setpoint_unit,
        mode.inverse, mode.minimum_setpoint, mode.maximum_setpoint,
        frame, datetime.now(UTC),
    )


#: Checked in order, so a more specific label wins: "Mass Flow Setpt" is a
#: setpoint column, not a second mass-flow column.
_FRAME_LABELS: tuple[tuple[tuple[str, ...], AlicatFrameField], ...] = (
    (("setpt", "setpoint", "set point"), AlicatFrameField.SETPOINT),
    (("totaliser", "totalizer", "total"), AlicatFrameField.TOTALIZED_FLOW),
    (("abs press", "absolute press", "abs pressure"), AlicatFrameField.ABSOLUTE_PRESSURE),
    (("flow temp", "temperature", "temp"), AlicatFrameField.GAS_TEMPERATURE),
    (("volu flow", "vol flow", "volumetric"), AlicatFrameField.VOLUMETRIC_FLOW),
    (("mass flow",), AlicatFrameField.MASS_FLOW),
    (("valve drive", "valve"), AlicatFrameField.VALVE_DRIVE),
    (("gas",), AlicatFrameField.GAS),
)

_UNSUPPORTED_FRAME_LABELS = ("gauge press", "diff press", "differential press")

#: Lines that describe the table itself or a status string, not a column
#: this software reads as a measurement.
_IGNORED_FRAME_LINES = ("unit id", "*status", "*error", "name___")

#: Alicat writes degrees with a leading backtick.
_UNIT_ALIASES = {"`c": "degC", "c": "degC", "`f": "degF", "f": "degF", "k": "K"}

#: Fields whose column carries no engineering unit.
_UNITLESS_FIELDS = frozenset({AlicatFrameField.GAS})


@dataclass(frozen=True, slots=True)
class AlicatFrameColumn:
    """One data-frame column, its unit, and the precision it is reported in."""

    field: AlicatFrameField
    unit: str | None = None
    #: Decimal places this column is reported with, from the frame table's
    #: width/decimals field. None when the table did not state it.
    decimals: int | None = None

    @property
    def resolution(self) -> float | None:
        """The smallest change this column can express, if it is known."""

        return None if self.decimals is None else 10.0 ** -self.decimals


def column_resolution(
    columns: tuple[AlicatFrameColumn, ...] | None,
    field: AlicatFrameField,
) -> float | None:
    """Return what one least-significant digit of a column is worth."""

    for column in columns or ():
        if column.field is field:
            return column.resolution
    return None


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    text = unit.strip()
    return _UNIT_ALIASES.get(text.casefold(), text) if text else None


def parse_frame_layout(address: str, frame: str) -> tuple[AlicatFrameColumn, ...] | None:
    """Read the instrument's own data-frame description, if it is legible.

    A 10v22 table has one line per column, ending in that column's unit, for
    example ``A D05 005 Mass Flow  s decimal  7/4 007 02 SLPM``. Reading it is
    what keeps a measurement attached to the right name and unit instead of
    an assumed frame order. An unrecognised reply returns None and the caller
    falls back to the documented default order, which is then cross-checked
    against a live setpoint readback before anything is saved.
    """

    if not isinstance(frame, str) or not frame.strip():
        return None
    columns: list[AlicatFrameColumn] = []
    seen: set[AlicatFrameField] = set()
    for line in frame.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.split(maxsplit=1)[0].upper() == address.upper():
            text = text.split(maxsplit=1)[1] if " " in text else ""
        folded = text.casefold()
        if any(label in folded for label in _UNSUPPORTED_FRAME_LABELS):
            raise ValueError(
                "This instrument reports a gauge or differential pressure column, "
                "which this software does not read. Use an absolute-pressure "
                "instrument, or add that sensor separately."
            )
        if any(ignored in folded for ignored in _IGNORED_FRAME_LINES):
            continue
        for labels, field in _FRAME_LABELS:
            if not any(label in folded for label in labels):
                continue
            if field not in seen:
                seen.add(field)
                columns.append(
                    AlicatFrameColumn(
                        field, _column_unit(field, text), _column_decimals(text)
                    )
                )
            break
    required = {
        AlicatFrameField.ABSOLUTE_PRESSURE,
        AlicatFrameField.GAS_TEMPERATURE,
        AlicatFrameField.VOLUMETRIC_FLOW,
        AlicatFrameField.MASS_FLOW,
    }
    if not required.issubset(seen):
        return None
    return tuple(columns)


def _column_decimals(text: str) -> int | None:
    """Read a column's decimal places from its width/decimals field.

    A 10v22 line reads "s decimal 7/2 010 02 PSIA": seven characters wide
    with two decimal places, so this column can only express hundredths.
    """

    found = re.search(r"\b(\d+)\s*/\s*(\d+)\b", text)
    return int(found[2]) if found is not None else None


def _column_unit(field: AlicatFrameField, text: str) -> str | None:
    """Take the trailing unit from one column line, where there is one."""

    if field in _UNITLESS_FIELDS:
        return None
    tokens = text.split()
    if not tokens:
        return None
    last = tokens[-1]
    try:
        float(last)
    except ValueError:
        return normalize_unit(last)
    return None  # A numeric last field means no unit was printed.


def default_frame_layout(*, controller: bool) -> tuple[AlicatFrameField, ...]:
    """The documented default Alicat data frame, in reported order."""

    fields = [
        AlicatFrameField.ABSOLUTE_PRESSURE,
        AlicatFrameField.GAS_TEMPERATURE,
        AlicatFrameField.VOLUMETRIC_FLOW,
        AlicatFrameField.MASS_FLOW,
    ]
    if controller:
        fields.append(AlicatFrameField.SETPOINT)
    fields.append(AlicatFrameField.GAS)
    return tuple(fields)


def role_label(role: str) -> str:
    return "BPR (absolute pressure, inverse)" if role == "bpr" else "MFC (mass flow, forward)"


def verify_role(observed: AlicatControlConfiguration | AlicatControlMode, *, bpr: bool,
                flow_unit: str, downstream_confirmed: bool = False) -> None:
    """Block control unless the live configuration still matches the saved role.

    The instrument's live control configuration is authoritative. Model and
    serial number are informational and are deliberately not required here.
    """

    expected = "bpr" if bpr else "mfc"
    if observed.detected_role != expected:
        raise ValueError(
            f"Saved role does not match the instrument: this device is saved as "
            f"{role_label(expected)} but now reports {observed.description}. "
            "Reconfigure the instrument to its intended mode, or remove and "
            "re-add the device; control stays blocked until they agree."
        )
    if bpr:
        from rig_control.devices.pressure_controller import absolute_unit_factor

        absolute_unit_factor(observed.setpoint_unit)
        if observed.minimum_setpoint is None or observed.maximum_setpoint is None:
            raise ValueError("Instrument pressure range could not be read")
        if not downstream_confirmed:
            raise ValueError(
                "Back-pressure installation has not been acknowledged for this "
                "device. Use 'Acknowledge BPR installation' in Device Setup."
            )
    elif observed.setpoint_unit.casefold() != flow_unit.casefold():
        raise ValueError(
            f"Instrument reports setpoints in {observed.setpoint_unit!r} but this "
            f"device is saved with flow unit {flow_unit!r}"
        )
