# 0023 — Calibrated channels for pump flow and CO2 humidity

Status: Accepted and implemented.
Written by: Claude, from a design conversation with the project maintainer.
Claude took part in all of the reasoning recorded here and implemented it.

## Context

The EZO-HUM probes read RH consistently low in a CO2 ambient. This is known
from the literature, and the maintainer confirmed it with their own
saturated-salt calibration (MgCl2, NaCl, KCl under CO2). The result was a
linear fit that was reproducible on both probes. The rig's experiments
always run in a mainly CO2 ambient, so the maintainer wanted a CO2-corrected
RH estimate recorded next to the raw reading, without recalibrating the
probes themselves.

The Kamoer pumps already had a linear rpm-to-mL/min calibration, but flow
was only shown on screen. It also turned out that the pump's rpm was not
recorded as a channel at all, only as command events.

## Decision

- **One shared linear calibration, two kinds.**
  - The fit, history file, review/accept flow and Settings panel are shared.
  - Each kind only differs in its file format name, its point field names
    and its wording.
  - History files stay as `calibrations/<device_id>.json`. Device IDs are
    unique, so no migration was needed.
- **Calibrations are named by the operator.**
  - The name is prefilled with today's date (`YYYY-MM-DD`) and is editable.
  - A name already used for that device is refused, as a file save would be.
  - The automatic timestamp ID was dropped. Older pump calibrations keep
    their timestamp IDs as their names.
- **Calibrated values are recorded channels, always on.**
  - Each read, a calibrated value is added next to the raw one:
    `flow_setpoint` (mL/min) for a pump and `humidity_co2_corrected` (%RH)
    for an EZO-HUM probe.
  - The correction is applied whether or not CO2 is present. The app does
    not try to know the gas composition.
  - A device without an accepted calibration gets no extra channel.
- **Pumps report `speed_setpoint` and `running`.** These are the last
  confirmed command state, read without bus traffic, since the pump has no
  speed feedback.
- **The raw value is always kept.**
  - The run metadata records each active calibration's name, notes and fit
    as `calibrations_json`, so a run can be recalculated later with a better
    calibration.
  - The Excel overview lists one "Calibration: <device>" row giving the name
    and notes.
- **Recipes can set a pump by flow.**
  - "Pump flow (calibrated)" in mL/min sits next to "Pump speed" in rpm.
  - The flow is converted to rpm when the step runs, using the active
    calibration at that moment, so a recipe stays valid after recalibrating.
  - Each write's rpm and calibration name are recorded with the step's
    command event. 0 mL/min means 0 rpm.
  - A pump without a calibration, or a flow below the calibration line, is
    refused when the recipe is checked, before the run starts.
- **Corrected RH above 100 % is kept but marked uncertain,** following the
  raw humidity's rule. Calibrated pump flow is never negative.

## Alternatives considered

- **Switching the correction on and off by gas composition.** Rejected. The
  experiments are always mainly CO2. Small amounts of CO and H2 from the
  electrolyser may affect the correction, but per-composition calibrations
  were judged not worth the complexity for now.
- **Showing the calibrated values on screen only, like the pump flow used
  to be.** Rejected. The maintainer wanted them as lines on the graphs and in
  the exports, and recording them is the simplest way to get that.
- **Recalibrating the EZO-HUM probes on the device itself.** Rejected. A
  software correction keeps the raw reading and is easy to revise.
