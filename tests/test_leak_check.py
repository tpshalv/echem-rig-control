import csv
from datetime import UTC, datetime, timedelta
import json
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from rig_control.control.commands import CommandSource, SetMfcFlow
from rig_control.control.service import ControlAccessError, ControlMode, RigControlService
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_mfc import SimulatedMassFlowController
from rig_control.devices.simulated_power_supply import SimulatedPowerSupply
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.flow.calibration import (
    CALIBRATION_FILE, TRACE_FILE, LeakCheckStore, fit_line, load_preferences, validate,
)
from rig_control.flow.indicator import (
    SCCM_PER_AMP, SOURCE, STATE_EXCESS, STATE_EXCESS_AND_FE, STATE_OFF, ExcessFlowIndicator, excess_flow, rough_fe,
)
from rig_control.flow.leak_check import AVERAGING, SETTLING, LeakCheckPlan, LeakCheckRunner, TraceRow, parse_flows
from rig_control.models import DeviceStatus, Measurement, Quality
from rig_control.polling import PollingBatch, PollingFailure
from rig_control.rig_profile import DeviceBackend, DeviceCapability, DeviceRole, RigProfile
from rig_control.ui.operation.leak_check_window import (
    MFC_COLOUR, averaging_spans, calibration_line_segment, delta_flow, draw_calibration,
    nice_ticks, outlet_percent, trace_segments,
)


def bench(tmp_path, *, supply=False):
    manager = DeviceManager()
    mfc = SimulatedMassFlowController("inlet", MassFlowControllerLimits(0.2, "SLPM"))
    meter = SimulatedSensor("outlet", 0.09, "SLPM")
    roles = [DeviceRole("inlet", "Inlet", DeviceCapability.MASS_FLOW_CONTROLLER, "t", backend=DeviceBackend.SIMULATED),
             DeviceRole("outlet", "Outlet", DeviceCapability.MASS_FLOW_METER, "t", backend=DeviceBackend.SIMULATED)]
    devices = [mfc, meter]
    if supply:
        power = make_supply()
        devices.append(power)
        roles.append(DeviceRole("supply", "Supply", DeviceCapability.DC_POWER_SUPPLY, "t", backend=DeviceBackend.SIMULATED))
    for device in devices:
        manager.register(device)
        device.connect()
    profile = RigProfile("test-flow", "Test", tuple(roles))
    control = RigControlService(manager)
    store = LeakCheckStore(tmp_path/"calibrations")
    runner = LeakCheckRunner(manager, control, store, profile, monitoring=lambda: True)
    return SimpleNamespace(manager=manager, mfc=mfc, meter=meter, control=control, store=store, runner=runner, profile=profile)


def make_supply():
    return SimulatedPowerSupply("supply", PowerSupplyLimits(10, 10, 100))


def calibration(slope=.9, intercept=-1., inlet_ids=("inlet",), outlet_id="outlet", created=None):
    return validate(dict(type="leak_check", version=1, id="check", created_utc=(created or datetime.now(UTC)).isoformat(),
        rig_profile_id="test-flow", notes="", inlet_ids=list(inlet_ids), outlet_id=outlet_id, instruments={},
        range_sccm=[25., 100.], points=[], slope=slope, intercept_sccm=intercept, rmse_sccm=.1))


def saved(rig, **kwargs):
    item = calibration(**kwargs)
    rig.store.save(rig.store.new_folder(datetime.now(UTC)), item)
    return item


def records(stamp, *readings):
    return tuple(MeasurementRecord(d, c, Measurement(v, u, stamp)) for d, c, v, u in readings)


def batch(stamp, *readings, failures=()):
    return PollingBatch(stamp, stamp, records(stamp, *readings), failures, ())


def flows(inlet, outlet):
    return ("inlet", "mass_flow", inlet/1000, "SLPM"), ("outlet", "mass_flow", outlet, "SCCM")


def derived(result):
    return {r.channel: r.measurement for r in result.measurements if r.device_id == SOURCE}


def indicate(rig, **kwargs):
    return ExcessFlowIndicator(rig.manager, "test-flow", store=rig.store, **kwargs)


# Calibration -----------------------------------------------------------------


