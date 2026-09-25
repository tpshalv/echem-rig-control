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
configuration**.  It reads firmware, identity, loop/range, inverse-control
register and data-frame description, then compares them with the explicitly
commissioned device role, serial number and frame signature.  An outlet BPR
also requires an explicit record that its valve is downstream.  A mismatch
or an uncommissioned controller remains readable for diagnosis but blocks
setpoint and resume commands.  The application never sends a command that
selects mass-flow versus pressure control or enables inverse control.

Pressure setpoints require an explicit absolute or gauge unit.  They are
converted to absolute pressure before validation.  The ordinary software
ceiling is 2.5 bara.  High Pressure Mode is a deliberately non-prominent
setting; when enabled it exposes an editable higher ceiling, while the
controller's verified range and commissioned installed-device ceiling still
apply.  The UI and recorded context clearly state when this override is in
effect.  This is an operating guard only, not a pressure-protection system.

Polling records absolute pressure, derived gauge pressure using the saved
atmospheric reference, pressure setpoint, mass and volumetric flow,
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

The serial query and parser behaviour are covered by protocol-contract tests,
including the firmware-9 full-scale fallback.  Before live operation, the
commissioning screen must be used with each actual MC, and the returned
firmware, loop/range, inverse flag, identity, data frame and hold/release
behaviour must be confirmed against that unit's Alicat documentation and
installed plumbing.
