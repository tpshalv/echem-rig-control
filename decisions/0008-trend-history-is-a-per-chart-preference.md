# 0008 — Trend history is a per-chart preference, not a global Operation setting

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

The Operation screen originally had a single "Trend history" control in
its toolbar, applying one retained-points limit to every channel's trend
chart uniformly. During a broader redesign pass, the maintainer flagged
they weren't sure this belonged there at all.

## Decision

Removed from Operation's toolbar entirely. A default value lives in the
app-wide Settings (on the Home screen); each individual `TrendWindow` can
override that default for itself.

## Reasoning

How much history is useful to see is a property of *looking at one
specific chart right now*, not a property of the whole screen — you might
reasonably want more history on one important reading than another at any
given moment. A single global dial forces the same choice on every chart
regardless. Splitting it into "sensible default, per-chart override"
mirrors the same reasoning already applied to per-device poll rates
(0002): global default for the common case, override where the specific
need actually shows up.

## Alternatives considered

- Leaving it as a single global control — rejected once the "why would
  every chart need the same amount of history" question was raised and
  had no good answer.

## Gaps

None significant for the decision itself. Whether the per-chart override
control was actually built into `TrendWindow`, versus only the global
default landing in Settings, was not confirmed against the resulting code
in the conversation this entry is drawn from.
