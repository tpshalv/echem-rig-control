"""Read-only Alicat configuration verification.

Sources: Alicat Serial Communications Primer, February 2023 rev. 2,
pp. 15, 19, 21; Alicat inverse-pressure-control tutorial (register 20).
No configuration write commands belong in this module.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
import re


@dataclass(frozen=True, slots=True)
class AlicatControlConfiguration:
    serial_number: str
    model: str
    firmware: str
    loop_variable: int
    setpoint_unit: str
    inverse: bool
    minimum_setpoint: float | None
    maximum_setpoint: float | None
    frame_description: str
    checked_at: datetime

    @property
    def description(self) -> str:
        variable = {34: "absolute pressure", 37: "mass flow"}.get(self.loop_variable, f"variable {self.loop_variable}")
        return f"{variable}; {'inverse' if self.inverse else 'forward'}; {self.setpoint_unit}; serial {self.serial_number}"


def addressed_tokens(address: str, response: str) -> list[str]:
    tokens = response.split()
    if not tokens or tokens[0].upper() != address.upper():
        raise ValueError(f"Unexpected Alicat response address: {response!r}")
    return tokens[1:]


def parse_control_configuration(address: str, firmware: str, identity: str,
                                loop: str, register: str, frame: str) -> AlicatControlConfiguration:
    from math import isfinite

    version = " ".join(addressed_tokens(address, firmware))
    match = re.search(r"\b(\d+)v(\d+)", version, re.I)
    if match is None or int(match[1]) < 9:
        raise ValueError("Control verification requires documented 9v00+ queries; legacy firmware is unverified")

    def identity_field(pattern: str) -> str:
        found = re.search(pattern, identity, re.I | re.M)
        if found is None:
            raise ValueError("Alicat identity is incomplete; model and serial number are required")
        return found[1].strip()

    serial = identity_field(r"\bSerial\s*(?:Number|No\.?|#)\s*[:=]?\s*([\w-]+)")
    model = identity_field(r"\bModel\s*[:=]?\s*(MC[\w./-]*)")
    parts = addressed_tokens(address, loop)
    if len(parts) not in {3, 5}:
        raise ValueError(f"Unrecognised loop-range reply: {loop!r}")
    variable = int(parts[0])
    int(parts[1])  # Engineering-unit code must be present, even though labels are used.
    minimum, maximum = (float(parts[3]), float(parts[4])) if len(parts) == 5 else (None, None)
    if minimum is not None and (not isfinite(minimum) or not isfinite(maximum) or minimum >= maximum):
        raise ValueError("Invalid instrument setpoint range")
    register_text = " ".join(addressed_tokens(address, register))
    reg = re.fullmatch(r"(?:R?20\s*(?:=|:)\s*)?(\d+)", register_text, re.I)
    if reg is None or not 0 <= int(reg[1]) <= 65535:
        raise ValueError(f"Unrecognised register 20 reply: {register!r}")
    if not frame.strip() or "?" == frame.strip():
        raise ValueError("Frame description unavailable")
    return AlicatControlConfiguration(serial, model, version, variable, parts[2],
                                     bool(int(reg[1]) & 32768), minimum, maximum,
                                     frame, datetime.now(UTC))


def frame_signature(description: str) -> str:
    import hashlib
    normalized = " ".join(description.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_role(observed: AlicatControlConfiguration, *, bpr: bool,
                expected_serial: str | None, expected_frame_signature: str | None,
                flow_unit: str, downstream_confirmed: bool = False) -> None:
    if not expected_serial or observed.serial_number != expected_serial:
        raise ValueError("Serial identity is uncommissioned or mismatched; run read-only commissioning in Device Setup")
    if not expected_frame_signature or frame_signature(observed.frame_description) != expected_frame_signature:
        raise ValueError("Data frame/units are uncommissioned or changed; verify the frame in Device Setup")
    expected = 34 if bpr else 37
    if observed.loop_variable != expected or observed.inverse != bpr:
        raise ValueError(f"Configuration mismatch: expected {'BPR (absolute pressure, inverse)' if bpr else 'MFC (mass flow, forward)'}; "
                         f"detected {observed.description}. Configure the instrument manually, then reconnect.")
    if bpr:
        from rig_control.devices.pressure_controller import absolute_unit_factor
        absolute_unit_factor(observed.setpoint_unit)
        if observed.minimum_setpoint is None or observed.maximum_setpoint is None:
            raise ValueError("Instrument pressure range could not be verified")
    if bpr and not downstream_confirmed:
        raise ValueError("Downstream valve placement has not been confirmed in Device Setup")
    if not bpr and observed.setpoint_unit.casefold() != flow_unit.casefold():
        raise ValueError("Reported setpoint unit does not match the configured flow unit")
