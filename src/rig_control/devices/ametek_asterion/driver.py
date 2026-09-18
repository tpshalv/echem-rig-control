from dataclasses import dataclass
from typing import Final

from rig_control.devices.ametek_asterion.protocol import (
    AmetekAsterionIdentity,
    AmetekAsterionProtocol,
)
from rig_control.devices.power_supply import PowerSupply, PowerSupplyLimits
from rig_control.models import DeviceStatus, Measurement
from rig_control.read_retry import retry_read
from rig_control.transports.scpi import ScpiTransport


@dataclass(frozen=True, slots=True)
class HardwareLimits:
    """Rated model capabilities, independent of profile and run limits."""

    maximum_voltage: float
    maximum_current: float
    maximum_power: float


HARDWARE_LIMITS_BY_MODEL: Final[dict[str, HardwareLimits]] = {}


class AmetekAsterion(PowerSupply):
    """Driver for AMETEK Sorensen Asterion DC Series power supplies."""

    _protocol = AmetekAsterionProtocol

    def __init__(
        self,
        device_id: str,
        limits: PowerSupplyLimits,
        transport: ScpiTransport,
        *,
        read_attempts: int = 3,
        read_retry_delay_seconds: float = 0.05,
    ) -> None:
        self._device_id = device_id
        self._limits = limits
        self._transport = transport
        self._status = DeviceStatus.DISCONNECTED
        self._identity: AmetekAsterionIdentity | None = None
        self._voltage_setpoint = 0.0
        self._current_limit = 0.0
        self._output_enabled = False
        self._read_attempts = read_attempts
        self._read_retry_delay_seconds = read_retry_delay_seconds

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def limits(self) -> PowerSupplyLimits:
        return self._limits

    @property
    def hardware_limits(self) -> HardwareLimits | None:
        if self._identity is None:
            return None
        return HARDWARE_LIMITS_BY_MODEL.get(self._normalise_model(self._identity.model))

    @property
    def identity(self) -> AmetekAsterionIdentity | None:
        """Return the identity reported by the connected instrument."""

        return self._identity

    @property
    def voltage_setpoint(self) -> float:
        return self._voltage_setpoint

    @property
    def current_limit(self) -> float:
        return self._current_limit

    @property
    def output_enabled(self) -> bool:
        return self._output_enabled

    def connect(self) -> None:
        """Connect and read the instrument's actual current state."""

        if self._transport.is_open:
            raise RuntimeError(
                f"Power supply {self.device_id!r} is already connected"
            )

        try:
            self._transport.open()

            identity = self._protocol.parse_identity(
                self._transport.query(self._protocol.IDENTIFY_QUERY)
            )

            if not self._accepts_identity(identity):
                raise RuntimeError(
                    "Connected instrument is not an AMETEK Sorensen "
                    "Asterion DC supply: "
                    f"manufacturer={identity.manufacturer!r}, "
                    f"model={identity.model!r}, "
                    f"serial_number={identity.serial_number!r}"
                )

            voltage = self._protocol.parse_number(
                self._transport.query(self._protocol.VOLTAGE_SETPOINT_QUERY)
            )
            current = self._protocol.parse_number(
                self._transport.query(self._protocol.CURRENT_LIMIT_QUERY)
            )
            output_enabled = self._protocol.parse_boolean(
                self._transport.query(self._protocol.OUTPUT_STATE_QUERY)
            )

            self._identity = identity
            self._validate_operating_point(voltage, current)
            self._voltage_setpoint = voltage
            self._current_limit = current
            self._output_enabled = output_enabled
            self._status = DeviceStatus.READY

        except Exception:
            self._transport.close()
            self._clear_local_state()
            raise

    def _accepts_identity(self, identity: AmetekAsterionIdentity) -> bool:
        manufacturer = identity.manufacturer.upper()
        model = identity.model.upper()
        vendor_ok = "AMETEK" in manufacturer or "SORENSEN" in manufacturer
        model_ok = "ASTERION" in model or model.startswith("AST")
        return vendor_ok and model_ok

    def disconnect(self) -> None:
        """Request a safe state and close the connection."""

        if not self._transport.is_open:
            self._clear_local_state()
            return

        try:
            self.enter_safe_state()
        finally:
            self._transport.close()
            self._clear_local_state()

    def set_voltage(self, voltage: float) -> None:
        self._require_ready()
        self._validate_operating_point(voltage, self._current_limit)

        self._transport.write(self._protocol.set_voltage(voltage))
        self._voltage_setpoint = float(voltage)

    def set_current_limit(self, current: float) -> None:
        self._require_ready()
        self._validate_operating_point(self._voltage_setpoint, current)

        self._transport.write(self._protocol.set_current_limit(current))
        self._current_limit = float(current)

    def set_output_enabled(self, enabled: bool) -> None:
        self._require_ready()
        if enabled:
            self._validate_operating_point(self._voltage_setpoint, self._current_limit)

        self._transport.write(self._protocol.set_output_enabled(enabled))
        self._output_enabled = enabled

    def measure_voltage(self) -> Measurement:
        self._require_ready()

        value = retry_read(
            lambda: self._protocol.parse_number(
                self._transport.query(self._protocol.MEASURE_VOLTAGE_QUERY)
            ),
            attempts=self._read_attempts,
            initial_delay_seconds=self._read_retry_delay_seconds,
        )

        return Measurement(value, "V")

    def measure_current(self) -> Measurement:
        self._require_ready()

        value = retry_read(
            lambda: self._protocol.parse_number(
                self._transport.query(self._protocol.MEASURE_CURRENT_QUERY)
            ),
            attempts=self._read_attempts,
            initial_delay_seconds=self._read_retry_delay_seconds,
        )

        return Measurement(value, "A")

    def enter_safe_state(self) -> None:
        """Disable output and reduce both setpoints to zero."""

        if not self._transport.is_open:
            self._voltage_setpoint = 0.0
            self._current_limit = 0.0
            self._output_enabled = False
            return

        self._transport.write(self._protocol.set_output_enabled(False))
        self._output_enabled = False

        self._transport.write(self._protocol.set_voltage(0.0))
        self._voltage_setpoint = 0.0

        self._transport.write(self._protocol.set_current_limit(0.0))
        self._current_limit = 0.0

    def _require_ready(self) -> None:
        if self.status is not DeviceStatus.READY or not self._transport.is_open:
            raise RuntimeError(f"Power supply {self.device_id!r} is not ready")

    def _clear_local_state(self) -> None:
        self._identity = None
        self._voltage_setpoint = 0.0
        self._current_limit = 0.0
        self._output_enabled = False
        self._status = DeviceStatus.DISCONNECTED

    def _validate_operating_point(self, voltage: float, current: float) -> None:
        self._protocol._format_number(voltage)
        self._protocol._format_number(current)

        if voltage < 0:
            raise ValueError("Voltage cannot be negative")
        if current < 0:
            raise ValueError("Current cannot be negative")

        self._validate_limit("Voltage", voltage, "V", self.limits.maximum_voltage)
        self._validate_limit("Current", current, "A", self.limits.maximum_current)
        self._validate_limit("Power", voltage * current, "W", self.limits.maximum_power)

        hardware_limits = self.hardware_limits
        if hardware_limits is not None:
            self._validate_limit(
                "Voltage",
                voltage,
                "V",
                hardware_limits.maximum_voltage,
                limit_kind="hardware",
            )
            self._validate_limit(
                "Current",
                current,
                "A",
                hardware_limits.maximum_current,
                limit_kind="hardware",
            )
            self._validate_limit(
                "Power",
                voltage * current,
                "W",
                hardware_limits.maximum_power,
                limit_kind="hardware",
            )

    @staticmethod
    def _validate_limit(
        name: str,
        value: float,
        unit: str,
        limit: float,
        *,
        limit_kind: str = "configured",
    ) -> None:
        if value > limit:
            raise ValueError(
                f"{name} {value} {unit} exceeds {limit_kind} maximum "
                f"{limit} {unit}"
            )

    @staticmethod
    def _normalise_model(model: str) -> str:
        return model.upper().removeprefix("MODEL ").strip()
