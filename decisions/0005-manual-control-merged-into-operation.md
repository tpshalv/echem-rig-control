# 0005 — Manual control merged into Operation, not kept as a separate window

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

Manual Control was originally its own standalone window/screen, separate
from Operation (live monitoring + recording). Following 0003/0004, the
question came up: should Manual Control be one more screen a user can
optionally run alongside Operation, or something else?

## Decision

Manual setpoint control became a panel inside the Operation screen
itself, wired through `RigControlService` the same way the standalone
window's commands were. The standalone Manual Control window was to be
retired once the panel was proven working, rather than keeping both
indefinitely.

## Reasoning

Manual Control and Operation are the one pairing that plausibly needs to
work *together* often — nudging a setpoint while watching/recording a
live run. Rather than generalising screen-sharing rules to cover that
specific case, folding manual control directly into the one screen that's
already watching the live run sidesteps the multi-window question
entirely for this pairing, while Device Setup and Diagnostics (which
don't need to run *during* an active adjustment in the same way) were
left as their own screens.

Routing the new panel's commands through `RigControlService` (rather than
some simpler direct path) was deliberate: it means a future recipe runner
can lock out manual adjustments during an active recipe the same way it
already would for the old standalone window, instead of that safety
property needing to be rebuilt for the new panel.

## Alternatives considered

- Keeping Manual Control as a fully separate screen and instead building
  general support for it to run alongside Operation — rejected as a
  bigger, more general change than the actual need (this one specific
  pairing) called for.

## Gaps

Whether/when the standalone Manual Control window was actually removed,
and the specifics of how the panel was composed into
`OperationViewModel`/`ManualControlPanel`, were implemented by ChatGPT
and not directly observed by Claude beyond reading the resulting
`ui/operation/manual_control_panel.py`.
