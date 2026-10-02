from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from math import isfinite

from rig_control.devices.alicat.bus import AlicatBus
from rig_control.models import Quality
from rig_control.devices.alicat.protocol_fields import AlicatFrameField
from rig_control.devices.dump_report import DumpQuery
from rig_control.devices.alicat.verification import (
    AlicatControlConfiguration,
    AlicatControlMode,
    ABSOLUTE_PRESSURE_VARIABLE,
    addressed_tokens,
    parse_control_configuration,
    parse_control_mode,
)


__all__ = [
    "AlicatFrameField",
    "AlicatEngineeringUnits",
    "AlicatInstrumentState",
    "AlicatProtocolClient",
    "AlicatAsciiProtocolClient",
]


@dataclass(frozen=True, slots=True)
class AlicatEngineeringUnits:
    """Units configured on one instrument for its serial data frame."""

    mass_flow: str
    volumetric_flow: str
    absolute_pressure: str
    gas_temperature: str
    setpoint: str
    totalized_flow: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "mass_flow",
            "volumetric_flow",
            "absolute_pressure",
            "gas_temperature",
            "setpoint",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Alicat {name.replace('_', ' ')} unit cannot be empty"
                )

        if self.totalized_flow is not None and (
            not isinstance(self.totalized_flow, str)
            or not self.totalized_flow.strip()
        ):
            raise ValueError(
                "Alicat totalized flow unit must be non-empty when supplied"
            )


