# 0025 — Recipe flows in SCCM, and current density with an active area

Status: Accepted and implemented.
Written by: Claude, from a request by the project maintainer, who asked how
the area should be entered and not forgotten. Claude chose the answer below;
the maintainer has not yet reviewed it.

## Context

The maintainer thinks in current density and sccm. Working out amperes by
hand for a list of densities is slow and easy to get wrong. The Operation
table could already show flows in SCCM, but the recipe builder could only
take a flow in each controller's own unit, shown only as "native unit".

Unlike a display unit, current density depends on a second number, the cell's
active area. A wrong area scales every current without any visible error.
That makes the area, rather than the arithmetic, the main risk here.

## Decision

- **The setting name states the unit.**
  - Recipes gain `flow_sccm` and `current_density_setpoint`, next to `flow`
    and `current_setpoint`, as `pump_flow` sits next to `pump_speed`.
  - Existing recipes keep their meaning.
  - One Set step cannot hold both settings for the same target.
- **A recipe stores the density, not amperes.** It converts when the run
  starts, so a recipe can be reused on a cell of a different area.
- **The supply is still commanded in amperes,** and raw current is still
  recorded. Density is an added channel, `current_density` in `mA/cm2`. Each
  recipe write records the amperes and area it used.
- **The area is one field, under Electrolyser ID in the recording panel.** It
  is a property of the build, so it sits with the build's ID. Recipes, manual
  control and the recorded channel all share that one value. It is stored in
  the run's metadata and shown in the Excel Overview.
- **Forgetting it is handled at four points:**
  1. There is no default and it is not remembered between sessions. A
     carried-over area would mislabel the next cell, as 0021 found for the
     Electrolyser ID. Changing the Electrolyser ID clears it for the same
     reason.
  2. With no area, a density cannot be sent at all. A density recipe is
     refused before anything moves, and the manual density row is not
     editable. Amperes still work.
  3. Starting a run with no area asks first if the run looks like a real
     cell: an Electrolyser ID is entered, a supply output is on, or a recipe
     turns one on. Runs with no cell, such as humidity tests, are not asked.
  4. The start prompt for a density recipe states the result, for example
     "Active area 5 cm²: 100 to 400 mA/cm² is 0.5 to 2 A". A wrong area shows
     up here as a wrong current.
- **The area is fixed while recording,** so a run has one area and the
  recorded density always matches it.
- **Limits are checked after conversion.** Device maximums apply to the
  amperes or the controller's own flow unit, not to the number typed.

## Alternatives considered

- **Converting to amperes in the builder.** Rejected. The saved recipe would
  be tied to one area, and reusing it on another cell would silently give the
  wrong density.
- **Making density a choice in the existing mA/A dropdown.** Rejected. That
  dropdown keeps the quantity and changes only the numbers shown, which a
  density cannot do without an area. It also has no sensible result while no
  area is entered.
- **A `flow` setting whose meaning depends on its saved unit.** Rejected. A
  recipe with no saved unit, displayed under a chosen unit, could be read as
  SCCM while it runs as SLPM: a factor of 1000.
- **Remembering the last area, or a per-rig default.** Rejected, as in point
  1. Retyping one number per build is a small cost.
- **Keeping the area in the recipe.** Rejected. It describes the cell, not the
  procedure, and it would go stale when a recipe is reused.

## Gaps

- Recipes are checked against the instrument's maximum, not the wiring ceiling
  from 0014, which only manual control enforces. This is not new, but a wrong
  area makes it easier to reach. The start prompt's amperes are the only guard
  until that is decided.
- Live graphs and exports keep each MFC's own unit, as `display_units`
  specifies. Only the tables and recipes follow the SCCM choice.
- Current density has no CV form. A CV current limit stays in amperes in
  recipes.
