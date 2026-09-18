from dataclasses import replace
from datetime import UTC, datetime
import csv
import json
from queue import Queue
from types import SimpleNamespace

import pytest

from rig_control.control.service import RigControlService
from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.export import export_wide_csv
from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.devices.temperature_probe import TemperatureProbe
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import DeviceStatus, Measurement
from rig_control.polling import PollingBatch
from rig_control.rig_profile import DeviceCapability, DeviceRole, RigProfile
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.rig_profile_writing import write_rig_profile
from rig_control.ui.device_setup.model import DeviceSetupViewModel
from rig_control.ui.device_setup.window import parse_channel_labels
from rig_control.ui.operation.model import OperationViewModel
from rig_control.ui.operation.window import OperationWindow


class GenericProbe(TemperatureProbe):
    device_id = "probe"
    channels = ("tc1", "tc2")
    status = DeviceStatus.READY

    def connect(self):
        pass

    def disconnect(self):
        pass

    def read_measurements(self):
        return tuple(DeviceMeasurement(ch, Measurement(value, "degC"))
                     for ch, value in (("tc1", 25), ("tc2", 40)))


def make_profile(labels=None):
    return RigProfile("rig", "Test", (
        DeviceRole("probe", "Thermocouples", DeviceCapability.TEMPERATURE_SENSOR,
                   "generic_test_probe", channel_labels=labels or {"tc1": "Water bath", "tc2": "Cell"}),
    ))


def test_labels_round_trip_with_quoted_keys_and_unicode(tmp_path):
    labels = {"tc1": 'Water bath "A"', "probe.temperature": "Cell – inlet"}
    original = make_profile(labels)
    path = tmp_path / "profile.toml"
    write_rig_profile(original, path)
    assert load_rig_profile(path) == original
    labels["tc1"] = "Changed"
    assert original.get_role("probe").channel_labels["tc1"] == 'Water bath "A"'
    with pytest.raises(TypeError):
        original.get_role("probe").channel_labels["tc1"] = "Changed"


@pytest.mark.parametrize("labels", [{"tc1": ""}, {"": "Bath"}, {"tc1": 123}])
def test_invalid_label_mapping_rejected(labels):
    with pytest.raises((ValueError, TypeError)):
        make_profile(labels)


def test_edit_device_preserves_and_changes_labels_without_channel_renames():
    saved = []
    model = DeviceSetupViewModel(make_profile(), profile_writer=lambda profile, _: saved.append(profile))
    request = model.device_edit_values("probe")
    assert request.channel_labels == {"tc1": "Water bath", "tc2": "Cell"}
    labels = parse_channel_labels("tc1 = Water bath\ntc2 = Cell inlet\n")
    result = model.update_device(replace(request, channel_labels=labels))
    assert result.succeeded
    role = saved[0].get_role("probe")
    assert role.channel_labels == labels
    assert role.device_id == "probe"
    assert role.driver == "generic_test_probe"


@pytest.mark.parametrize("text", ["tc1: bath", "tc1 =", " = bath", "tc1 = bath\ntc1 = cell"])
def test_label_editor_rejects_ambiguous_input(text):
    with pytest.raises(ValueError):
        parse_channel_labels(text)


def test_ui_graphs_and_exports_use_snapshot_labels_but_keep_raw_ids(tmp_path):
    from openpyxl import load_workbook

    manager = DeviceManager()
    manager.register(GenericProbe())
    writer = DirectoryExperimentWriter(tmp_path)
    recorder = ExperimentRecorder(writer_factory=lambda _: writer)
    polling = SimpleNamespace(is_running=True, results=Queue())
    model = OperationViewModel(manager, polling, recorder, RigControlService(manager),
                               profile_id="rig", profile=make_profile())
    # Labels appear before the first poll, too.
    rows = {row.channel: row for row in model.channel_rows()}
    assert rows["tc1"].channel_name == "Water bath (tc1)"
    assert rows["tc2"].channel_name == "Cell (tc2)"

    result = model.start_recording(experiment_id="label-test", operator="Tester", output_directory=tmp_path)
    assert result.succeeded
    directory = writer.experiment_directory
    now = datetime.now(UTC)
    batch = PollingBatch(now, now, tuple(
        MeasurementRecord("probe", reading.channel, reading.measurement)
        for reading in manager.get("probe").read_measurements()
    ), (), ())
    recorder.record_batch(batch)
    polling.results.put(batch)
    model.collect_polling_results()
    signals = OperationWindow._dashboard_signals(SimpleNamespace(_view_model=model))
    assert any(signal.channel == "tc1" and "Water bath (tc1)" in signal.label for signal in signals)

    # Relabelling future runs must not change the completed recording's labels.
    model._profile = make_profile({"tc1": "New bath", "tc2": "New cell"})
    assert model.stop_recording().succeeded
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    assert json.loads(metadata["extra"]["channel_labels_json"])["probe"]["tc1"] == "Water bath"
    raw = [json.loads(line) for line in (directory / "measurements.journal.jsonl").read_text().splitlines()]
    assert [record["channel"] for record in raw] == ["tc1", "tc2"]
    export_wide_csv(directory)
    with (directory / "measurements-wide.csv").open(encoding="utf-8-sig", newline="") as stream:
        headers = next(csv.reader(stream))
    assert headers == ["timestamp_utc", "probe.tc1 (Water bath) [degC]", "probe.tc2 (Cell) [degC]"]
    workbook = load_workbook(directory / "experiment.xlsx", read_only=True)
    try:
        assert [cell.value for cell in next(workbook["Data"].rows)] == headers
    finally:
        workbook.close()