@dataclass(frozen=True, slots=True)
class AlicatInstrumentState:
    """One complete, already-decoded status response from an Alicat MFC."""

    mass_flow: float
    mass_flow_unit: str
    volumetric_flow: float
    volumetric_flow_unit: str
    absolute_pressure: float
    pressure_unit: str
    gas_temperature: float
    temperature_unit: str
    setpoint: float
    setpoint_unit: str
    gas: str | None = None
    totalized_flow: float | None = None
    totalized_flow_unit: str | None = None
    status_codes: tuple[str, ...] = ()
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    quality: Quality = Quality.GOOD
    valve_drive_percent: float | None = None

    def __post_init__(self) -> None:
        if self.valve_drive_percent is not None:
            value = self.valve_drive_percent
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Valve drive must be a finite percentage from 0 to 100")
        for name in (
            "mass_flow",
            "volumetric_flow",
            "absolute_pressure",
            "gas_temperature",
            "setpoint",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Alicat {name.replace('_', ' ')} must be numeric")
            if not isfinite(float(value)):
                raise ValueError(f"Alicat {name.replace('_', ' ')} must be finite")

        for name in (
            "mass_flow_unit",
            "volumetric_flow_unit",
            "pressure_unit",
            "temperature_unit",
            "setpoint_unit",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Alicat {name.replace('_', ' ')} cannot be empty")

        if self.gas is not None and (
            not isinstance(self.gas, str) or not self.gas.strip()
        ):
            raise ValueError("Alicat gas must be non-empty text when supplied")

        if self.totalized_flow is not None:
            if isinstance(self.totalized_flow, bool) or not isinstance(
                self.totalized_flow,
                (int, float),
            ):
                raise TypeError("Alicat totalized flow must be numeric")
            if not isfinite(float(self.totalized_flow)):
                raise ValueError("Alicat totalized flow must be finite")
            if (
                not isinstance(self.totalized_flow_unit, str)
                or not self.totalized_flow_unit.strip()
            ):
                raise ValueError(
                    "Alicat totalized flow unit is required with its value"
                )
        elif self.totalized_flow_unit is not None:
            raise ValueError(
                "Alicat totalized flow value is required with its unit"
            )

        if not isinstance(self.status_codes, tuple) or any(
            not isinstance(code, str) or not code.strip()
            for code in self.status_codes
        ):
            raise TypeError("Alicat status codes must be non-empty text")

        if not isinstance(self.timestamp, datetime):
            raise TypeError("Alicat timestamp must be a datetime")
        if not isinstance(self.quality, Quality):
            raise TypeError("Alicat quality must be a Quality value")


class AlicatProtocolClient(ABC):
    """Decoded Alicat operations used by the rig-facing adapter.

    A later protocol implementation can use our serial bus or wrap a
    third-party package without exposing that choice to the rest of the rig.
    """

    def connect(self) -> None:
        """Acquire communication resources when the implementation owns any."""

    def disconnect(self) -> None:
        """Release communication resources owned by this client."""

    def read_control_configuration(self, unit_address: str) -> AlicatControlConfiguration:
        raise NotImplementedError("This protocol cannot read Alicat control configuration")

    def read_control_mode(self, unit_address: str) -> AlicatControlMode:
        """Read only the live loop variable, units and inverse flag.

        Measurement polling uses this small check; the full identity and
        data-frame queries belong to setup, not to every reading.
        """

        return self.read_control_configuration(unit_address).mode

    def read_setup_report(self, unit_address: str) -> tuple[DumpQuery, ...]:
        """Answer every documented setup query, for a settings dump."""

        raise NotImplementedError("This protocol cannot report its settings")

    def read_valve_drive(self, unit_address: str) -> tuple[float, ...]:
        """Return each valve's drive as a percentage of available power."""

        raise NotImplementedError("This protocol cannot read valve drive")

    def set_pressure_setpoint(self, unit_address: str, value: float) -> None:
        raise NotImplementedError("This protocol does not support pressure setpoints")

    def read_setpoint(self, unit_address: str) -> tuple[float, str]:
        raise NotImplementedError("This protocol cannot verify setpoints")

    def hold_closed(self, unit_address: str) -> AlicatInstrumentState:
        raise NotImplementedError("Valve hold is unsupported")

    def cancel_hold(self, unit_address: str) -> AlicatInstrumentState:
        raise NotImplementedError("Valve hold is unsupported")

    @abstractmethod
    def read_state(self, unit_address: str) -> AlicatInstrumentState:
        """Read and decode the current state of one addressed MFC."""

    @abstractmethod
    def set_flow_setpoint(self, unit_address: str, flow: float) -> None:
        """Send a mass-flow setpoint to one addressed MFC."""


#: Everything setup reads from one Alicat, in the order a dump reports it.
#: Each entry is a command suffix with no argument, which is what makes the
#: whole sequence read-only. Sources: Alicat Serial Communications Primer,
#: February 2023 rev. 2; Alicat inverse-pressure-control tutorial.
SETUP_QUERIES: tuple[tuple[str, str], ...] = (
    ("", "status frame (polling reply for this address)"),
    ("VE", "firmware version"),
    ("??M*", "manufacturer/identity table"),
    ("??D*", "data-frame description (column order and units)"),
    ("LR", "loop control variable, units and range"),
    ("R20", "register 20 (bit 32768 = inverse control)"),
    ("R122", "register 122 (control point on some firmware)"),
    ("LS", "current setpoint and its units"),
    ("FPF 2", "absolute-pressure full scale"),
    ("VD", "valve drive percentages (8v18+)"),
    ("LCA", "loop control algorithm: 1 PD/PDF, 2 PD2I (10v05+)"),
    ("LCG", "PD2I gains (10v05+)"),
    ("LCGD", "PD/PDF gains (10v05+)"),
    ("LCVO", "valve offsets: initial and closed (10v05+)"),
    ("LCDB", "deadband limit (10v05+)"),
    ("LCDM", "deadband mode: 1 hold, 2 close (10v05+)"),
    ("LCZA", "zero pressure control (10v05+)"),
    ("LSS", "setpoint source: A analog, S or U serial/display (10v05+)"),
    ("SR", "max ramp rate (7v11+)"),
    ("LSRC", "ramping options (10v05+)"),
)


_BAD_QUALITY_CODES = {
    "ADC",
    "MOV",
    "OPL",
    "POV",
    "TMF",
    "TOV",
    "VOV",
}


class AlicatAsciiProtocolClient(AlicatProtocolClient):
    """Alicat ASCII commands over one shared, serialized bus.

    Field order is supplied explicitly because Alicat data frames are
    configurable. It must come from applicable documentation, configuration,
    or diagnostic output for the actual instrument.
    """

    def __init__(
        self,
        bus: AlicatBus,
        frame_fields: tuple[AlicatFrameField, ...],
        units: AlicatEngineeringUnits,
        *,
        requires_setpoint: bool = True,
    ) -> None:
        if not isinstance(bus, AlicatBus):
            raise TypeError("bus must be an AlicatBus")
        if not isinstance(frame_fields, tuple) or any(
            not isinstance(item, AlicatFrameField) for item in frame_fields
        ):
            raise TypeError(
                "frame_fields must be a tuple of AlicatFrameField values"
            )
        if len(set(frame_fields)) != len(frame_fields):
            raise ValueError("Alicat frame fields cannot contain duplicates")

        required = {
            AlicatFrameField.ABSOLUTE_PRESSURE,
            AlicatFrameField.GAS_TEMPERATURE,
            AlicatFrameField.VOLUMETRIC_FLOW,
            AlicatFrameField.MASS_FLOW,
        }
        if requires_setpoint:
            required.add(AlicatFrameField.SETPOINT)
        missing = required.difference(frame_fields)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"Alicat frame is missing required fields: {names}")
        if not isinstance(units, AlicatEngineeringUnits):
            raise TypeError("units must be AlicatEngineeringUnits")
        if (
            AlicatFrameField.TOTALIZED_FLOW in frame_fields
            and units.totalized_flow is None
        ):
            raise ValueError(
                "A totalized-flow frame field requires its configured unit"
            )

        self._bus = bus
        self._frame_fields = frame_fields
        self._units = units
        self._requires_setpoint = requires_setpoint
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            raise RuntimeError("Alicat protocol client is already connected")
        self._bus.acquire()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._bus.release()
        self._connected = False

    def read_state(self, unit_address: str) -> AlicatInstrumentState:
        address = self._validate_address(unit_address)
        response = self._bus.request(address)
        return self.parse_state(address, response)

    def parse_state(
        self,
        unit_address: str,
        response: str,
    ) -> AlicatInstrumentState:
        """Decode a captured response using the configured frame layout."""

        address = self._validate_address(unit_address)
        if not isinstance(response, str) or not response.strip():
            raise ValueError("Alicat response must be non-empty text")
        return self._parse_state(address, response)

    def set_flow_setpoint(self, unit_address: str, flow: float) -> None:
        address = self._validate_address(unit_address)
        if isinstance(flow, bool) or not isinstance(flow, (int, float)):
            raise TypeError("Alicat flow setpoint must be numeric")
        if not isfinite(float(flow)):
            raise ValueError("Alicat flow setpoint must be finite")

        # SerialTextTransport owns the carriage-return line termination.
        self._bus.request(f"{address}S{float(flow):g}")

    def read_control_configuration(self, unit_address: str) -> AlicatControlConfiguration:
        """Read identity, firmware, control mode and data frame once, for setup."""

        address = self._validate_address(unit_address)
        firmware = self._bus.request(f"{address}VE")
        identity = self._optional_request(f"{address}??M*")
        loop = self._bus.request(f"{address}LR")
        register = self._bus.request(f"{address}R20")
        frame = self._bus.request(f"{address}??D*")
        observed = parse_control_configuration(address, firmware, identity, loop, register, frame)
        if observed.loop_variable == ABSOLUTE_PRESSURE_VARIABLE and observed.maximum_setpoint is None:
            bounds = self._read_pressure_full_scale(address, observed.setpoint_unit)
            observed = replace(observed, minimum_setpoint=bounds[0], maximum_setpoint=bounds[1])
        return observed

    def read_control_mode(self, unit_address: str) -> AlicatControlMode:
        """Read the live control variable, units and inverse flag only."""

        address = self._validate_address(unit_address)
        mode = parse_control_mode(
            address,
            self._bus.request(f"{address}LR"),
            self._bus.request(f"{address}R20"),
        )
        if mode.loop_variable == ABSOLUTE_PRESSURE_VARIABLE and mode.maximum_setpoint is None:
            minimum, maximum = self._read_pressure_full_scale(address, mode.setpoint_unit)
            mode = replace(mode, minimum_setpoint=minimum, maximum_setpoint=maximum)
        return mode

    def _read_pressure_full_scale(self, address: str, setpoint_unit: str) -> tuple[float, float]:
        # Firmware 9v reports units but not LR bounds. Read the absolute
        # pressure sensor range (statistic 2), never infer it from model size.
        from rig_control.devices.pressure_controller import absolute_unit_factor

        parts = addressed_tokens(address, self._bus.request(f"{address}FPF 2"))
        if len(parts) != 3:
            raise ValueError("Unrecognised absolute-pressure full-scale response")
        maximum = float(parts[0])
        int(parts[1])
        if not isfinite(maximum) or maximum <= 0:
            raise ValueError("Invalid absolute-pressure full scale")
        maximum *= absolute_unit_factor(parts[2]) / absolute_unit_factor(setpoint_unit)
        return 0.0, maximum

    def read_setup_report(self, unit_address: str) -> tuple[DumpQuery, ...]:
        """Answer every documented setup query, recording what came back.

        A query that does not answer is recorded as a failure rather than
        stopping the report: a partial dump is still evidence.
        """

        address = self._validate_address(unit_address)
        report: list[DumpQuery] = []
        for suffix, purpose in SETUP_QUERIES:
            command = f"{address}{suffix}"
            try:
                report.append(DumpQuery(command, purpose, self._bus.request(command)))
            except Exception as error:
                report.append(
                    DumpQuery(
                        command,
                        purpose,
                        f"{type(error).__name__}: {error}",
                        failed=True,
                    )
                )
        return tuple(report)

    def read_valve_drive(self, unit_address: str) -> tuple[float, ...]:
        """Read the valve drive state with the documented VD query.

        Serial Primer p. 25: the reply is the unit ID followed by one
        percentage per valve, upstream first on a dual-valve controller. This
        is how much of the available power is driven to the valve, not how
        far open it is, so it rises as a valve is asked to open further.

        Reading this needs no change to the instrument's data frame, which is
        why it is preferred over adding a valve-drive column.
        """

        address = self._validate_address(unit_address)
        parts = addressed_tokens(address, self._bus.request(f"{address}VD"))
        if not parts:
            raise ValueError("Empty valve-drive reply")
        drives = tuple(float(token) for token in parts)
        if any(not isfinite(value) for value in drives):
            raise ValueError("Non-finite valve drive")
        return drives

    def _optional_request(self, command: str) -> str:
        """Query informational text that must never block mode detection."""

        try:
            return self._bus.request(command)
        except (TimeoutError, ValueError, OSError, RuntimeError) as error:
            # The bus wraps transport failures, so catch that too: identity
            # text must never stand between the operator and mode detection.
            return f"(identity unavailable: {type(error).__name__})"

    def hold_closed(self, unit_address: str) -> AlicatInstrumentState:
        address = self._validate_address(unit_address)
        state = self.parse_state(address, self._bus.request(f"{address}HC"))
        if "HLD" not in state.status_codes:
            raise RuntimeError("Valve-close hold was not acknowledged (HLD missing)")
        return state

    def cancel_hold(self, unit_address: str) -> AlicatInstrumentState:
        address = self._validate_address(unit_address)
        state = self.parse_state(address, self._bus.request(f"{address}C"))
        if "HLD" in state.status_codes:
            raise RuntimeError("Valve hold remains active")
        return state

    def set_pressure_setpoint(self, unit_address: str, value: float) -> None:
        address = self._validate_address(unit_address)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError("Pressure setpoint must be finite and numeric")
        # LS without a units argument preserves the verified instrument units.
        self._bus.request(f"{address}LS {float(value):.12g}")

    def read_setpoint(self, unit_address: str) -> tuple[float, str]:
        address = self._validate_address(unit_address)
        parts = addressed_tokens(address, self._bus.request(f"{address}LS"))
        if len(parts) != 4:
            raise ValueError("Unrecognised Alicat LS reply")
        current, requested = float(parts[0]), float(parts[1])
        if not isfinite(current) or not isfinite(requested):
            raise ValueError("Non-finite Alicat setpoint")
        int(parts[2])
        return requested, parts[3]

    def _parse_state(
        self,
        expected_address: str,
        response: str,
    ) -> AlicatInstrumentState:
        tokens = response.split()
        expected_length = len(self._frame_fields) + 1
        if len(tokens) < expected_length:
            raise ValueError(
                f"Alicat response {response!r} has too few fields for "
                f"configured frame {self._frame_fields!r}"
            )
        if tokens[0].upper() != expected_address:
            raise ValueError(
                f"Alicat response address {tokens[0]!r} does not match "
                f"requested address {expected_address!r}: {response!r}"
            )

        values: dict[AlicatFrameField, float | str] = {}
        for field_name, token in zip(
            self._frame_fields,
            tokens[1:expected_length],
            strict=True,
        ):
            if field_name is AlicatFrameField.GAS:
                values[field_name] = token
                continue
            try:
                values[field_name] = float(token)
            except ValueError as error:
                raise ValueError(
                    f"Alicat response field {field_name.value!r} is not "
                    f"numeric in response {response!r}"
                ) from error

        status_codes = tuple(
            token.upper() for token in tokens[expected_length:]
        )
        for token in status_codes:
            try:
                float(token)
            except ValueError:
                continue
            raise ValueError("Unexpected numeric frame field; verify the configured frame layout")
        quality = (
            Quality.BAD
            if _BAD_QUALITY_CODES.intersection(status_codes)
            else Quality.GOOD
        )
        totalized_flow = values.get(AlicatFrameField.TOTALIZED_FLOW)

        return AlicatInstrumentState(
            mass_flow=float(values[AlicatFrameField.MASS_FLOW]),
            mass_flow_unit=self._units.mass_flow,
            volumetric_flow=float(
                values[AlicatFrameField.VOLUMETRIC_FLOW]
            ),
            volumetric_flow_unit=self._units.volumetric_flow,
            absolute_pressure=float(
                values[AlicatFrameField.ABSOLUTE_PRESSURE]
            ),
            pressure_unit=self._units.absolute_pressure,
            gas_temperature=float(
                values[AlicatFrameField.GAS_TEMPERATURE]
            ),
            temperature_unit=self._units.gas_temperature,
            setpoint=float(values.get(AlicatFrameField.SETPOINT, 0.0)),
            setpoint_unit=self._units.setpoint,
            gas=(
                str(values[AlicatFrameField.GAS])
                if AlicatFrameField.GAS in values
                else None
            ),
            totalized_flow=(
                float(totalized_flow)
                if totalized_flow is not None
                else None
            ),
            totalized_flow_unit=(
                self._units.totalized_flow
                if totalized_flow is not None
                else None
            ),
            status_codes=status_codes,
            quality=quality,
            valve_drive_percent=(float(values[AlicatFrameField.VALVE_DRIVE])
                                 if AlicatFrameField.VALVE_DRIVE in values else None),
        )

    @staticmethod
    def _validate_address(unit_address: str) -> str:
        if not isinstance(unit_address, str):
            raise TypeError("Alicat unit address must be text")
        address = unit_address.strip().upper()
        if len(address) != 1 or not "A" <= address <= "Z":
            raise ValueError("Alicat unit address must be one letter A-Z")
        return address
