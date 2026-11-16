# 0024 — Leak check and live excess flow

Status: Implemented; software-tested with simulated telemetry, not commissioned on a live rig.

Two separate things, in that order of importance:

1. A **leak check**: a quick measurement of outlet flow against inlet flow.
2. **Excess flow**: outlet flow during a run, minus what the leak check says to
   expect. A rough FE is one further calculated output from it.

GC remains the real product measurement and is entirely separate.

## Leak check

Operation shows **Leak check** when the rig has an MFC and an outlet meter or
BPR. The operator chooses the MFC(s), the outlet, a typed flow list, a settle
time and an average time. A one-value list holds that MFC constant. Monitoring
must be running.

The window has four panels covering the whole check. With several MFCs, inlet
values are totals. All of it is raw readings except the averaged points.

- **Flows against time:** inlet set, inlet at the MFC and outlet at the MFM.
- **Outlet against inlet:** as each condition finishes, its average is added as a
  point, with the fit and a dashed outlet = inlet diagonal.
- **Delta flow against time:** outlet minus inlet in sccm, dashed at 0.
- **Outlet as a percentage of inlet against time:** dashed at 100%. Inlet below
  1 sccm is left out, where the ratio is only noise.

The averaged period of each condition is shaded on the time panels. That average
is only how a condition becomes one point on the line; the readings stay raw in
the plots and the trace. The operator saves the calibration with **Save
calibration**; nothing is used until then. The newest saved calibration for the
rig is the one in use.

Each check has a folder under `calibrations/flow/` in the application data home:

- `trace.csv`: every reading, with its condition and phase, written line by line
  so a stopped or failed check keeps its trace. It also holds MFM temperature and
  pressure and RH from any humidity sensors. Nothing needs to be selected for this.
- `calibration.json`: the averaged points, `slope`, `intercept_sccm`, instruments
  and gas. Written only on save.

The result is `outlet = slope * inlet + intercept`, an ordinary least-squares
line. Call it outlet against inlet rather than a leak percentage: through a
humidified path the slope also includes the meter's response to water vapour.

### What stops a check

Control is exclusive, using the same service as recipes and manual commands.

- **Noisy or sparse data** flags the point (shown hollow) and the check carries
  on. A condition with too few readings is left out with a warning.
- **Stop, a lost instrument or stopped monitoring** ends the check and returns the
  MFCs to their previous setpoints. Finished points can still be saved.
- **A safety action** (global or device safe state) ends the check and leaves the
  rig as that action set it; the check never restores flows over it.

Bad data and lost instruments deliberately do not trigger the global safe state.
A noisy reading is not a hazard, and stopping every device on the rig for one
cost more than it protected.

It refuses to start only if an instrument it uses is not ready, a flow exceeds an
MFC's limit, or a connected supply has its output on. A disconnected supply,
differing gases, another MFC with a setpoint, or a held outlet valve are warnings.

### Recording

A check can run during a manual recording, and a recording can start during a
check. Readings reach the recording as usual, and the journal marks the start and
each condition. A recipe owns the rig's controls, so a check and a recipe cannot
run together. A recording keeps the calibration stored when it started; one saved
meanwhile applies from the next recording.

## Live excess flow and rough FE

`rig_control.flow.indicator` adds channels under source `excess_flow`, which use
the existing Operation, trend, recording and export paths:

```
excess_flow = measured outlet - (slope * total inlet + intercept)    [SCCM]
h2_fe       = 100 * excess_flow / (7.607 * current)                  [%]
co_fe       = 100 - h2_fe                                            [%]
```

Each CO replaces the CO2 it came from and adds no net flow, while each H2 is
extra gas, so excess flow is the H2 indicator. 7.607 sccm/A is the flow of a
2-electron product at 25 C and 1 atm. `indicator_state` records what was
available (0 off, 1 excess, 2 excess and FE).

Values are calculated once per outlet reading, from that reading and the latest
inlet and current readings no more than 15 s old, with no smoothing. Any averaging
belongs in analysis of the recorded values. Expect noise, most of all in FE, which
is a small signal divided by the current.

- Total inlet is the MFCs in the calibration. Gas from any other MFC that reaches
  the outlet counts as excess.
- Without a calibration or fresh readings, excess flow is not calculated.
- FE needs at least 0.01 A from the supply, whose output must be on. The current's
  sign is ignored. With several supplies, the leak-check window chooses one.
- Values are not clipped: a negative or above-100% FE is a useful warning sign.
- Nothing is calculated during a leak check, so its steps cannot look like gas
  production. A flow step in a run does show, for as long as the outlet takes to
  catch up with the inlet.

Recordings store the calibration, current source and formulas in metadata
(`excess_flow_json`) and the Excel `Calibrations` sheet, so results can be
recalculated without the calibration folder.

## How rough the FE is

Use excess flow trends and comparisons between runs; do not treat FE as a
measurement. It assumes CO and H2 are the only products and has no corrections:

- **The meter response biases it high.** The MFM is set to CO2, but CO and H2
  change the viscosity it relies on. An ideal-gas/Wilke viscosity estimate (dry gas,
  25 C, 100 sccm, not measured on this rig) gave about 24% for a true 10% H2 FE,
  60% for 50%, and 96% for 90%.
- **CO2 lost to carbonate biases it low**, because that gas never reaches the
  outlet. The two biases can partly cancel, so a sensible value is not proof of
  accuracy.
- **Humidity** different from the leak check shifts excess flow directly.
