"""Standalone inlet-versus-outlet leak check.

Steps the chosen MFCs through a list of flows, averages each condition, and
writes every reading to a trace file. It knows nothing about current or FE.
"""
import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from math import isfinite
import re
from statistics import mean, stdev
from threading import Event as StopEvent, RLock, Thread
from time import monotonic

from rig_control.control.commands import CommandSource, SetMfcFlow
from rig_control.control.service import ControlMode
from rig_control.data.run_context import portable_value
from rig_control.devices.mass_flow_controller import MassFlowController, convert_mass_flow
from rig_control.devices.power_supply import PowerSupply
from rig_control.devices.pressure_controller import absolute_unit_factor
from rig_control.flow.calibration import TRACE_FILE, fit_line
from rig_control.models import DeviceStatus, Event, EventSeverity, Quality
from rig_control.rig_profile import DeviceCapability

MAXIMUM_READING_AGE_SECONDS = 5.
MINIMUM_SAMPLES = 2
NOISY_FRACTION = 0.03
SPARSE_COVERAGE = 0.8
SETTLING, AVERAGING = "settling", "averaging"
HUMIDITY_CHANNELS = ("humidity_co2_corrected", "humidity")


def parse_flows(text):
    """Read a typed list such as ``25, 50, 75 100`` into numbers."""
    try:
        return tuple(float(part) for part in re.split(r"[,;\s]+", text.strip()) if part)
    except ValueError as error:
        raise ValueError(f"Could not read the flow list {text!r}") from error


@dataclass(frozen=True)
class LeakCheckPlan:
    # One {MFC ID: SCCM} per condition.
    conditions: tuple[dict[str, float], ...]
    outlet_id: str
    settle_s: float = 60.
    average_s: float = 60.
    notes: str = ""

    def __post_init__(self):
        object.__setattr__(self, "conditions", tuple(self.conditions))
        if not self.conditions or not self.conditions[0]:
            raise ValueError("Choose at least one MFC and one flow")
        if not self.outlet_id or self.outlet_id in self.inlet_ids:
            raise ValueError("Choose an outlet meter that is not one of the inlets")
        for flows in self.conditions:
            if any(not isfinite(flow) or flow < 0 for flow in flows.values()) or sum(flows.values()) <= 0:
                raise ValueError("Flows cannot be negative and each condition needs some flow")
        if len({sum(flows.values()) for flows in self.conditions}) < 2:
            raise ValueError("A line needs at least two different total flows")
        if not (isfinite(self.settle_s) and self.settle_s >= 0):
            raise ValueError("Settle time cannot be negative")
        if not (isfinite(self.average_s) and self.average_s > 0):
            raise ValueError("Average time must be positive")

    @classmethod
    def from_flow_lists(cls, flows, outlet_id, *, settle_s=60., average_s=60., notes=""):
        """A one-value list holds that MFC constant across the other MFC's steps."""
        count = max((len(values) for values in flows.values()), default=0)
        if count == 0:
            raise ValueError("Choose at least one MFC and one flow")
        if any(len(values) not in {1, count} for values in flows.values()):
            raise ValueError("Each MFC needs the same number of flows, or a single constant flow")
        return cls(tuple({key: values[index if len(values) > 1 else 0] for key, values in flows.items()}
                         for index in range(count)), outlet_id, settle_s, average_s, notes)

    @property
    def inlet_ids(self):
        return tuple(self.conditions[0])

    @property
    def duration_s(self):
        return len(self.conditions)*(self.settle_s+self.average_s)


@dataclass(slots=True)
class TraceRow:
    time: datetime
    elapsed_s: float
    condition: int
    phase: str
    set_sccm: dict
    flows: dict
    outlet_sccm: float | None
    extras: dict = field(default_factory=dict)

    @property
    def set_total(self):
        return sum(self.set_sccm.values())

    @property
    def inlet_total(self):
        return None if None in self.flows.values() else sum(self.flows.values())


def _sd(values):
    return stdev(values) if len(values) > 1 else 0.


def _celsius(measurement):
    unit = measurement.unit.strip().casefold().replace("°", "")
    if unit in {"c", "degc", "celsius"}:
        return measurement.value
    if unit in {"k", "kelvin"}:
        return measurement.value-273.15
    if unit in {"f", "degf", "fahrenheit"}:
        return (measurement.value-32)*5/9
    raise ValueError(f"Unknown temperature unit {measurement.unit}")


