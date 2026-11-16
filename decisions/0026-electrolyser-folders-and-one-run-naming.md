# 0026 — Electrolyser folders, and one naming for manual and recipe runs

Status: Accepted and implemented.
Written by: Claude, from a discussion with the project maintainer, who chose
the folder layout, the electrolyser name format and three-digit numbers, and
asked that no build detail be compulsory.

## Context

Manual and recipe runs were named differently. A recipe run with no
description was named after the recipe (`…_Untitled-recipe`), which
Operation's ID preview did not show. The recipe builder kept its own copy of
the output folder. It named the run from one clock reading and started it
from another. Its recordings also lacked the rig profile, channel labels and
calibrations that manual runs record.

Runs were saved flat in the output folder. Several people build
electrolysers and mostly test their own. The maintainer wanted to see every
electrolyser at a glance, the runs on each, and roughly when each was built.

## Decision

- **One naming for every run.** Operation's `plan_recording` names a run
  (`INITIALS_YYYY-MM-DD_HHhMM[_description]`, unchanged), files it and builds
  its metadata.
  - The recipe builder uses `plan_recording` too, and the runner only adds
    `experiment_type="recipe"`, `recipe_name` and the recipe snapshot.
  - The description no longer falls back to the recipe name.
  - Manual runs now record `experiment_type="manual"`.
  - The recipe builder shows the same preview and uses Operation's output
    folder; it has no folder of its own.
  - It checks the initials and output folder before its confirmation dialog.
    It names the run after that dialog, so the time in the ID is the start
    time.
  - A same-named recording is deleted only after the runner's own checks
    pass.
- **Layout:**
  `<output>/<electrolyser>/<run>/`, with `No-electrolyser/` for runs on no
  cell. The app files every run; nobody chooses where a run goes.
- **Electrolyser names are `BUILDER-E###-YYYY-MM`**, for example
  `TPS-E057-2026-11`.
  - The builder's initials come first. The number is the builder's own,
    padded to three digits so Explorer sorts it. The month is the build
    month.
  - Names use hyphens only, so they can never look like a run, whose parts
    are separated by underscores.
- **The next number is worked out, not remembered.** It is one more than the
  builder's highest existing number, counting retired and abandoned folders,
  so a number is never reused. The name is only a suggestion and can be
  edited, so older cells (for example `B142`) can be added under their own
  names.
- **`electrolyser.json` marks an electrolyser folder** and holds its build
  details.
  - No field is compulsory, and the form can add custom fields.
  - Fields the app does not know are kept as they are, so the record can
    become stricter later without losing older data.
  - A new build is filled in from the builder's latest build, except Notes.
  - Edits are logged in `history` with who made them, what changed and an
    optional note.
  - A retired cell leaves the dropdown, but its number stays taken.
- **The Electrolyser field is a dropdown** of the output folder's
  electrolysers, so a typo cannot create a second folder. It is locked while
  recording.
- **Each run keeps a copy of the build record as it was** at start, in
  `electrolyser_json`. Later edits to the cell do not change old runs. The
  Excel Overview lists the filled-in details.
- **Choosing an electrolyser fills in its active area** if the record has
  one, and otherwise clears the area. This refines 0025 point 1: the area
  still never carries over from a different build, but it now comes from the
  build itself.

## Alternatives considered

- **Initials-free sequential names (`EL-003`).** Rejected, because several
  builders would have to coordinate one count.
- **The build date in full, or details in the name.** Rejected. Details go
  stale when a part is swapped, and a full date looks too much like a run.
  The month is enough to see age; the exact date is in the record.
- **The electrolyser in the run ID.** Rejected. The parent folder and the
  metadata already give it, and it would make every ID longer.
- **Letters for rebuilds (`E003b`).** Rejected. A rebuild that changes
  performance gets a new number, so the date in the name stays true. Small
  fixes stay under the same name and are logged in the history.

## Gaps

- Numbering is only reliable if everyone saves to one shared output folder.
  Separate local folders on two PCs can give the same builder the same
  number twice.
- Existing flat recordings are not moved. Move them by hand into
  `No-electrolyser/` or an electrolyser folder; nothing depends on their
  location.
- No build detail is checked: area, loadings and torque are free text. The
  area must still parse as a positive number when it is filled into
  Operation, or Operation shows its usual error.
