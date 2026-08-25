# 0009 — Manual control grouped by physical system, with structural read/write

Status: Accepted (superseded in part — see note below)
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

Early manual-control mockups showed one box per adjustable device in a
flat list. The maintainer pointed out this only had two example
devices (a power supply, one MFC) and asked how it should look with many
more — multiple MFCs, multiple heaters, etc. — and specifically how
read-only sensors and adjustable controls should relate to each other.

## Decision

Group controls by physical system (Electrical / Thermal / Gas flow — the
categories the maintainer actually uses to think about the rig), not by
driver or device type. Mark adjustable channels structurally (a small
visible marker) rather than relying on implicit convention, and model
read/write per the device's real physical semantics rather than
uniformly: e.g. a power supply's voltage is one row (the reading and the
setpoint are the same underlying number), but current draw and current
*limit* are two separate rows, since one isn't directly settable and the
other is.

Note: this entry's original layout (tabs of grouped device boxes) was
itself later revised further in the same conversation — the tabs stayed,
but the boxes-per-device shape inside them was replaced by a unified
per-tab table with click-to-edit rows (see 0010's context for that later
merge). Recorded here anyway because the *system grouping* and *read/
write-by-actual-semantics* reasoning carried forward unchanged.

## Reasoning

Grouping by system matches the maintainer's own mental model rather than
the software's internal capability/driver taxonomy — asking "where would
a new PID heater's control go" should have an obvious answer (Thermal)
without needing to know anything about how the code is organised
underneath. Modelling current vs. current-limit as two rows rather than
one was a deliberate correctness call, not simplification-for-its-own-
sake: treating them as the same thing would have been factually wrong
about what a constant-voltage supply actually lets you set.

## Alternatives considered

- Grouping by capability/driver type instead of physical system —
  rejected as matching the code's internal organisation rather than how
  the maintainer actually thinks about the rig.
- Treating every adjustable field the same way (always reading == always
  setpoint) — rejected once the current-vs-current-limit distinction was
  raised; would have misrepresented what's actually controllable.

## Gaps

None significant for the reasoning; see 0010 (once written) for how the
box-per-device presentation was superseded.
