# 0014 — Three-tier power-supply current/voltage limit model

Status: Accepted, not yet implemented as of this entry (handed over for
implementation; see Gaps).
Written by: Claude, from direct discussion with the project maintainer,
who explicitly confirmed this model before it was finalised, then
corrected one internal contradiction in it (see revision note at the
bottom).

## Context

Testing on a fresh machine surfaced two related power-supply issues:
CC/CV mode switching wasn't updating displayed labels correctly, and the
manual-control default current limit was seeding from
`PowerSupplyLimits.maximum_current` (108 A - the Keithley 2260B's own
rated maximum) as if it were a sensible everyday value. It isn't: the
actual wiring connecting the supply to the cell has its own, lower,
physical current rating (45 A) - a real constraint the instrument itself
knows nothing about.

## Decision

Three separate tiers, each with a different mutability rule:

1. **Instrument's own absolute maximum** (108 A / 30 V) - already exists
   as `PowerSupplyLimits.maximum_current`/`maximum_voltage`, sourced from
   the rig profile. Unchanged.
2. **A wiring-imposed ceiling, defaulting to 45 A** (voltage ceiling
   unaffected - the wiring only constrains current, not voltage). This is
   a real, changeable setting, not a literal source-code constant - it
   must be possible to raise it later (e.g. once wired for a larger
   electrolyser with heavier cables) without editing code. What makes it
   safe is not immutability but *inaccessibility by default*: there is no
   control anywhere in the ordinary settings UI that can change this
   value. The only way to reach it at all is through tier 3 below - there
   is no field sitting next to the everyday settings that could be bumped
   or edited casually.
3. **A global "High current mode" setting**, off by default, that is the
   sole gateway to tier 2. Enabling it must show a warning (confirming
   the cables in use are actually rated for higher current), and only
   then does a control for the ceiling itself become visible, allowing it
   to be raised toward the instrument's true 108 A maximum. A further
   warning must appear again at the point any value over the 45 A default
   is actually entered/applied - a persistent reminder, not just a
   toggle-time one-off. Outside High current mode, the ceiling is
   enforced at 45 A with nothing in the UI able to change it.
4. **A separate, low "starting default"** (illustrative example ~20 A /
   10 V - not a fixed number, since the maintainer will tune the actual
   value) applied each time manual mode is initialised. Unlike tier 2,
   this **is** a normal, freely-editable global setting, stored in the
   settings TOML and changeable via the ordinary Settings screen - always
   clamped to whichever ceiling (45 A, or the raised one if High current
   mode is on) is currently active, and freely editable up to that
   ceiling from the Operation pane.

## Reasoning

The core insight is that "how much current the instrument can source"
and "how much current the installed wiring can safely carry" are
genuinely different constraints from different sources, and conflating
them (as the original 108 A default did) silently drops the more
restrictive, more relevant one.

Tier 2's safety property comes from where it lives, not from being
unchangeable: gating it entirely behind tier 3's deliberate toggle-plus-
warning means it can never be bumped by accident while adjusting
something else, while still remaining reachable on purpose when the
maintainer genuinely needs to raise it (a real future case - a larger
electrolyser). Tier 4 is explicitly the opposite - a genuine day-to-day
convenience value with no safety meaning beyond "a low, sane place to
start from," so it belongs in ordinary, always-accessible settings with
no gating at all.

## Alternatives considered

- Making the default current limit a per-device rig-profile field
  (Claude's initial suggestion, based on how other per-device settings in
  this project work) - superseded once the maintainer clarified this
  should be a *global* setting instead, since there's realistically one
  power supply on this rig, not several needing independent values.
- A single configurable ceiling with no separate "wiring vs. instrument"
  distinction - rejected once the maintainer pointed out the wiring
  limit is a real, separate, more restrictive constraint that the
  instrument's own rating doesn't capture at all.
- Tier 2 as a literal hardcoded Python constant, inaccessible from the
  UI entirely - this was the first version of this entry. The maintainer
  caught the contradiction directly: a value described as hardcoded
  cannot simultaneously be something a "High current mode" toggle raises.
  Revised to tier 2 being a real, storable setting that is simply
  reachable only through tier 3's gate, rather than something requiring
  a code edit to ever change.

## Gaps

Not yet implemented as of this entry - handed over for implementation
with this exact model specified. If the final implementation deviates
from any of the four points above, that deviation and its reason should
be recorded as a new entry rather than edited into this one.
