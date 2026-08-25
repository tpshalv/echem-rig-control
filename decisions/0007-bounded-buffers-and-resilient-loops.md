# 0007 — Bounded buffers plus a loop that can't be stopped by one bad tick

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer,
following an actual overnight crash the maintainer experienced.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

An overnight unattended run crashed; Windows reported climbing memory
pressure shortly before. Reading the code directly (not guessing)
found two concrete, confirmed causes: `PollingService.results`, the queue
handing polling batches to the screen, had no size limit; and the
screen's refresh loop (`_poll_ui_queue`) called into code that could
throw, with nothing catching that exception — so one bad tick could
silently stop the loop from ever rescheduling itself, while the
background polling thread kept producing into the now-unwatched,
unbounded queue for the rest of the night.

## Decision

Two separate, complementary fixes, not one or the other: bound the
queue (and the similarly-unbounded `OperationViewModel._events` list)
with an explicit policy for what happens when full; and make the
screen-refresh loop unable to be permanently stopped by a single bad
tick — catch, log via the existing technical event sink, and keep
rescheduling, every time.

## Reasoning

These solve different failure shapes. The bound is a backstop: even a
cause nobody anticipated can't grow into an unbounded crash. The
resilient loop is the actual fix for what happened this time: the real
problem wasn't that data was ever going to accumulate without limit in
principle, it was that the *consumer* could quietly die while the
*producer* kept running, with nothing noticing. A loop that survives one
bad tick doesn't have that failure mode at all — there's no gap where
the consumer is gone and nothing has noticed yet, because it never
actually stops.

Explicitly rejected: a "notice the consumer died and restart it" design.
That's strictly worse than "the consumer can't die from one bad
tick" for something meant to run every ~100ms — a restart-after-death
design needs a separate watcher and has a real window of lost service
before it recovers; a consumer that just doesn't stop needs neither.

## Alternatives considered

- Fixing only the unbounded queue and treating that as sufficient —
  rejected; it would have stopped the *crash* but not the underlying
  *data loss* (a permanently-stalled consumer means readings and
  recording both silently stop, queue-bound or not).

## Gaps

The exact bounded-queue policy actually implemented (what happens on
overflow — drop oldest, log an event, or something else) was left to
ChatGPT's judgement in the handover and was not confirmed against the
resulting code as part of the conversation this entry is drawn from.
