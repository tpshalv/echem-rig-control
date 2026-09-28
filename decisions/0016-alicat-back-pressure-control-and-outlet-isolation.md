# 0016 — Alicat MC back-pressure control and outlet isolation

Status: Accepted and implemented.
Written by: Codex, from direct discussion with the project maintainer.

## Context

The rig may use an Alicat MC-series controller at the outlet of an
electrolyser, downstream of the sensing section and upstream of a GC.  In
this arrangement its valve regulates upstream pressure rather than inlet
flow.  The normal rig hardware rating is approximately 2.5 bara.

An MC is physically configurable as a mass-flow controller or as an
inverse pressure controller.  The selected role and its safe valve
orientation are properties of the installed instrument and plumbing; they
must not be guessed from the serial address or changed by ordinary rig
control software.

## Decision

The application represents an outlet controller as a distinct
`back_pressure_controller` role.  It uses the existing Alicat transport,
status parser, polling and recording path, but presents pressure controls
instead of a flow setpoint control.

The application is deliberately **read-only with respect to Alicat control
configuration**.  The live loop-control variable and register 20 inverse bit
are authoritative for distinguishing an MFC from a BPR: mass-flow/forward is
MFC, while pressure/inverse is BPR.  Model and serial number are retained as
useful information but are not prerequisites for role detection, and a
failed or unusual manufacturer query never prevents it.  The application
never sends a command that selects mass-flow versus pressure control or
enables inverse control.

The role is therefore **detected, not chosen**.  Adding a device scans the
selected port, reads each responding instrument's configuration, and assigns
the role automatically; the data-frame layout and units come from the
instrument's own readbacks, with the setpoint unit from LR taken as
authoritative and the saved frame confirmed against a live setpoint readback
before anything is stored.  There is no separate verification or
commissioning screen.  A controller whose LR/R20 query fails is never
assumed to be a meter or an MFC: only an instrument whose own data frame has
no setpoint column is recorded as a meter, and anything else is reported as
an unreadable configuration that the operator can retry.

Adding a detected BPR shows one hardware warning: the software cannot detect
whether the physical valve position and plumbing are correct.  The operator's
acknowledgement is stored with that installation, so ordinary reconnects do
not ask again.  Devices saved before this was stored can record it once from
Device Setup.

Every connection and readiness check re-reads the live control mode.  If a
saved role no longer matches the instrument, the mismatch is reported and
setpoint, resume and hold commands are blocked; the role is never silently
reassigned.  Ordinary measurement polling re-reads only the loop variable and
register 20, not the full identity and data-frame sequence, which belongs to
setup.

The current pressure-control adapter accepts an absolute-pressure control
loop (loop variable 34) only.  Alicat pressure here is absolute, so the
operation UI offers one absolute setpoint and no derived gauge reading or
gauge entry for this device.  A native gauge or differential control loop,
or a separate gauge sensor, would need its own unit mapping before being
enabled.

Pressure setpoints require an explicit absolute or gauge unit.  They are
converted to absolute pressure before validation.  The ordinary software
ceiling is 2.5 bara.  High Pressure Mode is a deliberately non-prominent
setting; when enabled it exposes an editable higher ceiling, while the
controller's verified range and commissioned installed-device ceiling still
apply.  The UI and recorded context clearly state when this override is in
effect.  This is an operating guard only, not a pressure-protection system.

Polling records absolute pressure, pressure setpoint, mass and volumetric flow,
temperature, totalizer where configured, valve drive where present, and
valve-hold state.

For a global safe-state request the application first asks flow sources and
power-producing devices to enter their safe states, then sends the verified
outlet BPR its documented hold/close command.  This isolates the GC side and
may trap gas upstream.  It does not automatically release the hold: resuming
pressure regulation is an explicit, warned operator action after the current
configuration, identity and pressure target have been rechecked.

## Consequences and open hardware work

Closing the outlet valve is not pressure relief.  Continued gas generation,
thermal expansion, failed upstream shutdown, or a failed controller can
still increase trapped pressure.  A future hardware change must add a
properly rated pressure-relief device or rupture disc protecting the lowest
rated part of the pressurised system.  This automatic protection is distinct
from a separately engineered, normally safe vent valve and vent path, which
may later be added for controlled depressurisation.  Neither device exists in
the current software model, and the software must not claim that either is
present.

## Verification boundary

The serial query and parser behaviour are covered by protocol-contract
tests, including the firmware-9 full-scale fallback, and the complete add
path is covered end to end: detected MFC, detected BPR with its single
acknowledgement, detected meter, unreadable configuration, missing identity
labels and saved-role mismatch.  These are contract tests against documented
reply shapes, not captures from commissioned hardware.

Two things remain to be confirmed against a real instrument: the exact text
of the `??D*` data-frame table, which is parsed tolerantly and falls back to
the documented default order, and the hold/release behaviour.  When
pressure/inverse mode is detected, the UI warns that it cannot verify the
physical valve position or plumbing; the operator acknowledges the
downstream installation once.  A future hardware change must add a properly rated
pressure-relief device or rupture disc protecting the lowest-rated pressurised
component; this remains an open hardware action and is not represented as
present by the software.
