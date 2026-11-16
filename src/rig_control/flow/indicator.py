"""Live excess flow and rough FE from the latest leak check.

    excess_flow = measured outlet - (slope * total inlet + intercept)
    h2_fe       = 100 * excess_flow / (SCCM_PER_AMP * current)
    co_fe       = 100 - h2_fe

Calculated once per outlet reading with no smoothing; analysis can average the
recorded values. Each CO replaces the CO2 it came from, so only H2 adds net gas:
excess flow is the H2 indicator. FE is a rough extra output; see decision 0024.
"""
from dataclasses import replace
import json
from threading import RLock

from rig_control.data.records import MeasurementRecord
from rig_control.devices.mass_flow_controller import convert_mass_flow
from rig_control.devices.power_supply import PowerSupply
from rig_control.flow.calibration import LeakCheckStore
from rig_control.models import Measurement, Quality

SOURCE = "excess_flow"
# Gas flow of a 2-electron product per ampere at 25 C and 1 atm.
SCCM_PER_AMP = 8.314462618*298.15/(2*96485.33212*101325.)*60e6
MAXIMUM_READING_AGE_SECONDS = 15.
MINIMUM_FE_CURRENT_AMPS = 0.01
STATE_OFF, STATE_EXCESS, STATE_EXCESS_AND_FE = 0., 1., 2.
STATE_UNIT = "0=off;1=excess;2=excess+FE"
ASSUMPTIONS = ["CO and H2 are the only products; their FE sums to 100%",
    "No correction for the CO2-set meter's response to CO, H2 or water vapour",
    "No correction for CO2 lost to carbonate",
    "Leak-check line still applies, including humidity at the time of the check"]


def excess_flow(calibration, inlet_sccm, outlet_sccm):
    return outlet_sccm-(calibration["slope"]*inlet_sccm+calibration["intercept_sccm"])


def rough_fe(excess_sccm, current_a):
    h2 = 100*excess_sccm/(SCCM_PER_AMP*current_a)
    return h2, 100-h2


class ExcessFlowIndicator:
    def __init__(self, manager, rig_profile_id, *, store=None, is_recording=lambda: False,
                 is_paused=lambda: False, supply_id=""):
        self.manager, self.rig_profile_id = manager, rig_profile_id
        self.store = store or LeakCheckStore()
        self.is_recording, self.is_paused = is_recording, is_paused
        self.lock = RLock()
        self.calibration = None
        self._supply_id = supply_id
        self._latest = {}
        self._output_on = {}
        self._state = None

    def supplies(self):
        return tuple(key for key in self.manager.device_ids if isinstance(self.manager.get(key), PowerSupply))

    @property
    def supply_id(self):
        supplies = self.supplies()
        return self._supply_id if self._supply_id in supplies else (supplies[0] if supplies else "")

    def select_supply(self, supply_id):
        with self.lock:
            if self.is_recording():
                raise RuntimeError("The current source is frozen during recording")
            if supply_id not in self.supplies():
                raise ValueError(f"Unknown power supply {supply_id!r}")
            self._supply_id = supply_id
            self._latest.clear()

    def _refresh_calibration(self):
        # A recording keeps the calibration stored in its metadata until it stops.
        if not self.is_recording():
            latest = self.store.latest(self.rig_profile_id)
            if latest is not None and set((*latest["inlet_ids"], latest["outlet_id"])) - set(self.manager.device_ids):
                latest = None
            self.calibration = latest

    def current_calibration(self):
        with self.lock:
            self._refresh_calibration()
            return self.calibration

    def snapshot(self):
        with self.lock:
            self._refresh_calibration()
            return {"excess_flow_json": json.dumps({
                "version": 2, "calibration": self.calibration, "current_source": self.supply_id,
                "sccm_per_amp": SCCM_PER_AMP, "calculated": "at each outlet reading, unsmoothed",
                "excess_flow": "outlet - (slope * inlet + intercept)",
                "h2_fe": "100 * excess_flow / (sccm_per_amp * current)", "co_fe": "100 - h2_fe",
                "assumptions": ASSUMPTIONS}, allow_nan=False)}

    def _store(self, record, calibration, supply_id):
        """Keep one usable reading; return its (stamp, value) if it is a new one."""
        device, channel, m = record.device_id, record.channel, record.measurement
        if device == supply_id and channel == "output_enabled":
            self._output_on[device] = bool(m.value)
            return None
        flow = channel == "mass_flow" and device in (*calibration["inlet_ids"], calibration["outlet_id"])
        if not (flow or (channel == "current" and device == supply_id)) or m.quality is not Quality.GOOD:
            return None
        try:
            value = convert_mass_flow(m.value, m.unit, "SCCM") if flow else (abs(m.value) if m.unit == "A" else None)
        except ValueError:
            return None
        if value is None:
            return None
        stamp = m.timestamp.timestamp()
        previous = self._latest.get((device, channel))
        if previous is not None and stamp <= previous[0]:
            return None
        self._latest[(device, channel)] = (stamp, value)
        return stamp, value

    def _fresh(self, key, now):
        reading = self._latest.get(key)
        return None if reading is None or now-reading[0] > MAXIMUM_READING_AGE_SECONDS else reading[1]

    def _values(self, outlet, now):
        c = self.calibration
        inlets = [self._fresh((key, "mass_flow"), now) for key in c["inlet_ids"]]
        if None in inlets:
            return {}, STATE_OFF
        excess = excess_flow(c, sum(inlets), outlet)
        values = dict(excess_flow=(excess, "SCCM"))
        supply = self.supply_id
        current = self._fresh((supply, "current"), now) if self._output_on.get(supply, True) else None
        if current is None or current < MINIMUM_FE_CURRENT_AMPS:
            return values, STATE_EXCESS
        h2, co = rough_fe(excess, current)
        values.update(h2_fe=(h2, "%"), co_fe=(co, "%"))
        return values, STATE_EXCESS_AND_FE

    def _with_values(self, batch, values, state, stamp):
        self._state = state
        values = {**values, "indicator_state": (state, STATE_UNIT)}
        records = tuple(MeasurementRecord(SOURCE, channel, Measurement(value, unit, stamp))
                        for channel, (value, unit) in values.items())
        return replace(batch, measurements=batch.measurements+records)

    def process(self, batch):
        """Add the derived channels when this batch holds a new outlet reading."""
        with self.lock:
            self._refresh_calibration()
            c = self.calibration
            finished = batch.finished_at
            if c is None or self.is_paused():
                self._latest.clear()
                if self._state == STATE_OFF or (self._state is None and c is None):
                    return batch
                return self._with_values(batch, {}, STATE_OFF, finished)
            for failure in batch.failures:
                for key in tuple(self._latest):
                    if key[0] == failure.device_id:
                        del self._latest[key]
            supply, outlet_id = self.supply_id, c["outlet_id"]
            outlet = None
            for record in batch.measurements:
                new = self._store(record, c, supply)
                if new is not None and record.device_id == outlet_id and record.channel == "mass_flow":
                    outlet = new
            if outlet is not None:
                stamp = next(r.measurement.timestamp for r in batch.measurements
                             if r.device_id == outlet_id and r.channel == "mass_flow")
                values, state = self._values(outlet[1], outlet[0])
                return self._with_values(batch, values, state, stamp)
            # A meter that has gone quiet must not leave its last value looking live.
            if self._state not in (None, STATE_OFF) and self._fresh((outlet_id, "mass_flow"), finished.timestamp()) is None:
                return self._with_values(batch, {}, STATE_OFF, finished)
            return batch
