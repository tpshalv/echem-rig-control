# 0011 — Retry-with-backoff for instrument reads, deliberately not for writes

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

Reviewing an older, unrelated single-instrument tool the maintainer had
previously used (a separate PyQt5 Keithley control app, not part of this
project) turned up a retry-with-backoff wrapper around instrument
measurement calls that this project's drivers didn't obviously have.

## Decision

Add retry-with-backoff specifically to READ/measurement operations
(Alicat driver, ESP32 client). Explicitly do not apply the same wrapper
to WRITE/command operations (setting a voltage, opening a valve) without
separate, more careful consideration.

## Reasoning

A failed read is unambiguous to retry — worst case you ask again and get
a fresh answer. A failed *write* is ambiguous: the command may have
applied before the failure occurred, or not at all, and blindly retrying
it risks double-applying a command or masking a real problem rather than
surfacing it. These are not the same kind of operation and shouldn't
share a blanket policy.

## Alternatives considered

- Applying the same retry wrapper uniformly to all instrument calls —
  rejected specifically because of the write-ambiguity problem above.

## Gaps

Whether write-side retry/confirmation was ever revisited as its own,
separate decision is not known to Claude — it was explicitly deferred,
not resolved, in the conversation this entry is drawn from.
