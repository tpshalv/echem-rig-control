# 0017 — Recipe power-supply settings state their CC/CV mode

Status: Accepted and implemented.
Written by: Claude, from a request by the project maintainer.

## Context

Recipes offered the power supply as a plain `voltage` / `current_limit` pair.
Its builder labelled current as "Current limit (A; not current
regulation)". That is wrong for the 2280S: the supply regulates current
through that same register whenever the load pulls it into CC. The label
also gave no way to write a constant-current experiment whose meaning was
clear. The manual pane already has a CC/CV selector (see 0014), but a
recipe must mean the same thing whatever the manual pane happens to show.

A recipe may also need both modes in turn, for example chronoamperometry
followed by chronopotentiometry.

## Decision

- The setting name states the mode. CC uses `current_setpoint` +
  `voltage_limit`. CV uses `voltage` + `current_limit`. There is no
  separate mode field, and the manual pane's mode is never read or changed.
- A recipe may switch a supply between modes. The mode at any point is the
  mode of the last setpoint or limit written to that supply.
- Validation (`power_supply_problems`) follows execution order, running each
  loop twice so the state one pass leaves behind is checked too:
  - The output may only turn on once both settings of the current mode have
    been written since the mode last changed.
  - Switching mode while that supply's output is on is refused. Otherwise
    the register left over from the old mode would briefly act as the new
    mode's limit.
- Both modes reuse the existing `SetPowerSupplyVoltage` /
  `SetPowerSupplyCurrentLimit` commands and the same capability and
  instrument-maximum checks. No new instrument command path exists.
- Each mode change is recorded as a `power_supply_mode` event. The actual
  CC/CV state remains the polled `regulation_mode` channel. The two are
  never merged, because the load can put the supply into the other mode
  than intended.

## Alternatives considered

- **One declared mode per supply per recipe.** This was the first
  implementation. Rejected because it cannot express CA followed by CP, and
  it needed a mode dropdown that confused recipes which never touch the
  supply.
- **New CV names (`voltage_setpoint`) with `voltage` kept as an alias.**
  Rejected: an alias adds a second spelling for the same thing, and CV
  semantics already match the existing names.

## Gaps

Recipe validation still checks only the instrument maximum
(`PowerSupplyLimits`). It does not check the wiring current ceiling from
0014 (45 A unless High current mode is on). That gap existed before this
change and is unchanged by it.
