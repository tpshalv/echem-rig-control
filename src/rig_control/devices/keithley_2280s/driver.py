from dataclasses import dataclass
from typing import Final

from rig_control.devices.keithley_2260b.driver import Keithley2260B
from rig_control.devices.keithley_2280s.configuration import _validate_rig_limits
from rig_control.devices.keithley_2280s.protocol import (
    Keithley2280SProtocol,
    KeithleyIdentity,
)
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.models import Measurement
from rig_control.power_supply_telemetry import REGULATION_MODE_UNIT
from rig_control.read_retry import retry_read
from rig_control.transports.scpi import ScpiTransport


@dataclass(frozen=True, slots=True)
class HardwareLimits:
    """Rated model capabilities, independent of profile and run limits."""

    maximum_voltage: float
    maximum_current: float
    maximum_power: float


HARDWARE_LIMITS: Final = HardwareLimits(32.0, 6.0, 192.0)


class Keithley2280S(Keithley2260B):
    """2280S-32-6 using the 2260B lifecycle, caching and read retries.

    Power validation uses voltage setpoint times current limit, bounding the
    possible output power rather than relying on a measured load current.
    As with the 2260B, this assumes exclusive control of instrument settings.
    """

    _protocol = Keithley2280SProtocol
    _model_name = "Keithley 2280S-32-6"

    def __init__(
        self,
        device_id: str,
        limits: PowerSupplyLimits,
        transport: ScpiTransport,
        *,
        measurement_nplc: float | None = None,
        read_attempts: int = 3,
        read_retry_delay_seconds: float = 0.05,
    ) -> None:
        super().__init__(
            device_id, limits, transport, read_attempts=read_attempts,
            read_retry_delay_seconds=read_retry_delay_seconds,
        )
        self._measurement_nplc_command = (
            None if measurement_nplc is None
            else self._protocol.set_measurement_nplc(measurement_nplc)
        )

    @property
    def hardware_limits(self) -> HardwareLimits:
        return HARDWARE_LIMITS

    def _accepts_identity(self, identity: KeithleyIdentity) -> bool:
        model = identity.model.upper().removeprefix("MODEL ").strip()
        return model == "2280S-32-6" and "KEITHLEY" in identity.manufacturer.upper()

    def _configure_instrument(self) -> None:
        # Configure once per connection. Polling only fetches completed data,
        # avoiding MEASure's function switches and abort/initiate cycles.
        self._transport.write(self._protocol.CONCURRENT_MEASUREMENT)
        if self._measurement_nplc_command is not None:
            self._transport.write(self._measurement_nplc_command)
        self._transport.write(self._protocol.MEASUREMENT_FORMAT)
        self._transport.write(self._protocol.IMMEDIATE_ARM)
        self._transport.write(self._protocol.IMMEDIATE_TRIGGER)
        self._transport.write(self._protocol.CONTINUOUS_MEASUREMENT)
        # A saved falling delay must not defer the safe-state output-off.
        self._transport.write(self._protocol.DISABLE_OUTPUT_DELAY)

    def _validate_operating_point(self, voltage: float, current: float) -> None:
        _validate_rig_limits(self.limits)
        self._protocol._format_number(voltage)
        self._protocol._format_number(current)
        super()._validate_operating_point(voltage, current)
        for name, value, unit, configured, rated in (
            ("Voltage", voltage, "V", self.limits.maximum_voltage,
             self.hardware_limits.maximum_voltage),
            ("Current", current, "A", self.limits.maximum_current,
             self.hardware_limits.maximum_current),
            ("Power", voltage * current, "W", self.limits.maximum_power,
             self.hardware_limits.maximum_power),
        ):
            if value > rated:
                raise ValueError(
                    f"{name} {value} {unit} exceeds hardware maximum {rated} {unit}"
                )
            if value > configured:
                raise ValueError(
                    f"{name} {value} {unit} exceeds configured maximum "
                    f"{configured} {unit}"
                )

    def set_output_enabled(self, enabled: bool) -> None:
        self._require_ready()
        if enabled:
            self._validate_operating_point(self.voltage_setpoint, self.current_limit)
        super().set_output_enabled(enabled)

    def _require_measurement_ready(self) -> None:
        self._require_ready()
        if not self.output_enabled:
            raise RuntimeError("Keithley 2280S measurements require output enabled")

    def measure_voltage(self) -> Measurement:
        return self.read_measurements()[0].measurement

    def measure_current(self) -> Measurement:
        return self.read_measurements()[1].measurement

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        """Read both measured channels with one non-triggering fetch query."""
        self._require_measurement_ready()
        current, voltage, mode = retry_read(
            lambda: self._protocol.parse_measurements(self._transport.query(
                self._protocol.FETCH_QUERY
            )),
            attempts=self._read_attempts,
            initial_delay_seconds=self._read_retry_delay_seconds,
        )
        voltage_measurement = Measurement(voltage, "V")
        return (
            DeviceMeasurement("voltage", voltage_measurement),
            DeviceMeasurement("current", Measurement(
                current, "A", timestamp=voltage_measurement.timestamp,
            )),
            DeviceMeasurement("regulation_mode", Measurement(
                {"OFF": 0.0, "CC": 1.0, "CV": 2.0}[mode], REGULATION_MODE_UNIT,
                timestamp=voltage_measurement.timestamp,
            )),
        )

    def read_telemetry(self) -> tuple[DeviceMeasurement, ...]:
        # OUTP:STAT? (077085503, 7-59) observes trips as well as app commands.
        # It does not initiate readings or change the active measurement function.
        self._require_ready()
        self._output_enabled = retry_read(
            lambda: self._protocol.parse_boolean(self._transport.query(
                self._protocol.OUTPUT_STATE_QUERY)),
            attempts=self._read_attempts,
            initial_delay_seconds=self._read_retry_delay_seconds,
        )
        readings = super().read_telemetry()
        if not self.output_enabled:
            readings += (DeviceMeasurement("regulation_mode", Measurement(
                0.0, REGULATION_MODE_UNIT, timestamp=readings[0].measurement.timestamp,
            )),)
        return readings
