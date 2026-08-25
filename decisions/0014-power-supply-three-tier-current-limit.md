# 0014 — Three-tier power-supply current/voltage limit model

Status: Accepted and implemented.
Written by: Claude, from direct discussion with the project maintainer,
who explicitly confirmed this model before it was finalised, then
corrected two mistakes in it after implementation: one internal
contradiction caught before coding began, and one setpoint/limit mapping
error caught during live testing on the simulated rig (see revision notes
under Alternatives considered).

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
   value) applied to whichever quantity is acting as the *protective
   compliance limit* in the mode manual control is currently in - not to
   the setpoint. A power supply's current and voltage registers swap
   roles depending on mode: in constant current mode, current is the
   setpoint (left at its remembered value, untouched by this tier) and
   voltage is the compliance limit, defaulting to 10 V; in constant
   voltage mode, voltage is the setpoint (likewise untouched) and current
   is the compliance limit, defaulting to 20 A. Unlike tier 2, this **is**
   a normal, freely-editable global setting, stored in the settings TOML
   and changeable via the ordinary Settings screen - applied each time
   manual mode is initialised or switched into, and (for the current
   value specifically) always clamped to whichever ceiling (45 A, or the
   raised one if High current mode is on) is currently active.

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
electrolyser). Tier 4 is different in kind, not just in size: it protects
whichever quantity manual control *isn't* actively commanding. Left
alone, that quantity would either sit at zero (useless - the output could
never actually reach the setpoint) or jump straight to the instrument's
true maximum (unsafe - exactly the original 108 A bug, just relocated).
A low compliance-limit default gives a sane starting bound without
requiring the maintainer to think about it before every session, while
staying freely editable since - unlike tier 2 - going higher here isn't
inherently hazardous enough to need a deliberate gate.

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
- Tier 4 applied to the *setpoint* (current in constant current mode,
  voltage in constant voltage mode) - this was the first implementation.
  The maintainer caught it live on the simulated rig: switching to
  constant voltage mode showed a 45 A current *limit* as expected, but
  constant current mode showed no voltage value at all, and neither
  showed the configured 20 A/10 V. The maintainer's correction: a power
  supply's current and voltage registers swap roles by mode - whichever
  one is the setpoint is left alone (remembered/zero), and tier 4 belongs
  on the *other* one, the compliance limit, in whichever mode is active.
  Revised throughout `initialize_manual_power_supply_defaults()` and
  `set_power_supply_operating_mode()` to apply tier 4 to the limit, not
  the setpoint.

## Gaps

Implemented in `ui/manual_control/model.py` (`ManualControlViewModel`,
`PowerSupplyManualSafety`), `ui/manual_control/types.py`,
`app_settings.py`, and `ui/home/settings_window.py`. Verified against the
simulated rig (labels, values, and the High current mode gate); not yet
verified against real Keithley hardware.
