import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import tkinter as tk

from openpyxl import load_workbook

from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.export import build_wide_rows
from rig_control.data.serialization import measurement_record_to_dict
from rig_control.devices.manager import DeviceManager
from rig_control.polling import PollingService
from rig_control.ui.operation.dashboard_window import MultiTraceCanvasRenderer
from rig_control.ui.operation.window import OperationWindow
from test_keithley_2280s_driver import make_supply
from test_operation_view_model import make_model


def samples():
    supply, transport = make_supply(voltage="1", current="0.05", output="ON")
    supply.connect()
    manager = DeviceManager()
    manager.register(supply)
    poller = PollingService(manager)
    start = datetime(2026, 9, 29, tzinfo=UTC)
    batches = []
    for index, (output, fetch) in enumerate([
        ("ON", "0.05,0.505,CC"), ("ON", "0.04,0.6,CV"), ("OFF", None),
    ]):
        if index == 1:
            supply.set_voltage(0.6)
            supply.set_current_limit(0.08)
        transport.queue_response("OUTP:STAT?", output)
        if fetch:
            transport.queue_response("FETC?", fetch)
        batch = poller.poll_once()
        assert not batch.failures
        # First two samples share a bin; output-off has its own bin.
        timestamp = start + timedelta(seconds=(0.1, 0.2, 1.1)[index])
        batches.append(replace(batch, measurements=tuple(
            replace(r, measurement=replace(r.measurement, timestamp=timestamp))
            for r in batch.measurements)))
    return supply, batches


def test_supply_history_reaches_raw_csv_and_excel_with_readable_states(tmp_path):
    _, batches = samples()
    writer = DirectoryExperimentWriter(tmp_path)
    writer.open_experiment(ExperimentMetadata(experiment_id="supply", operator="test"))
    for batch in batches:
        for record in batch.measurements:
            writer.write_measurement(record)
    directory = writer.experiment_directory
    writer.close_experiment()
    raw = [json.loads(line) for line in
           (directory / "measurements.journal.jsonl").read_text().splitlines()]
    expected_raw = [measurement_record_to_dict(r) for b in batches for r in b.measurements]
    assert raw == expected_raw
    headers, expected = build_wide_rows(raw)
    with (directory / "measurements-wide.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.reader(stream)) == [headers, *[[str(v) for v in row] for row in expected]]
    first = dict(zip(headers[1:], expected[0][1:]))
    assert first["supply.voltage [V]"] == (0.505 + 0.6) / 2
    assert first["supply.current [A]"] == (0.05 + 0.04) / 2
    assert first["supply.voltage_setpoint [V]"] == 0.6
    assert first["supply.current_limit [A]"] == 0.08
    assert first["supply.output_enabled [0=OFF,1=ON]"] == "ON"
    assert first["supply.regulation_mode [0=OFF,1=CC,2=CV]"] == "CV"
    off = dict(zip(headers[1:], expected[1][1:]))
    assert off["supply.current [A]"] == off["supply.voltage [V]"] == ""
    assert off["supply.output_enabled [0=OFF,1=ON]"] == "OFF"
    assert off["supply.regulation_mode [0=OFF,1=CC,2=CV]"] == "OFF"
    workbook = load_workbook(directory / "experiment.xlsx", read_only=True)
    try:
        data = list(workbook["Data"].values)
        assert list(data[0]) == headers
        assert list(data[1][1:]) == expected[0][1:]
        assert list(data[2][1:]) == [None if v == "" else v for v in expected[1][1:]]
    finally:
        workbook.close()


def test_live_selector_exposes_setpoints_and_renders_measured_solid_set_dashed():
    supply, batches = samples()
    model, manager, polling, _ = make_model()
    manager.register(supply)
    for batch in batches[:2]:
        polling.results.put(batch)
    model.collect_polling_results()
    window = OperationWindow.__new__(OperationWindow)
    window._view_model = model
    signals = {s.channel: s for s in window._dashboard_signals()}
    assert {"voltage", "current", "voltage_setpoint", "current_limit"} <= signals.keys()
    assert "regulation_mode" not in signals
    assert signals["current_limit"].is_setpoint
    assert signals["voltage_setpoint"].is_setpoint
    assert not signals["current"].is_setpoint
    rows = {r.channel: r for r in model.channel_rows() if r.device_id == "supply"}
    assert rows["regulation_mode"].value == "CV"
    root = tk.Tk()
    root.withdraw()
    try:
        canvas = tk.Canvas(root)
        renderer = MultiTraceCanvasRenderer(canvas)
        renderer.draw(width=800, height=400, view_start=None, view_end=None, traces=tuple(
            (signals[channel], model.measurement_history("supply", channel), colour)
            for channel, colour in (("current", "blue"), ("current_limit", "red"))
        ))
        assert canvas.itemcget(renderer._lines[("supply", "current")], "dash") == ""
        assert canvas.itemcget(renderer._lines[("supply", "current_limit")], "dash") == "6 4"
    finally:
        root.destroy()
    polling.results.put(batches[2])
    model.collect_polling_results()
    rows = {r.channel: r for r in model.channel_rows() if r.device_id == "supply"}
    assert rows["regulation_mode"].value == "OFF"
    assert rows["current"].quality == "stale"
    assert len(model.measurement_history("supply", "current")) == 2
    assert len(model.measurement_history("supply", "current_limit")) == 3
