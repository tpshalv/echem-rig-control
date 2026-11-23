# 0028 — Everyday supply limits, checked on every command

Status: Accepted and implemented.
Written by: Claude, from discussion with the project maintainer, who agreed
the limits and their defaults before they were built.

## Context

Decision 0014 set up three tiers for power-supply current: the instrument's
own maximum, a 45 A cable ceiling that only High current mode can raise, and
low starting values for the compliance limit. Three problems remained:

- The starting values defaulted to 0, so every time a supply entered a mode
  its compliance limit was 0 until the operator raised it, and runs errored.
- Nothing stopped a typing slip well inside the cable ceiling. On a 5 cm²
  cell, 25 typed into an A box meaning mA is 25 A: far beyond what the cell
  should see, but under 45 A. In constant current the only hardware
  protection is the voltage compliance limit, and at 6 V a small cell can
  still draw far more than intended.
- The cable ceiling was only enforced by manual control. A recipe could ask
  for anything up to the instrument maximum.

Most work is in current density, typically up to 300 mA/cm² on 5 cm² cells,
so 1.5 A covers normal use.

## Decision

- **Three everyday limits in Settings › Power supply**, changed rarely:
  - Everyday maximum current: 1.5 A.
  - Everyday maximum voltage: 6 V.
  - Maximum current density: 300 mA/cm², applied as a current once an
    active area is entered.
- **A value must pass every limit that applies**: instrument maximum,
  cable ceiling, everyday maximum, and density × area. Both the current and
  the density limit apply, so a mistyped area can only tighten protection.
  Limits are on the supply's registers, whatever a setting is called in the
  current mode.
- **Checked in the control service**, which every command passes through:
  manual control, recipes, overrides and revisions alike. A refusal is
  audited as blocked and says which limit was hit and by how much, including
  the density when an area is entered. Recipes are also checked at Validate,
  Start and Apply to run, so a run cannot start that would break a limit
  later. Safety-system commands are exempt: they only switch off.
- **The everyday values are the default compliance limits.** A supply
  entering constant current gets the everyday voltage as its voltage limit;
  entering constant voltage it gets the current ceiling as its current limit.
  The recipe runner writes this as the supply enters a mode (logged as from
  Settings), so recipes need not set it, and the power-supply sequence check
  no longer asks for it. A run may set a lower limit; never a higher one,
  because the everyday value is also the ceiling. To go higher, change the
  setting.
- **The settings keep their old keys** (`power_supply_default_current_amps`,
  `power_supply_default_voltage_volts`) so saved files still load; a saved 0
  meant "never set" and loads as the new default.
- Settings only change with Operation closed, so limits are fixed for a
  session and cannot change during a run.

## Alternatives considered

- **A prompt when a setpoint jumps tenfold.** Most slips are already caught
  by a real limit, and a limit tied to the cell is the actual protection; a
  prompt would be clicked through.
- **A density limit alone.** It depends on the area being right; the plain
  current limit keeps protection when it is not.
- **Allowing a run to raise its compliance limit above the everyday value.**
  That reopens the slip this decision closes.
