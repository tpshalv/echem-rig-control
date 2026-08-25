# 0010 — One click-to-edit table per system, replacing separate read/write areas

Status: Accepted (supersedes the box-per-device layout from 0009)
Written by: Claude, from direct discussion with the project maintainer.
Not implemented as of this entry — this was a mockup-stage design
decision; see Gaps.

## Context

Even after grouping controls by system (0009), the maintainer asked for
further design work on "controlling lots of different things" — pointing
out that a box per adjustable device would still not scale well as MFC
count grew, and separately observed that Operation still had two
disconnected areas: a global read-only measurements table, and a
separately-tabbed write-only control area below it.

Several candidate interaction patterns were sketched and discussed:
click-to-edit values in place instead of always-visible input boxes;
a compact table (one row per device) instead of a box per device for
things that share the same one field, e.g. several MFCs' flow setpoints;
a "select a device, one shared adjust panel" pattern; and range-aware
sliders. The maintainer asked to combine specific ones rather than pick a
single winner.

## Decision

System tabs (from 0009) now govern a single unified table per tab, not
two separate sections. Read-only and writable channels appear in the same
list. A writable channel's value is click-to-edit in place (plain text
until clicked, then an input plus Apply) rather than a permanently-visible
input box. An "Overview" tab (Claude's suggestion, flagged explicitly as
an addition rather than something the maintainer asked for, and accepted)
shows every channel unfiltered, preserving the "see everything at once"
view that existed before the split into per-system tabs.

## Reasoning

This specifically combines three of the sketched patterns rather than
picking one: system-level tabs (the "pick an area, then work within it"
idea, applied at the system level rather than per-individual-device),
one compact table per tab (rather than a box per device — the same
reasoning as wanting a table for repeated MFCs, generalised to every
tab), and click-to-edit (so a device with several fields, like a power
supply, doesn't need several permanently-visible input boxes at once).
The "select one device, see only its controls" pattern was discussed but
not chosen for the base design — it was judged as a bigger interaction
change (only ever seeing one device's controls at a time) that trades
away comparing several devices at a glance, which didn't seem justified
as the default.

## Alternatives considered

- The "pick a device from a list, one shared adjust panel fills in"
  pattern — considered, explicitly not chosen as the default; noted as
  better suited to workflows that mostly adjust one thing at a time
  rather than comparing several.
- A slider-based control for range-aware setpoints — considered as
  additive on top of whichever base pattern was chosen, not a
  replacement for it; not built into the base design.

## Gaps

This entry describes a design decision reached through mockups; as of
this entry it had not yet been implemented in the real Tkinter code (a
handover prompt for it was drafted but implementation status is
unconfirmed). If implemented, a later entry or a note appended by
whoever implements it should record any real-Tkinter constraints that
changed the design (click-to-edit is not a trivial pattern in ttk, and
this was flagged explicitly as needing the implementer's own judgement).