def test_line_fit_is_outlet_against_inlet():
    points = [dict(inlet_sccm=q, outlet_sccm=.9*q-1) for q in (25., 50., 100.)]
    fit = fit_line(points)
    assert fit["slope"] == pytest.approx(.9)
    assert fit["intercept_sccm"] == pytest.approx(-1)
    assert fit["rmse_sccm"] == pytest.approx(0, abs=1e-9)


def test_line_needs_two_different_flows():
    assert fit_line([dict(inlet_sccm=25., outlet_sccm=24.)]*3) is None
    two = fit_line([dict(inlet_sccm=25., outlet_sccm=24.), dict(inlet_sccm=50., outlet_sccm=48.)])
    assert two["slope"] == pytest.approx(.96)
    assert two["rmse_sccm"] == 0


def test_fit_is_unweighted_with_free_intercept_and_includes_flagged_points():
    points = [dict(inlet_sccm=10., outlet_sccm=13., count=100),
              dict(inlet_sccm=20., outlet_sccm=26., count=3, flags=["noisy"]),
              dict(inlet_sccm=30., outlet_sccm=31., count=100)]
    fit = fit_line(points)
    assert fit["slope"] == pytest.approx(.9)
    assert fit["intercept_sccm"] == pytest.approx(16/3)
    residuals = [p["outlet_sccm"]-fit["slope"]*p["inlet_sccm"]-fit["intercept_sccm"] for p in points]
    assert sum(residuals) == pytest.approx(0, abs=1e-12)
    assert any(abs(r) > 1 for r in residuals)


@pytest.mark.parametrize("slope,intercept", [(.9, -1), (1.2, 7), (-.5, 100), (0, 45)])
@pytest.mark.parametrize("flows", [(25., 100.), (25., 50., 100.)])
def test_drawn_fit_passes_through_exact_points_at_different_canvas_sizes(slope, intercept, flows):
    class Canvas:
        def __init__(self, width, height):
            self.width, self.height = width, height
            self.lines, self.ovals = [], []

        def winfo_width(self): return self.width
        def winfo_height(self): return self.height
        def delete(self, _tag): self.lines.clear(); self.ovals.clear()
        def create_text(self, *args, **kwargs): pass
        def create_line(self, *args, **kwargs): self.lines.append((args, kwargs))
        def create_oval(self, *args, **kwargs): self.ovals.append(args)

    points = [dict(inlet_sccm=q, outlet_sccm=slope*q+intercept) for q in flows]
    fit = fit_line(points)
    # Reuse the canvas to exercise redraws as the panel changes size.
    canvas = Canvas(560, 320)
    for width, height in ((560, 320), (310, 440)):
        canvas.width, canvas.height = width, height
        draw_calibration(canvas, points, fit)
        line, = [coords for coords, style in canvas.lines if style.get("fill") == MFC_COLOUR]
        x0, y0, x1, y1 = line
        for left, top, right, bottom in canvas.ovals:
            x, y = (left+right)/2, (top+bottom)/2
            assert (x-x0)*(y1-y0)-(y-y0)*(x1-x0) == pytest.approx(0, abs=1e-7)


def test_fit_clipping_uses_boundary_intersections():
    fit = dict(slope=.5, intercept_sccm=0)
    assert calibration_line_segment(fit, 20, 100) == ((40, 20), (100, 50))
    assert calibration_line_segment(dict(slope=0, intercept_sccm=10), 20, 100) == ()
    assert calibration_line_segment(dict(slope=1, intercept_sccm=200), 20, 100) == ()


def test_store_finds_newest_for_this_rig_and_reports_damaged_files(tmp_path):
    rig = bench(tmp_path)
    saved(rig, slope=.8, created=datetime.now(UTC)-timedelta(days=1))
    newest = saved(rig, slope=.9)
    other = dict(calibration(slope=.5), rig_profile_id="other-rig")
    rig.store.save(rig.store.new_folder(datetime.now(UTC)), other)
    damaged = rig.store.new_folder(datetime.now(UTC))
    (damaged/CALIBRATION_FILE).write_text('{"type":"leak_check"')
    rig.store._refresh_after = 0.
    assert rig.store.latest("test-flow")["slope"] == newest["slope"]
    assert damaged.name in rig.store.errors[0]


