"""Calibrated outputs recorded next to the raw readings they come from.

Each poll, a pump's speed setpoint gains a calibrated flow, and an EZO-HUM
probe's humidity gains a CO2-corrected estimate, both from that device's
active calibration. The raw readings are always kept as they are, so a run
can be recalculated later with a better calibration; a device with no
accepted calibration simply gets no extra channel. A supply's current gains
a current density the same way, once the cell's active area is entered.
"""

from rig_control.cell_area import CellArea, RECORDED_DENSITY_UNIT
from rig_control.devices.atlas_ezo_hum.driver import AtlasEzoHum
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.power_supply import PowerSupply
from rig_control.devices.pump import Pump
from rig_control.instrument_settings.calibration import (
    CalibrationService,
    HumidityCalibrationService,
    PumpCalibrationService,
)
from rig_control.models import Measurement, Quality
import json


PUMP_FLOW_CHANNEL = "flow_setpoint"
CO2_HUMIDITY_CHANNEL = "humidity_co2_corrected"
CURRENT_DENSITY_CHANNEL = "current_density"


class _RunCalibrationView:
    """Use the same frozen fit for commands and their derived readbacks."""
    def __init__(self, owner, service):
        self._owner, self._service = owner, service

    def active_calibration(self, device_id):
        if self._owner._is_recording():
            return self._owner._frozen.get(device_id)
        return self._service.active_calibration(device_id)

    def __getattr__(self, name):
        return getattr(self._service, name)


class CalibratedChannels:
    def __init__(
        self,
        *,
        pumps: CalibrationService | None = None,
        humidity: CalibrationService | None = None,
        is_recording=lambda: False,
        cell_area: CellArea | None = None,
    ) -> None:
        self._cell_area = cell_area or CellArea()
        self._pumps = pumps or PumpCalibrationService()
        self._humidity = humidity or HumidityCalibrationService()
        self._is_recording = is_recording
        self._frozen = {}
        self.pumps = _RunCalibrationView(self, self._pumps)
        self.humidity = _RunCalibrationView(self, self._humidity)

    def snapshot(self, manager):
        frozen, metadata = {}, []
        for device_id in manager.device_ids:
            device = manager.get(device_id)
            service = self._pumps if isinstance(device, Pump) else self._humidity if isinstance(device, AtlasEzoHum) else None
            if service is None:
                continue
            calibration = service.active_calibration(device_id)
            frozen[device_id] = calibration
            if calibration is not None:
                metadata.append(service._to_dict(calibration))
        self._frozen = frozen
        return {"calibrations_json": json.dumps(metadata)}

    def add_to(
        self, device: object, readings: tuple[DeviceMeasurement, ...],
    ) -> tuple[DeviceMeasurement, ...]:
        if isinstance(device, PowerSupply):
            return self._with_current_density(readings)
        if isinstance(device, Pump):
            source, service, channel, unit = "speed_setpoint", self._pumps, PUMP_FLOW_CHANNEL, "mL/min"
        elif isinstance(device, AtlasEzoHum):
            source, service, channel, unit = "humidity", self._humidity, CO2_HUMIDITY_CHANNEL, "%RH"
        else:
            return readings
        raw = next((reading.measurement for reading in readings if reading.channel == source), None)
        if raw is None:
            return readings
        calibration = (self._frozen.get(device.device_id) if self._is_recording()
                       else service.active_calibration(device.device_id))
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

    def _with_current_density(
        self, readings: tuple[DeviceMeasurement, ...],
    ) -> tuple[DeviceMeasurement, ...]:
        raw = next((reading.measurement for reading in readings if reading.channel == "current"), None)
        density = None if raw is None or raw.unit != "A" else self._cell_area.density_for(raw.value)
        if density is None:
            return readings
        return (*readings, DeviceMeasurement(
            CURRENT_DENSITY_CHANNEL, Measurement(density, RECORDED_DENSITY_UNIT, raw.timestamp, raw.quality),
        ))