class LeakCheckRunner:
    def __init__(self, manager, control, store, profile, *, monitoring=lambda: False, event_sink=lambda event: None):
        self.manager, self.control, self.store, self.profile = manager, control, store, profile
        self.monitoring, self.event_sink = monitoring, event_sink
        self.lock = RLock()
        self._stop = StopEvent()
        self._thread = None
        self._phase = None
        self._condition = 0
        self._set_flows = {}
        self._latest = {}
        self._last_outlet_stamp = None
        self._started_at = None
        self._trace_file = None
        self._trace_writer = None
        self.plan = None
        self.folder = None
        self.rows = []
        self.points = []
        self.fit = None
        self.warnings = ()
        self.status = "Idle"
        self.complete = False
        self.saved = False

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def available(self):
        return bool(self.inlets() and self.outlets())

    def inlets(self):
        return tuple(key for key in self.manager.device_ids if isinstance(self.manager.get(key), MassFlowController))

    def outlets(self):
        return tuple(role.device_id for role in self.profile.enabled_roles
                     if role.device_id in self.manager.device_ids and role.capability in
                     {DeviceCapability.MASS_FLOW_METER, DeviceCapability.BACK_PRESSURE_CONTROLLER})

    @property
    def can_save(self):
        return not self.is_running and not self.saved and self.fit is not None

    @property
    def has_unsaved_points(self):
        return bool(self.points) and not self.saved

    def _humidity_ids(self):
        return tuple(role.device_id for role in self.profile.enabled_roles
                     if role.device_id in self.manager.device_ids and role.capability is DeviceCapability.HUMIDITY_SENSOR)

    def _preflight(self, plan):
        """Refuse only what makes the measurement meaningless or the rig unsafe."""
        if not self.monitoring():
            raise RuntimeError("Start monitoring before running a leak check")
        for key in (*plan.inlet_ids, plan.outlet_id):
            device = self.manager.get(key)
            if device.status is not DeviceStatus.READY or not getattr(device, "control_ready", True):
                raise RuntimeError(f"{key}: instrument is not ready")
        for key in plan.inlet_ids:
            device = self.manager.get(key)
            limit = convert_mass_flow(device.limits.maximum_flow, device.limits.flow_unit, "SCCM")
            if max(flows[key] for flows in plan.conditions) > limit:
                raise ValueError(f"{key}: requested flow exceeds its {limit:g} sccm limit")
        for key in self.manager.device_ids:
            device = self.manager.get(key)
            if isinstance(device, PowerSupply) and device.status is DeviceStatus.READY and device.output_enabled:
                raise RuntimeError(f"{key}: turn the output off before a leak check")

    def _warnings(self, plan):
        notes = []
        gases = {key: getattr(getattr(self.manager.get(key), "current_state", None), "gas", None)
                 for key in (*plan.inlet_ids, plan.outlet_id)}
        if len({str(gas).casefold() for gas in gases.values()}) > 1:
            notes.append("Instrument gases differ: "+", ".join(f"{k}={g}" for k, g in gases.items()))
        for key in self.manager.device_ids:
            device = self.manager.get(key)
            if isinstance(device, PowerSupply) and device.status is not DeviceStatus.READY:
                notes.append(f"{key} is not connected, so its output state is unknown")
            elif isinstance(device, MassFlowController) and key not in plan.inlet_ids and device.flow_setpoint:
                notes.append(f"{key} is not in this check but has a setpoint")
        if getattr(self.manager.get(plan.outlet_id), "valve_hold", False):
            notes.append(f"{plan.outlet_id}: valve is held, so gas may not reach the meter")
        return tuple(notes)

    def start(self, plan):
        with self.lock:
            if self.is_running:
                raise RuntimeError("A leak check is already running")
            self._preflight(plan)
            restore = {key: (self.manager.get(key).flow_setpoint, self.manager.get(key).limits.flow_unit) for key in plan.inlet_ids}
            self.control.begin_calibration_control()
            try:
                started_at = datetime.now(UTC)
                self.folder = self.store.new_folder(started_at)
                self._open_trace(plan)
            except Exception:
                self.control.end_calibration_control()
                raise
            self.plan = plan
            self.warnings = self._warnings(plan)
            self.rows, self.points, self.fit = [], [], None
            self.complete = self.saved = False
            self._started_at = started_at
            self._latest.clear()
            self._last_outlet_stamp = None
            self._phase, self._condition = None, 0
            self._stop.clear()
            self.status = "Starting"
            self._thread = Thread(target=self._run, args=(plan, restore), daemon=True, name="leak-check")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def join(self, timeout=10):
        if self._thread:
            self._thread.join(timeout)
        return not self.is_running

    def _emit(self, message, error=False):
        try:
            self.event_sink(Event(source="leak_check", message=message,
                                  severity=EventSeverity.ERROR if error else EventSeverity.INFO))
        except Exception:
            pass  # A journal failure must never stop flows being restored.

    # Trace ------------------------------------------------------------------

    def _trace_columns(self, plan):
        return (["timestamp_utc", "elapsed_s", "condition", "phase"]
                + [f"set_sccm_{key}" for key in plan.inlet_ids]
                + [f"mfc_sccm_{key}" for key in plan.inlet_ids]
                + [f"mfm_sccm_{plan.outlet_id}", "temperature_c", "pressure_pa"]
                + [f"rh_{key}" for key in self._humidity_ids()])

    def _open_trace(self, plan):
        self._trace_file = open(self.folder/TRACE_FILE, "w", newline="", encoding="utf-8")
        self._trace_writer = csv.writer(self._trace_file)
        self._trace_writer.writerow(self._trace_columns(plan))
        self._trace_file.flush()

    def _write_trace(self, row, plan):
        if self._trace_writer is None:
            return
        blank = lambda value: "" if value is None else value
        try:
            self._trace_writer.writerow([row.time.isoformat(), round(row.elapsed_s, 3), row.condition, row.phase]
                + [row.set_sccm[key] for key in plan.inlet_ids]
                + [blank(row.flows[key]) for key in plan.inlet_ids]
                + [blank(row.outlet_sccm), blank(row.extras.get("temperature_c")), blank(row.extras.get("pressure_pa"))]
                + [blank(row.extras.get(f"rh_{key}")) for key in self._humidity_ids()])
            self._trace_file.flush()
        except OSError as error:
            self._close_trace()
            self.warnings += (f"Trace file stopped: {error}",)

    def _close_trace(self):
        file, self._trace_file, self._trace_writer = self._trace_file, None, None
        if file is not None:
            try:
                file.close()
            except OSError:
                pass

    def _reading(self, device, channel, stamp):
        m = self._latest.get((device, channel))
        if m is None or m.quality is not Quality.GOOD or (stamp-m.timestamp).total_seconds() > MAXIMUM_READING_AGE_SECONDS:
            return None
        return m

    def _flow(self, device, stamp):
        m = self._reading(device, "mass_flow", stamp)
        try:
            return None if m is None else convert_mass_flow(m.value, m.unit, "SCCM")
        except ValueError:
            return None

    def _extras(self, plan, stamp):
        extras = {}
        temperature = self._reading(plan.outlet_id, "gas_temperature", stamp)
        pressure = self._reading(plan.outlet_id, "absolute_pressure", stamp)
        try:
            if temperature is not None:
                extras["temperature_c"] = _celsius(temperature)
            if pressure is not None:
                extras["pressure_pa"] = pressure.value*absolute_unit_factor(pressure.unit)
        except ValueError:
            pass
        for key in self._humidity_ids():
            for channel in HUMIDITY_CHANNELS:
                m = self._reading(key, channel, stamp)
                if m is not None:
                    extras[f"rh_{key}"] = m.value
                    break
        return extras

    def observe(self, batch):
        """Take fresh acquisition records; one trace row per new outlet reading."""
        with self.lock:
            plan = self.plan
            if self._phase is None or plan is None:
                return
            for failure in batch.failures:
                for key in tuple(self._latest):
                    if key[0] == failure.device_id:
                        del self._latest[key]
            for record in batch.measurements:
                self._latest[(record.device_id, record.channel)] = record.measurement
            outlet = next((r.measurement for r in batch.measurements
                           if r.device_id == plan.outlet_id and r.channel == "mass_flow"), None)
            if outlet is None or (self._last_outlet_stamp is not None and outlet.timestamp <= self._last_outlet_stamp):
                return
            self._last_outlet_stamp = stamp = outlet.timestamp
            row = TraceRow(stamp, (stamp-self._started_at).total_seconds(), self._condition, self._phase,
                dict(self._set_flows), {key: self._flow(key, stamp) for key in plan.inlet_ids},
                self._flow(plan.outlet_id, stamp), self._extras(plan, stamp))
            self.rows.append(row)
            self._write_trace(row, plan)

    # Run --------------------------------------------------------------------

    def _check(self):
        if self.control.mode is not ControlMode.CALIBRATION_ACTIVE:
            raise _Revoked()
        if self._stop.is_set():
            raise _Stopped()
        if not self.monitoring():
            raise RuntimeError("monitoring stopped")
        for key in (*self.plan.inlet_ids, self.plan.outlet_id):
            if self.manager.get(key).status is not DeviceStatus.READY:
                raise RuntimeError(f"{key} is not ready")

    def _wait(self, seconds):
        until = monotonic()+seconds
        while True:
            self._check()
            remaining = until-monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(remaining, 0.1))

    def _set_phase(self, condition, phase, flows):
        with self.lock:
            self._condition, self._phase, self._set_flows = condition, phase, dict(flows)
        self.status = f"Condition {condition}/{len(self.plan.conditions)}: {phase}"

    def _point(self, condition, flows, plan):
        with self.lock:
            rows = [r for r in self.rows if r.condition == condition and r.phase == AVERAGING
                    and r.outlet_sccm is not None and r.inlet_total is not None]
        if len(rows) < MINIMUM_SAMPLES:
            return None
        inlet = [r.inlet_total for r in rows]
        outlet = [r.outlet_sccm for r in rows]
        flags = []
        if any(_sd(values) > NOISY_FRACTION*max(mean(values), 1) for values in (inlet, outlet)):
            flags.append("noisy")
        if (rows[-1].time-rows[0].time).total_seconds() < SPARSE_COVERAGE*plan.average_s:
            flags.append("sparse")
        conditions = {}
        for row in rows:
            for key, value in row.extras.items():
                conditions.setdefault(key, []).append(value)
        return dict(condition=condition, set_sccm=dict(flows), inlet_sccm=mean(inlet), outlet_sccm=mean(outlet),
            flows={key: mean(r.flows[key] for r in rows) for key in plan.inlet_ids},
            sd_inlet_sccm=_sd(inlet), sd_outlet_sccm=_sd(outlet), count=len(rows), flags=flags,
            conditions={key: mean(values) for key, values in conditions.items()})

    def _run(self, plan, restore):
        ending = "Leak check finished"
        failed = False
        try:
            self._emit(f"Leak check started; trace in {self.folder}")
            for condition, flows in enumerate(plan.conditions, 1):
                self._check()
                self._set_phase(condition, SETTLING, flows)
                self._emit(f"Leak check condition {condition}/{len(plan.conditions)}: {json.dumps(flows)} sccm")
                for key, flow in flows.items():
                    self.control.execute(SetMfcFlow(key, flow, CommandSource.CALIBRATION, "SCCM"))
                self._wait(plan.settle_s)
                self._set_phase(condition, AVERAGING, flows)
                self._wait(plan.average_s)
                point = self._point(condition, flows, plan)
                if point is None:
                    self.warnings += (f"Condition {condition} had too few readings and was left out",)
                    self._emit(f"Leak check condition {condition} had too few readings", error=True)
                    continue
                with self.lock:
                    self.points.append(point)
                    self.fit = fit_line(self.points)
            self.complete = True
        except _Stopped:
            ending = "Leak check stopped"
        except _Revoked:
            ending = "Leak check ended by a safety action; flows left as that action set them"
            failed = True
        except Exception as error:
            ending = f"Leak check stopped: {error}"
            failed = True
        finally:
            with self.lock:
                self._phase = None
                self._close_trace()
            failures = self._restore(restore)
            if failures:
                ending += "; COULD NOT RESTORE FLOW: "+"; ".join(failures)
                failed = True
            if self.control.mode is ControlMode.CALIBRATION_ACTIVE:
                self.control.end_calibration_control()
            self.status = ending+f" ({len(self.points)} points)"
            self._emit(self.status, error=failed)

    def _restore(self, restore):
        # After a safety action the rig stays as it was left: never undo it.
        if self.control.mode is not ControlMode.CALIBRATION_ACTIVE:
            return []
        failures = []
        for key, (flow, unit) in restore.items():
            try:
                self.control.execute(SetMfcFlow(key, flow, CommandSource.CALIBRATION, unit))
            except Exception as error:
                failures.append(f"{key}: {error}")
        return failures

    def _instrument_records(self, plan):
        records = {}
        for key in (*plan.inlet_ids, plan.outlet_id):
            device = self.manager.get(key)
            config = getattr(device, "control_configuration", None)
            records[key] = dict(gas=getattr(getattr(device, "current_state", None), "gas", None),
                identity=portable_value(getattr(device, "identity", None)),
                reported_serial=getattr(config, "serial_number", None),
                reported_model=getattr(config, "model", None))
        return json.loads(json.dumps(records))

    def save(self):
        """Keep the finished points as the calibration the live run will use."""
        with self.lock:
            if not self.can_save:
                raise RuntimeError("Needs a finished check with at least two different flows")
            plan = self.plan
            xs = [p["inlet_sccm"] for p in self.points]
            calibration = dict(type="leak_check", version=1, id=self.folder.name, created_utc=datetime.now(UTC).isoformat(),
                rig_profile_id=self.profile.profile_id, notes=plan.notes, inlet_ids=list(plan.inlet_ids), outlet_id=plan.outlet_id,
                instruments=self._instrument_records(plan), range_sccm=[min(xs), max(xs)], points=self.points,
                settle_s=plan.settle_s, average_s=plan.average_s, complete=self.complete, trace=TRACE_FILE, **self.fit)
            self.store.save(self.folder, calibration)
            self.saved = True
            self.status = f"Saved calibration {calibration['id']}"
        self._emit(self.status)
        return calibration


class _Stopped(Exception):
    pass


class _Revoked(Exception):
    pass