def test_folder_without_calibration_is_not_in_use(tmp_path):
    rig = bench(tmp_path)
    rig.store.new_folder(datetime.now(UTC))
    assert rig.store.latest("test-flow") is None
    assert rig.store.errors == []


def test_invalid_calibration_rejected():
    with pytest.raises(ValueError):
        validate(dict(calibration(), slope=float("nan")))
    with pytest.raises(ValueError):
        validate(dict(calibration(), type="flow_deficit"))


def test_damaged_preferences_mean_none(tmp_path):
    path = tmp_path/"p.json"
    assert load_preferences(path) == {}
    path.write_text("[1, 2")
    assert load_preferences(path) == {}


# Plan ------------------------------------------------------------------------


def test_flow_list_parsing():
    assert parse_flows("25, 50;75  100") == (25, 50, 75, 100)
    with pytest.raises(ValueError):
        parse_flows("25, fifty")


def test_plan_repeats_are_allowed_and_constant_line_holds():
    plan = LeakCheckPlan.from_flow_lists({"wet": (25., 50., 25.), "dry": (10.,)}, "outlet", settle_s=10, average_s=20)
    assert plan.conditions == ({"wet": 25., "dry": 10.}, {"wet": 50., "dry": 10.}, {"wet": 25., "dry": 10.})
    assert plan.inlet_ids == ("wet", "dry")
    assert plan.duration_s == 90


@pytest.mark.parametrize("lists,outlet", [
    ({}, "outlet"),
    ({"a": (25., 50.), "b": (1., 2., 3.)}, "outlet"),
    ({"a": (25., 25.)}, "outlet"),
    ({"a": (25., -1.)}, "outlet"),
    ({"a": (0., 0.)}, "outlet"),
    ({"a": (25., 50.)}, "a"),
    ({"a": (25., 50.)}, ""),
])
def test_plan_rejects(lists, outlet):
    with pytest.raises(ValueError):
        LeakCheckPlan.from_flow_lists(lists, outlet)


# Live indicator --------------------------------------------------------------


def test_formulas():
    assert SCCM_PER_AMP == pytest.approx(7.607, rel=.001)
    assert excess_flow(calibration(), 100, 95) == pytest.approx(6)
    h2, co = rough_fe(SCCM_PER_AMP/2, 1)
    assert h2 == pytest.approx(50)
    assert co == pytest.approx(50)


def test_fe_is_not_clipped():
    assert rough_fe(-SCCM_PER_AMP, 1)[0] == pytest.approx(-100)
    assert rough_fe(2*SCCM_PER_AMP, 1)[0] == pytest.approx(200)


def test_no_calibration_adds_no_channels(tmp_path):
    rig = bench(tmp_path)
    result = indicate(rig).process(batch(datetime.now(UTC), *flows(100, 95)))
    assert derived(result) == {}


def test_excess_flow_subtracts_leak_with_mixed_units(tmp_path):
    rig = bench(tmp_path)
    saved(rig)
    values = derived(indicate(rig).process(batch(datetime.now(UTC), *flows(100, 95))))
    assert values["excess_flow"].value == pytest.approx(6)
    assert values["excess_flow"].unit == "SCCM"
    assert values["indicator_state"].value == STATE_EXCESS
    assert "h2_fe" not in values


def test_rough_fe_with_current(tmp_path):
    rig = bench(tmp_path, supply=True)
    # Excess of half the 2 A product flow.
    saved(rig, slope=1., intercept=0.)
    now = datetime.now(UTC)
    values = derived(indicate(rig).process(batch(now, *flows(100, 100+SCCM_PER_AMP), ("supply", "current", -2., "A"))))
    assert values["h2_fe"].value == pytest.approx(50)
    assert values["co_fe"].value == pytest.approx(50)
    assert values["indicator_state"].value == STATE_EXCESS_AND_FE


@pytest.mark.parametrize("extra", [
    (("supply", "current", 0., "A"),),
    (("supply", "current", 2000., "mA"),),
    (("supply", "current", 2., "A"), ("supply", "output_enabled", 0., "")),
    (),
])
def test_excess_stays_when_fe_has_no_usable_current(tmp_path, extra):
    rig = bench(tmp_path, supply=True)
    saved(rig)
    values = derived(indicate(rig).process(batch(datetime.now(UTC), *flows(100, 95), *extra)))
    assert "excess_flow" in values
    assert "h2_fe" not in values


