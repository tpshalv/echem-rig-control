# 0003 — One shared ApplicationSession, owned by a Home screen

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

Each screen (Device Setup, Diagnostics, Manual Control, Operation) was
originally its own separately-run script, each building its own
`DeviceManager` and opening its own connections to the rig independently.
Confirmed directly in the code: this meant two screens could never safely
be open at once against real hardware, since they'd each try to open the
same serial ports/sockets separately.

## Decision

A single home/landing screen becomes the one entry point to the app. It
loads/selects a rig profile once and constructs one shared
`ApplicationSession` (one `DeviceManager`, one `RigControlService`, one
`PollingService`, etc.), then hands that same session down to whichever
feature screen is opened, rather than each screen building its own.

## Reasoning

Chose this specifically so that "can two screens run at once" becomes a
question about a screen-opening policy (see 0004), not a question about
whether the underlying connections can be shared at all — the hard part
(one real connection per physical device, safely shared) gets solved once
here, rather than needing to be solved separately for every future pair
of screens that might want to run together.

## Alternatives considered

- Building full simultaneous-multi-screen support immediately — rejected
  as more than was needed at the time. The chosen middle ground: keep
  screens opening one at a time for now, but shape the ownership model
  (session built once, handed down) so that relaxing that restriction
  later is a small, targeted change rather than a rewrite. This was an
  explicit, deliberate tradeoff, not an oversight — see 0004 for how it
  played out once a real need for two screens together actually
  appeared.

## Gaps

The exact mechanics of how `ApplicationSession` is constructed and
threaded into each screen (`ui/home/window.py`, `ui/home/model.py`,
`application_session.py`) were implemented by ChatGPT and confirmed by
Claude reading the resulting code, but the session that actually designed
those specific interfaces was not observed by Claude in full.
