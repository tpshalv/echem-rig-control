from datetime import UTC, datetime
from threading import RLock

from rig_control.devices.atlas_ezo_hum.configuration import EzoHumConfiguration
from rig_control.devices.atlas_ezo_hum.protocol import EzoHumIdentity, EzoHumProtocol
from rig_control.devices.base import Device
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.models import DeviceStatus, Measurement, Quality


class AtlasEzoHum(Device, MeasurementSource):
    """One direct USB-to-UART EZO-HUM probe."""

    def __init__(self, configuration: EzoHumConfiguration, protocol: EzoHumProtocol) -> None:
        self.configuration = configuration
        self._protocol = protocol
        self._status = DeviceStatus.DISCONNECTED
        self._identity: EzoHumIdentity | None = None
        self._lock = RLock()

    @property
    def device_id(self) -> str:
        return self.configuration.device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def identity(self) -> EzoHumIdentity | None:
        return self._identity

    def connect(self) -> None:
        with self._lock:
            if self._protocol.transport.is_open:
                raise RuntimeError("EZO-HUM is already connected")
            self._status = DeviceStatus.CONNECTING
            try:
                self._protocol.transport.open()
                self._protocol.configure_output(
                    include_dew_point=self.configuration.include_dew_point
                )
                self._identity = self._protocol.identify()
                self._protocol.read(include_dew_point=self.configuration.include_dew_point)
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
                self._protocol.transport.close()
            finally:
                self._identity = None
                self._status = DeviceStatus.DISCONNECTED

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        with self._lock:
            if not self._protocol.transport.is_open or self._identity is None:
                raise RuntimeError("EZO-HUM is not connected")
            try:
                reading = self._protocol.read(
                    include_dew_point=self.configuration.include_dew_point
                )
                timestamp = datetime.now(UTC)
                measurements = [
                    DeviceMeasurement(
                        "humidity",
                        Measurement(reading.humidity, "%RH", timestamp, _humidity_quality(reading.humidity)),
                    ),
                    DeviceMeasurement(
                        "temperature",
                        Measurement(reading.temperature, "degC", timestamp, _temperature_quality(reading.temperature)),
                    ),
                ]
                if reading.dew_point is not None:
                    measurements.append(
                        DeviceMeasurement(
                            "dew_point",
                            Measurement(reading.dew_point, "degC", timestamp, _temperature_quality(reading.dew_point)),
                        )
                    )
            except Exception:
                self._status = DeviceStatus.DEGRADED
                raise
            self._status = DeviceStatus.READY
            return tuple(measurements)


def _humidity_quality(value: float) -> Quality:
    # Wet sensors can report over 100% RH. Preserve the observation, but make
    # its abnormal physical condition visible to downstream users.
    return Quality.GOOD if 0.0 <= value <= 100.0 else Quality.UNCERTAIN


def _temperature_quality(value: float) -> Quality:
    return Quality.GOOD if -20.0 <= value <= 80.0 else Quality.UNCERTAIN
