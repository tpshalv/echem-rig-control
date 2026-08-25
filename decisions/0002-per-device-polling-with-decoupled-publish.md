# 0002 — Per-device polling rates, decoupled from a shared publish tick

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

The original background-polling design (`PollingService`) read every
connected device on one single shared interval. This broke down for two
reasons, found by reading the code directly rather than assumed:

- A slow device in the mix (a future GC producing one result every ~90s
  was the concrete example) would stall the entire read cycle, because
  all devices were read together and awaited as one batch before
  anything was published to the screen or the recorder.
- There was no way to give a fast instrument (e.g. a Keithley capable of
  ~100ms readings) a faster rate without also forcing slow, simple
  sensors to be polled unnecessarily often.

## Decision

Two independent clocks instead of one: each device gets its own read
interval, and a single separate "publish" tick (one steady, configurable
rate) is what actually reaches the screen and the recorder, drawing from
a cache of each device's latest known reading rather than waiting for
every device to answer on every tick.

## Reasoning

- A slow device's own schedule can no longer block anyone else's reads,
  because reads are independent per device rather than a bundled batch.
- The publish side stays simple — one rate, easy to reason about — while
  the complexity (independent per-device timing) lives only where it's
  actually needed.
- This also directly answered a concrete, smaller version of the same
  problem the maintainer noticed independently: with a shared rate, a
  Keithley and a temperature sensor would both be limited to whichever
  rate was configured, with no way to give one device a faster read
  without forcing it on the other.

## Alternatives considered

- Giving every device its own separate `PollingService` instance/thread —
  rejected as unnecessarily heavyweight; a single scheduler that tracks a
  per-device "next due" time (the same pattern already used by
  `ExperimentRecorder`'s own sample-interval throttle) does the same job
  with less machinery.
- Making the internal scheduling tick a fixed interval — rejected once
  it was pointed out that the tick must run at least as often as the
  *fastest* configured device, or that device can never actually be read
  at its configured rate; the tick is derived from configuration instead
  of hardcoded.

## Gaps

None — this decision and its implementation were both discussed directly
in the conversation this entry is drawn from.
