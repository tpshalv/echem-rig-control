from math import isfinite

from rig_control.devices.base import Device
from rig_control.devices.esp32_bus import Esp32Bus
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.models import DeviceStatus, Measurement, Quality


class Esp32Dht11(Device, MeasurementSource):
    """Temperature and humidity channels read through a shared ESP32 bus."""

    _EXPECTED_UNITS = {
        "temperature": "degC",
        "humidity": "%RH",
    }

    def __init__(self, device_id: str, bus: Esp32Bus) -> None:
        self._device_id = device_id
        self._bus = bus
        self._status = DeviceStatus.DISCONNECTED
        self._bus_acquired = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    def connect(self) -> None:
        self._status = DeviceStatus.CONNECTING
        try:
            self._bus.acquire()
            self._bus_acquired = True
        except Exception:
            self._status = DeviceStatus.DISCONNECTED
            raise
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        if not self._bus_acquired:
            self._status = DeviceStatus.DISCONNECTED
            return
        try:
            self._bus.release()
        finally:
            self._bus_acquired = False
            self._status = DeviceStatus.DISCONNECTED

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        try:
            channels = self._bus.read_sensors()
            by_name = self._validate_channels(channels)
            measurements = tuple(
                DeviceMeasurement(
                    name,
                    Measurement(
                        value=by_name[name]["value"],
                        unit=by_name[name]["unit"],
                        quality=by_name[name]["quality"],
                    ),
                )
                for name in ("temperature", "humidity")
            )
        except Exception:
            self._status = DeviceStatus.DEGRADED
            raise
        self._status = DeviceStatus.READY
        return measurements

    @classmethod
    def _validate_channels(
        cls,
        channels: list[dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        selected: dict[str, dict[str, object]] = {}
        for channel in channels:
            name = channel.get("name")
            if name not in cls._EXPECTED_UNITS:
                continue
            if name in selected:
                raise ValueError(f"Duplicate ESP32 sensor channel: {name!r}")
            value = channel.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"ESP32 sensor channel {name!r} has no numeric value")
            numeric_value = float(value)
            if not isfinite(numeric_value):
                raise ValueError(f"ESP32 sensor channel {name!r} value must be finite")
            unit = channel.get("unit")
            if unit != cls._EXPECTED_UNITS[name]:
                raise ValueError(
                    f"ESP32 sensor channel {name!r} expected unit "
                    f"{cls._EXPECTED_UNITS[name]!r}, received {unit!r}"
                )
            try:
                quality = Quality(channel.get("quality"))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"ESP32 sensor channel {name!r} has invalid quality"
                ) from error
            if quality is Quality.GOOD and not cls._within_rated_range(
                name,
                numeric_value,
            ):
                quality = Quality.UNCERTAIN
            selected[name] = {
                "value": numeric_value,
                "unit": unit,
                "quality": quality,
            }
        missing = set(cls._EXPECTED_UNITS) - set(selected)
        if missing:
            raise ValueError(
                "ESP32 sensor response is missing channels: "
                + ", ".join(sorted(missing))
            )
        return selected

    @staticmethod
    def _within_rated_range(name: str, value: float) -> bool:
        if name == "temperature":
            return 0.0 <= value <= 50.0
        if name == "humidity":
            return 20.0 <= value <= 90.0
        return False