def test_each_outlet_reading_is_calculated_without_smoothing(tmp_path):
    rig = bench(tmp_path)
    saved(rig, slope=1., intercept=0.)
    indicator = indicate(rig)
    t = datetime.now(UTC)
    for seconds, outlet, expected in ((0, 90, -10), (1, 110, 10), (2, 100, 0)):
        stamp = t+timedelta(seconds=seconds)
        values = derived(indicator.process(batch(stamp, *flows(100, outlet))))
        assert values["excess_flow"].value == pytest.approx(expected)
        assert values["excess_flow"].timestamp == stamp


def test_separate_device_batches_pair_outlet_with_latest_inlet(tmp_path):
    rig = bench(tmp_path)
    saved(rig, slope=1., intercept=0.)
    indicator = indicate(rig)
    t = datetime.now(UTC)
    inlet, outlet = flows(100, 95)
    # An inlet reading on its own has nothing new to calculate.
    assert derived(indicator.process(batch(t, inlet))) == {}
    values = derived(indicator.process(batch(t+timedelta(seconds=1), outlet)))
    assert values["excess_flow"].value == pytest.approx(-5)
    # A repeated outlet reading is not a new point.
    repeat = records(t+timedelta(seconds=1), outlet)
    assert derived(indicator.process(PollingBatch(t, t+timedelta(seconds=2), repeat, (), ()))) == {}
    # An inlet reading too old to pair with switches the channel off.
    values = derived(indicator.process(batch(t+timedelta(seconds=30), outlet)))
    assert "excess_flow" not in values
    assert values["indicator_state"].value == STATE_OFF


def test_stale_inputs_turn_excess_off(tmp_path):
    rig = bench(tmp_path)
    saved(rig)
    indicator = indicate(rig)
    t = datetime.now(UTC)
    indicator.process(batch(t, *flows(100, 95)))
    values = derived(indicator.process(batch(t+timedelta(seconds=40))))
    assert "excess_flow" not in values
    assert values["indicator_state"].value == STATE_OFF


def test_bad_quality_and_instrument_failure_are_excluded(tmp_path):
    rig = bench(tmp_path)
    saved(rig)
    indicator = indicate(rig)
    t = datetime.now(UTC)
    indicator.process(batch(t, *flows(100, 95)))
    failure = PollingFailure("outlet", "read", "OSError", "lost")
    values = derived(indicator.process(PollingBatch(t, t+timedelta(seconds=6), (), (failure,), ())))
    assert "excess_flow" not in values
    bad = MeasurementRecord("outlet", "mass_flow", Measurement(999., "SCCM", t+timedelta(seconds=7), Quality.STALE))
    indicator.process(PollingBatch(t, t+timedelta(seconds=7), (bad,), (), ()))
    assert ("outlet", "mass_flow") not in indicator._latest


def test_nothing_is_calculated_during_a_leak_check(tmp_path):
    rig = bench(tmp_path)
    saved(rig)
    paused = [True]
    indicator = indicate(rig, is_paused=lambda: paused[0])
    t = datetime.now(UTC)
    values = derived(indicator.process(batch(t, *flows(100, 95))))
    assert values["indicator_state"].value == STATE_OFF
    assert "excess_flow" not in values
    # The check's readings must not leak into the first mean after it.
    paused[0] = False
    values = derived(indicator.process(batch(t+timedelta(seconds=6), *flows(100, 100))))
    assert values["excess_flow"].value == pytest.approx(11)


def test_recording_keeps_its_calibration_until_it_stops(tmp_path):
    rig = bench(tmp_path)
    first = saved(rig, slope=.9, created=datetime.now(UTC)-timedelta(days=1))
    recording = [False]
    indicator = indicate(rig, is_recording=lambda: recording[0])
    snapshot = json.loads(indicator.snapshot()["excess_flow_json"])
    assert snapshot["calibration"]["slope"] == first["slope"]
    recording[0] = True
    saved(rig, slope=.5)
    assert indicator.current_calibration()["slope"] == .9
    recording[0] = False
    assert indicator.current_calibration()["slope"] == .5


