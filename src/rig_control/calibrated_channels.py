"""Calibrated outputs recorded next to the raw readings they come from.

Each poll, a pump's speed setpoint gains a calibrated flow, and an EZO-HUM
probe's humidity gains a CO2-corrected estimate, both from that device's
active calibration. The raw readings are always kept as they are, so a run
can be recalculated later with a better calibration; a device with no
accepted calibration simply gets no extra channel.
"""

from rig_control.devices.atlas_ezo_hum.driver import AtlasEzoHum
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.pump import Pump
from rig_control.instrument_settings.calibration import (
    CalibrationService,
    HumidityCalibrationService,
    PumpCalibrationService,
)
from rig_control.models import Measurement, Quality


PUMP_FLOW_CHANNEL = "flow_setpoint"
CO2_HUMIDITY_CHANNEL = "humidity_co2_corrected"


class CalibratedChannels:
    def __init__(
        self,
        *,
        pumps: CalibrationService | None = None,
        humidity: CalibrationService | None = None,
    ) -> None:
        self._pumps = pumps or PumpCalibrationService()
        self._humidity = humidity or HumidityCalibrationService()

    def add_to(
        self, device: object, readings: tuple[DeviceMeasurement, ...],
    ) -> tuple[DeviceMeasurement, ...]:
        if isinstance(device, Pump):
            source, service, channel, unit = "speed_setpoint", self._pumps, PUMP_FLOW_CHANNEL, "mL/min"
        elif isinstance(device, AtlasEzoHum):
            source, service, channel, unit = "humidity", self._humidity, CO2_HUMIDITY_CHANNEL, "%RH"
        else:
            return readings
        raw = next((reading.measurement for reading in readings if reading.channel == source), None)
        if raw is None:
            return readings
        calibration = service.active_calibration(device.device_id)
        if calibration is None:
            return readings
        value = calibration.fit.y_for_x(raw.value)
        quality = raw.quality
        if isinstance(device, Pump):
            # A positive intercept would otherwise record flow at 0 rpm.
            value = max(value, 0.0)
        elif quality is Quality.GOOD and not 0.0 <= value <= 100.0:
            quality = Quality.UNCERTAIN
        return (*readings, DeviceMeasurement(
            channel, Measurement(value, unit, raw.timestamp, quality),
        ))
