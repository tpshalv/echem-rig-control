from datetime import UTC, datetime
from functools import partial

import pytest
from openpyxl import load_workbook

from rig_control.calibrated_channels import CalibratedChannels
from rig_control.control.service import RigControlService
from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.devices.atlas_ezo_hum.configuration import EzoHumConfiguration
from rig_control.devices.atlas_ezo_hum.driver import AtlasEzoHum
from rig_control.devices.atlas_ezo_hum.protocol import EzoHumProtocol
from rig_control.devices.linear_calibration import CalibrationPoint
from rig_control.devices.manager import DeviceManager
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.instrument_settings.calibration import (
    HumidityCalibrationService,
    PumpCalibrationService,
)
from rig_control.models import DeviceStatus, Measurement, Quality
from rig_control.polling import PollingService
from rig_control.power_supply_telemetry import OUTPUT_STATE_UNIT
from rig_control.ui.operation.model import OperationViewModel
from tests.test_atlas_ezo_hum import FakeTransport
from tests.test_operation_view_model import _StubPump


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def make_probe(device_id: str = "ezo1") -> AtlasEzoHum:
    return AtlasEzoHum(EzoHumConfiguration(device_id, "COM8"), EzoHumProtocol(FakeTransport()))


def humidity(value: float, quality: Quality = Quality.GOOD) -> tuple[DeviceMeasurement, ...]:
    return (
        DeviceMeasurement("humidity", Measurement(value, "%RH", NOW, quality)),
        DeviceMeasurement("temperature", Measurement(22.0, "degC", NOW)),
    )


def services(tmp_path):
    pumps = PumpCalibrationService(directory=tmp_path)
    humidity_service = HumidityCalibrationService(directory=tmp_path)
    return pumps, humidity_service, CalibratedChannels(pumps=pumps, humidity=humidity_service)


def calibrate(service, device_id, points, notes=""):
    result = service.accept(service.fit_candidate(
        device_id, "2026-10-27", "operator", [CalibrationPoint(*point) for point in points], notes,
    ))
    assert result.succeeded


def channels(readings):
    return {reading.channel: reading.measurement for reading in readings}


def test_uncalibrated_devices_get_no_extra_channel(tmp_path):
    _pumps, _humidity, calibrated = services(tmp_path)
    raw = humidity(40.0)

    assert calibrated.add_to(make_probe(), raw) == raw


def test_probe_gains_a_co2_corrected_humidity_next_to_the_raw_value(tmp_path):
    _pumps, humidity_service, calibrated = services(tmp_path)
    # reference = 1.1 * reported + 2
    calibrate(humidity_service, "ezo1", [(30.0, 35.0), (70.0, 79.0)])

    result = channels(calibrated.add_to(make_probe(), humidity(40.0)))

    assert result["humidity"].value == 40.0
    corrected = result["humidity_co2_corrected"]
    assert corrected.value == pytest.approx(46.0)
    assert (corrected.unit, corrected.timestamp, corrected.quality) == ("%RH", NOW, Quality.GOOD)


def test_corrected_humidity_over_100_is_kept_but_marked_uncertain(tmp_path):
    _pumps, humidity_service, calibrated = services(tmp_path)
    calibrate(humidity_service, "ezo1", [(30.0, 35.0), (70.0, 79.0)])

    corrected = channels(calibrated.add_to(make_probe(), humidity(95.0)))["humidity_co2_corrected"]

    assert corrected.value == pytest.approx(106.5)
    assert corrected.quality is Quality.UNCERTAIN


def test_pump_gains_a_calibrated_flow_that_is_never_negative(tmp_path):
    pumps, _humidity, calibrated = services(tmp_path)
    # flow = 2 * rpm - 4
    calibrate(pumps, "pump1", [(10.0, 16.0), (50.0, 96.0)])
    pump = _StubPump("pump1")

    def flow_at(rpm):
        readings = (DeviceMeasurement("speed_setpoint", Measurement(rpm, "rpm", NOW)),)
        return channels(calibrated.add_to(pump, readings))["flow_setpoint"]

    assert flow_at(20.0).value == pytest.approx(36.0)
    assert flow_at(20.0).unit == "mL/min"
    assert flow_at(0.0).value == 0.0