def test_calibration_for_missing_instruments_is_not_used(tmp_path):
    rig = bench(tmp_path)
    saved(rig, outlet_id="gone")
    assert indicate(rig).current_calibration() is None


def test_current_source_is_frozen_during_recording(tmp_path):
    rig = bench(tmp_path, supply=True)
    recording = [False]
    indicator = indicate(rig, is_recording=lambda: recording[0])
    assert indicator.supply_id == "supply"
    indicator.select_supply("supply")
    with pytest.raises(ValueError):
        indicator.select_supply("other")
    recording[0] = True
    with pytest.raises(RuntimeError):
        indicator.select_supply("supply")


# Leak check ------------------------------------------------------------------


class Feed:
    """Stand-in for polling: a 5 % loss on the MFC's commanded flow."""

    def __init__(self, rig, *, noise=0., consumers=()):
        self.rig, self.noise, self.count = rig, noise, 0
        self.consumers = consumers or (rig.runner.observe,)
        self._stopped = Event()
        self._thread = Thread(target=self._run)

    def _run(self):
        while not self._stopped.wait(.01):
            q = self.rig.mfc.flow_setpoint*1000
            self.count += 1
            wobble = self.noise*q*(1 if self.count % 2 else -1)
            item = batch(datetime.now(UTC), *flows(q, .95*q+wobble),
                         ("outlet", "gas_temperature", 25., "degC"), ("outlet", "absolute_pressure", 1., "bar"))
            for consumer in self.consumers:
                consumer(item)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stopped.set()
        self._thread.join(2)


def quick_plan(*values, settle_s=.03, average_s=.25):
    return LeakCheckPlan.from_flow_lists({"inlet": values or (25., 50., 100.)}, "outlet", settle_s=settle_s, average_s=average_s, notes="bench")


