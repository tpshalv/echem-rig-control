from datetime import UTC, datetime
from threading import RLock

from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.temperature_probe import TemperatureProbe
from rig_control.devices.tasi_ta612c.configuration import Ta612cConfiguration
from rig_control.devices.tasi_ta612c.protocol import (
    CHANNELS, Ta612cIdentity, Ta612cProtocol, decode_temperature,
)
from rig_control.models import DeviceStatus, Measurement


class Ta612cTemperatureProbe(TemperatureProbe):
    """Temperature probe speaking TA612C protocol, independent of physical brand."""

    def __init__(self, configuration: Ta612cConfiguration, protocol: Ta612cProtocol) -> None:
        self.configuration = configuration
        self._protocol = protocol
        self._status = DeviceStatus.DISCONNECTED
        self._identity: Ta612cIdentity | None = None
        self._lock = RLock()

    @property
    def device_id(self) -> str:
        return self.configuration.device_id

    @property
    def channels(self) -> tuple[str, ...]:
        return self.configuration.channels

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def identity(self) -> Ta612cIdentity | None:
        return self._identity

    def connect(self) -> None:
        with self._lock:
            if self._protocol.transport.is_open:
                raise RuntimeError("Temperature probe is already connected")
            self._status = DeviceStatus.CONNECTING
            try:
                self._protocol.transport.open()
                identity = self._protocol.identify()
                # Validate both identity framing and four-channel data framing.
                # A different numeric model ID is allowed for compatible units.
                self._protocol.read_raw_temperatures()
                self._identity = identity
                self._status = DeviceStatus.READY
            except Exception:
                try:
                    self._protocol.transport.close()
                finally:
                    self._identity = None
                    self._status = DeviceStatus.DISCONNECTED
                raise

    def disconnect(self) -> None:
        with self._lock:
            try:
                if self._protocol.transport.is_open:
                    self._protocol.identify()  # Documented stop/identity command.
            finally:
                try:
                    self._protocol.transport.close()
                finally:
                    self._identity = None
                    self._status = DeviceStatus.DISCONNECTED

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        with self._lock:
            if not self._protocol.transport.is_open or self._identity is None:
                raise RuntimeError("Temperature probe is not connected")
            try:
                raw = dict(zip(CHANNELS, self._protocol.read_raw_temperatures(), strict=True))
                timestamp = datetime.now(UTC)
                measurements = tuple(
                    DeviceMeasurement(channel, Measurement(decode_temperature(raw[channel]), "degC", timestamp))
                    for channel in self.channels
                )
            except Exception:
                # No partial or cached batch: the existing polling pipeline marks
                # the last readings stale and logs the failure.
                self._status = DeviceStatus.DEGRADED
                raise
            self._status = DeviceStatus.READY
            return measurements
