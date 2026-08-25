# 0013 — Discover and add devices, rather than hand-writing rig-profile.toml

Status: Accepted
Written by: Claude, from context relayed directly by the project
maintainer. Designed and implemented by ChatGPT, in a session Claude was
not part of — Claude reviewed the resulting code afterward but did not
witness the reasoning behind its specific implementation choices.

## Context

Rig profiles were originally hand-written TOML. The maintainer's stated
reasoning (relayed directly, not inferred): the rig is still under
development and will keep gaining instruments, and hand-editing TOML for
every new device or sensor "isn't suitable in the future" as that number
grows — particularly relevant for the ESP32, which is expected to gain
support for new sensor types over time as real hardware (thermocouples,
pressure sensors beyond the current DHT11 stand-in) replaces generic
stand-ins.

## Decision

Device Setup gained the ability to scan real hardware (an ESP32's
reported capabilities, or an Alicat bus's addressed units) and build the
corresponding profile entries from what's actually found, rather than
requiring those entries to be written by hand.

Confirmed by Claude reading the resulting code: scanning itself never
changes any device state (read-only, matching the same principle already
established for the Diagnostics screen); adding a discovered ESP32
correctly reuses one shared connection across the controller and every
sensor found on it (see 0006); Alicat scanning infers directly from each
unit's own response whether it's a controller (settable) or a meter
(read-only), which maps onto the same read/write distinction established
in 0009/0010; and adding a device backs up the previous profile before
writing changes.

## Reasoning (as relayed by the maintainer, not independently verified
by Claude beyond reading the resulting code)

Manual TOML editing doesn't scale as device count grows and is easy to
get subtly wrong; discovery reuses the existing profile schema
(connections + device roles) rather than requiring a new configuration
concept, and the maintainer explicitly accepted that new sensor kinds
will require firmware updates to support ("that's just the cost of it")
rather than expecting the discovery mechanism itself to handle arbitrary
future hardware automatically.

## Alternatives considered

Not discussed in detail in the conversation this entry is drawn from —
this entry documents the decision and its stated motivation, not a
comparison of approaches that were weighed against each other.

## Gaps

This is the entry with the largest gap in this log. The actual design
work — the shape of the ESP32 "capabilities" query, why only a
`"dht11"` device kind is currently recognised, the Alicat scan/inference
logic's specifics, and any tradeoffs made while building the ~1,600 lines
of changed code in `ui/device_setup/model.py` and `window.py` — happened
in a ChatGPT session Claude has no visibility into. This entry should be
extended by whoever has that context (the other assistant, or the
maintainer) rather than treated as complete.