def trace_rows(rig):
    with open(rig.runner.folder/TRACE_FILE, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def test_full_check_gives_points_line_and_trace(tmp_path):
    rig = bench(tmp_path)
    rig.control.execute(SetMfcFlow("inlet", 40, CommandSource.MANUAL, "SCCM"))
    with Feed(rig):
        rig.runner.start(quick_plan())
        assert rig.runner.join(8), rig.runner.status
    runner = rig.runner
    assert runner.complete
    assert [p["set_sccm"]["inlet"] for p in runner.points] == [25, 50, 100]
    assert runner.fit["slope"] == pytest.approx(.95)
    assert runner.fit["intercept_sccm"] == pytest.approx(0, abs=1e-6)
    assert runner.points[0]["conditions"]["pressure_pa"] == pytest.approx(1e5)
    # Back where the operator had it, and control released.
    assert rig.mfc.flow_setpoint == pytest.approx(.04)
    assert rig.control.mode is ControlMode.IDLE
    rows = trace_rows(rig)
    assert {r["phase"] for r in rows} == {SETTLING, AVERAGING}
    assert {r["condition"] for r in rows} == {"1", "2", "3"}
    assert set(rows[0]) >= {"timestamp_utc", "elapsed_s", "set_sccm_inlet", "mfc_sccm_inlet", "mfm_sccm_outlet", "temperature_c", "pressure_pa"}


def test_calibration_is_only_used_once_saved(tmp_path):
    rig = bench(tmp_path)
    with Feed(rig):
        rig.runner.start(quick_plan())
        assert rig.runner.join(8), rig.runner.status
    assert not (rig.runner.folder/CALIBRATION_FILE).exists()
    assert rig.store.latest("test-flow") is None
    assert rig.runner.has_unsaved_points
    result = rig.runner.save()
    assert (rig.runner.folder/CALIBRATION_FILE).exists()
    assert rig.store.latest("test-flow")["id"] == result["id"]
    assert result["trace"] == TRACE_FILE
    assert result["notes"] == "bench"
    assert result["slope"] == pytest.approx(.95)
    assert not rig.runner.can_save
    with pytest.raises(RuntimeError):
        rig.runner.save()


def test_saved_line_drives_the_live_indicator(tmp_path):
    rig = bench(tmp_path)
    with Feed(rig):
        rig.runner.start(quick_plan())
        assert rig.runner.join(8), rig.runner.status
    rig.runner.save()
    values = derived(indicate(rig).process(batch(datetime.now(UTC), *flows(100, 97))))
    assert values["excess_flow"].value == pytest.approx(2, abs=.01)


def test_noisy_data_flags_the_point_and_carries_on(tmp_path):
    rig = bench(tmp_path)
    with Feed(rig, noise=.2):
        rig.runner.start(quick_plan())
        assert rig.runner.join(8), rig.runner.status
    assert rig.runner.complete
    assert len(rig.runner.points) == 3
    assert all("noisy" in p["flags"] for p in rig.runner.points)
    assert rig.mfc.status is DeviceStatus.READY
    assert rig.mfc.flow_setpoint == 0


def test_condition_without_readings_is_left_out_with_a_warning(tmp_path):
    rig = bench(tmp_path)
    rig.runner.start(quick_plan())
    assert rig.runner.join(8), rig.runner.status
    assert rig.runner.points == []
    assert rig.runner.fit is None
    assert sum("too few readings" in w for w in rig.runner.warnings) == 3
    assert not rig.runner.can_save


def test_stop_restores_flow_and_keeps_points_and_trace(tmp_path):
    rig = bench(tmp_path)
    rig.control.execute(SetMfcFlow("inlet", 40, CommandSource.MANUAL, "SCCM"))
    with Feed(rig):
        rig.runner.start(quick_plan(25., 50., 75., 100., settle_s=.01, average_s=.2))
        while len(rig.runner.points) < 2:
            assert rig.runner.is_running, rig.runner.status
            rig.runner._stop.wait(.01)
        rig.runner.stop()
        assert rig.runner.join(5)
    assert not rig.runner.complete
    assert rig.mfc.flow_setpoint == pytest.approx(.04)
    assert rig.runner.can_save
    assert rig.runner.save()["complete"] is False
    assert trace_rows(rig)


def test_stopping_early_keeps_the_trace_without_a_calibration(tmp_path):
    rig = bench(tmp_path)
    with Feed(rig):
        rig.runner.start(quick_plan(settle_s=5.))
        while not rig.runner.rows:
            assert rig.runner.is_running
            rig.runner._stop.wait(.01)
        rig.runner.stop()
        assert rig.runner.join(5)
    assert trace_rows(rig)
    assert not rig.runner.can_save
    assert not (rig.runner.folder/CALIBRATION_FILE).exists()


def test_lost_instrument_stops_without_a_global_safe_state(tmp_path):
    rig = bench(tmp_path)
    pump_like = SimulatedMassFlowController("other", MassFlowControllerLimits(100, "SCCM"))
    rig.manager.register(pump_like)
    pump_like.connect()
    rig.control.execute(SetMfcFlow("other", 30, CommandSource.MANUAL, "SCCM"))
    rig.control.execute(SetMfcFlow("inlet", 40, CommandSource.MANUAL, "SCCM"))
    with Feed(rig):
        rig.runner.start(quick_plan(settle_s=5.))
        while not rig.runner.rows:
            rig.runner._stop.wait(.01)
        rig.meter.disconnect()
        assert rig.runner.join(5)
    assert "outlet is not ready" in rig.runner.status
    assert rig.mfc.flow_setpoint == pytest.approx(.04)
    assert pump_like.flow_setpoint == 30
    assert rig.control.mode is ControlMode.IDLE


def test_safety_action_is_never_undone(tmp_path):
    rig = bench(tmp_path)
    rig.control.execute(SetMfcFlow("inlet", 40, CommandSource.MANUAL, "SCCM"))
    with Feed(rig):
        rig.runner.start(quick_plan(settle_s=5.))
        while rig.mfc.flow_setpoint != .025:
            rig.runner._stop.wait(.01)
        rig.control.enter_global_safe_state()
        assert rig.runner.join(5)
    assert rig.mfc.flow_setpoint == 0
    assert "safety" in rig.runner.status
    assert rig.control.mode is ControlMode.IDLE


def test_check_owns_control_exclusively(tmp_path):
    rig = bench(tmp_path)
    with Feed(rig):
        rig.runner.start(quick_plan(settle_s=5.))
        with pytest.raises(ControlAccessError):
            rig.control.execute(SetMfcFlow("inlet", 10, CommandSource.MANUAL, "SCCM"))
        with pytest.raises(ControlAccessError):
            rig.control.begin_recipe_control()
        with pytest.raises(RuntimeError):
            rig.runner.start(quick_plan())
        rig.runner.stop()
        assert rig.runner.join(5)


def test_refused_while_a_recipe_controls_the_rig(tmp_path):
    rig = bench(tmp_path)
    rig.control.begin_recipe_control()
    with pytest.raises(ControlAccessError):
        rig.runner.start(quick_plan())
    assert rig.runner.folder is None


def test_start_conditions(tmp_path):
    rig = bench(tmp_path, supply=True)
    supply = rig.manager.get("supply")
    supply.set_output_enabled(True)
    with pytest.raises(RuntimeError, match="output off"):
        rig.runner.start(quick_plan())
    supply.set_output_enabled(False)
    with pytest.raises(ValueError, match="limit"):
        rig.runner.start(quick_plan(25., 500.))
    rig.runner.monitoring = lambda: False
    with pytest.raises(RuntimeError, match="monitoring"):
        rig.runner.start(quick_plan())
    assert rig.control.mode is ControlMode.IDLE


def test_disconnected_supply_is_a_warning_not_a_block(tmp_path):
    rig = bench(tmp_path, supply=True)
    rig.manager.get("supply").disconnect()
    rig.runner.start(quick_plan(settle_s=5.))
    assert any("supply is not connected" in w for w in rig.runner.warnings)
    rig.runner.stop()
    assert rig.runner.join(5)


def test_unit_mismatch_never_reaches_the_trace_as_a_number(tmp_path):
    rig = bench(tmp_path)
    plan = quick_plan(settle_s=5.)
    rig.runner.start(plan)
    while rig.runner._phase is None:
        rig.runner._stop.wait(.01)
    t = datetime.now(UTC)
    rig.runner.observe(batch(t, ("inlet", "mass_flow", 50., "bananas"), ("outlet", "mass_flow", 47., "SCCM")))
    rig.runner.stop()
    assert rig.runner.join(5)
    row = trace_rows(rig)[0]
    assert row["mfc_sccm_inlet"] == ""
    assert float(row["mfm_sccm_outlet"]) == 47


def test_recording_runs_alongside_a_check_with_condition_markers(tmp_path):
    rig = bench(tmp_path)
    indicator = indicate(rig, is_paused=lambda: rig.runner.is_running)
    recorder = ExperimentRecorder(context_provider=indicator.snapshot)
    rig.runner.event_sink = recorder.record_event
    recorder.start(metadata=ExperimentMetadata("leak-during-run", "tester"), root_directory=tmp_path/"runs")
    assert recorder.is_recording

    def through_recorder(item):
        rig.runner.observe(item)
        recorder.record_batch(indicator.process(item))

    with Feed(rig, consumers=(through_recorder,)):
        rig.runner.start(quick_plan())
        assert rig.runner.join(8), rig.runner.status
    recorder.stop()
    directory = recorder.experiment_directory
    measurements = (directory/"measurements.journal.jsonl").read_text(encoding="utf-8")
    events = (directory/"events.journal.jsonl").read_text(encoding="utf-8")
    assert "mass_flow" in measurements
    assert "Leak check started" in events
    assert "condition 3/3" in events


def test_export_holds_the_calibration_and_derived_channels(tmp_path):
    from openpyxl import load_workbook
    rig = bench(tmp_path)
    item = saved(rig)
    indicator = indicate(rig)
    recorder = ExperimentRecorder(context_provider=indicator.snapshot)
    recorder.start(metadata=ExperimentMetadata("flow-test", "tester"), root_directory=tmp_path/"runs")
    recorder.record_batch(indicator.process(batch(datetime.now(UTC), *flows(100, 95))))
    recorder.stop()
    book = load_workbook(next(recorder.experiment_directory.glob("*.xlsx")), read_only=True)
    assert any("excess_flow" in str(cell) for row in book["Data"].values for cell in row)
    assert any(item["id"] in str(cell) for row in book["Calibrations"].values for cell in row)
    book.close()


# Window ----------------------------------------------------------------------


def row(elapsed, phase=SETTLING, condition=1, inlet=25., outlet=24.):
    return TraceRow(datetime.now(UTC), elapsed, condition, phase, {"inlet": 25.}, {"inlet": inlet}, outlet)


def test_trace_breaks_at_missing_readings():
    rows = [row(0), row(60, outlet=None), row(120), row(180)]
    assert [len(s) for s in trace_segments(rows, lambda r: r.outlet_sccm)] == [1, 2]
    assert trace_segments(rows, lambda r: r.inlet_total)[0][1] == (1., 25.)


def test_averaging_spans_per_condition():
    rows = [row(0), row(60, AVERAGING), row(120, AVERAGING), row(180, SETTLING, 2), row(240, AVERAGING, 2), row(300, AVERAGING, 2)]
    assert averaging_spans(rows) == [(1., 2.), (4., 5.)]


def test_delta_and_percentage_of_a_raw_reading():
    assert delta_flow(row(0, inlet=100., outlet=95.)) == pytest.approx(-5)
    assert outlet_percent(row(0, inlet=100., outlet=95.)) == pytest.approx(95)
    assert delta_flow(row(0, outlet=None)) is None
    assert outlet_percent(row(0, inlet=None)) is None
    # Noise over an inlet of almost nothing is not a percentage.
    assert outlet_percent(row(0, inlet=.5, outlet=.4)) is None


@pytest.mark.parametrize("low,high", [(0, 108), (0, 3.7), (20, 95), (0, 0.4), (-26, 3), (-0.4, 0.2)])
def test_axis_ticks_cover_the_range(low, high):
    ticks = nice_ticks(low, high)
    assert ticks[0] <= low
    assert ticks[-1] >= high
    assert 2 <= len(ticks) <= 12


def test_window_shows_a_check_and_saves_it(tmp_path):
    import tkinter as tk
    from rig_control.ui.operation.leak_check_window import LeakCheckWindow
    rig = bench(tmp_path)
    indicator = indicate(rig)
    root = tk.Tk()
    root.withdraw()
    window = None
    try:
        window = LeakCheckWindow(root, rig.runner, indicator, preferences_file=tmp_path/"prefs.json")
        window.window.update_idletasks()
        assert str(window.save["state"]) == "disabled"
        assert "4 conditions" in window.duration["text"]
        window.lines["inlet"][1].set("25, 50")
        window.settle.set("0.03")
        window.average.set("0.25")
        assert "2 conditions" in window.duration["text"]
        with Feed(rig):
            window._start()
            assert rig.runner.join(8), rig.runner.status
        window._tick()
        assert str(window.save["state"]) == "normal"
        assert "This check" in window.calibration_title["text"]
        assert "Leak check display" not in window.status["text"]
        for canvas in (window.flows_canvas, window.calibration_canvas, window.delta_canvas, window.percent_canvas):
            assert canvas.find_all()
        for canvas in (window.delta_canvas, window.percent_canvas):
            assert any(canvas.type(item) == "text" and canvas.itemcget(item, "text") == "outlet = inlet" for item in canvas.find_all())
        window._save()
        window._tick()
        assert rig.store.latest("test-flow") is not None
        assert load_preferences(tmp_path/"prefs.json")["lines"]["inlet"]["flows"] == "25, 50"
    finally:
        if window:
            window.close()
        root.destroy()


def test_window_shows_saved_calibration_when_idle(tmp_path):
    import tkinter as tk
    from rig_control.ui.operation.leak_check_window import LeakCheckWindow
    rig = bench(tmp_path, supply=True)
    item = saved(rig)
    root = tk.Tk()
    root.withdraw()
    window = None
    try:
        window = LeakCheckWindow(root, rig.runner, indicate(rig), preferences_file=tmp_path/"prefs.json")
        window.window.update_idletasks()
        assert item["id"] in window.calibration_title["text"]
        # One supply needs no choice.
        assert window.supply_box is None
    finally:
        if window:
            window.close()
        root.destroy()
