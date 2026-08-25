# 0004 — Device Setup stays exclusive; Operation and Diagnostics may coexist

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT, in a session Claude was not part of.

## Context

Following on from 0003: once one shared session exists, the question
became which screens, if any, should be allowed open at the same time.
The maintainer wanted this specifically to watch a live diagnostics/
memory chart while Operation was running an actual overnight recording —
a real, current need, not a hypothetical one.

## Decision

Device Setup requires everything else to be closed, and blocks everything
else while it's open. Operation and Diagnostics may be open together.

## Reasoning

This isn't a uniform rule ("some screens can be concurrent") — it's
specifically about what each screen actually does to the shared session.
Opening Device Setup tears the whole `ApplicationSession` down and
rebuilds it afterward (confirmed directly in `suspend_for_device_setup`/
`resume_after_device_setup`), because editing the rig profile can change
the device list itself — there's no way to safely have another screen
holding references into a `DeviceManager` that might get replaced out
from under it. Operation and Diagnostics never replace the session; they
only read from and issue commands into the one that already exists, so
sharing it between them is safe.

## Alternatives considered

- Uniform "any two screens can be open together" — rejected once it was
  established that Device Setup's rebuild-the-session behaviour makes it
  structurally different from the other two, not just a matter of
  policy.
- Building general N-screens-at-once support — rejected as more than the
  actual need called for; the concrete need was specifically Operation +
  Diagnostics together, so that's what got built.

## Gaps

None significant — this was discussed directly and confirmed against the
real code (`ui/home/model.py`'s feature-gating logic) in the conversation
this entry is drawn from.