def test_pump_reports_its_confirmed_speed_and_run_state(tmp_path):
    pump = _StubPump("pump1")
    pump.set_speed_rpm(12.5)
    pump.start()

    result = channels(pump.read_measurements())

    assert (result["speed_setpoint"].value, result["speed_setpoint"].unit) == (12.5, "rpm")
    assert (result["running"].value, result["running"].unit) == (1.0, OUTPUT_STATE_UNIT)


def test_disconnected_pump_cannot_be_read():
    pump = _StubPump("pump1")
    pump.disconnect()
    assert pump.status is DeviceStatus.DISCONNECTED

    with pytest.raises(RuntimeError, match="not connected"):
        pump.read_measurements()


def test_polling_records_pump_speed_and_calibrated_flow(tmp_path):
    pumps, _humidity, calibrated = services(tmp_path)
    calibrate(pumps, "pump1", [(10.0, 16.0), (50.0, 96.0)])
    manager = DeviceManager()
    pump = _StubPump("pump1")
    pump.set_speed_rpm(20.0)
    manager.register(pump)

    batch = PollingService(manager, derived_readings=calibrated.add_to).poll_once()

    values = {record.channel: record.measurement.value for record in batch.measurements}
    assert values["speed_setpoint"] == 20.0
    assert values["flow_setpoint"] == pytest.approx(36.0)


def test_an_unreadable_calibration_never_costs_the_raw_readings(tmp_path):
    (tmp_path / "pump1.json").write_text("not json", encoding="utf-8")
    _pumps, _humidity, calibrated = services(tmp_path)
    manager = DeviceManager()
    pump = _StubPump("pump1")
    pump.set_speed_rpm(20.0)
    manager.register(pump)

    batch = PollingService(manager, derived_readings=calibrated.add_to).poll_once()

    assert [record.channel for record in batch.measurements] == ["speed_setpoint"]
    assert batch.failures == ()


def test_excel_overview_lists_each_active_calibration_with_its_notes(tmp_path):
    pumps, humidity_service, calibrated = services(tmp_path / "calibrations")
    calibrate(pumps, "pump1", [(10.0, 16.0), (50.0, 96.0)], "new tubing")
    calibrate(humidity_service, "ezo1", [(30.0, 35.0), (70.0, 79.0)])
    manager = DeviceManager()
    manager.register(_StubPump("pump1"))
    manager.register(make_probe())
    recorder = ExperimentRecorder(writer_factory=partial(
        DirectoryExperimentWriter, live_export_interval_seconds=0))
    # Only the pump is polled; the unconnected probe just has to be registered.
    polling = PollingService(manager, interval_seconds=60.0, batch_handler=recorder.record_batch,
                             derived_readings=calibrated.add_to)
    model = OperationViewModel(
        manager, polling, recorder, RigControlService(manager), profile_id="simulation",
        pump_calibration_service=pumps, humidity_calibration_service=humidity_service,
    )
    model.start_monitoring()
    try:
        assert model.start_recording(
            operator="TPS", output_directory=tmp_path / "runs", experiment_id="EXP-1",
        ).succeeded
        model.stop_recording()
    finally:
        model.shutdown()

    workbook = load_workbook(tmp_path / "runs" / "EXP-1" / "EXP-1.xlsx", read_only=True)
    try:
        overview = {row[0]: row[1] for row in workbook["Overview"].values if len(row) > 1}
    finally:
        workbook.close()
    assert overview["Calibration: pump1"] == "2026-10-27 — new tubing"
    assert overview["Calibration: ezo1"] == "2026-10-27"
