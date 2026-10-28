# 0021 — Generated experiment IDs, and an Electrolyser ID on each test

Status: Accepted and implemented.
Written by: Claude, from a design conversation with the project maintainer.
Claude took part in all of the reasoning recorded here and implemented it.

## Context

The maintainer wanted to know which electrolyser each test ran on. An
electrolyser here means a full build, with the MEA prepared and sandwiched,
not just the metal body. Most builds are tested once, occasionally a couple
of times. A lab-wide Excel database form is also planned, for people who
don't use this software.

At the same time, the old naming duplicated information. Recording folders
were `<UTC timestamp>_<experiment ID>`, and the maintainer usually typed a
date and time into the experiment ID as well. All the exports were also
called `experiment.xlsx` and `measurements-wide.csv`, so Excel would not
open two runs side by side.

## Decision

- **Electrolyser ID.** A single optional field on each test, stored as its
  own `electrolyser_id` key in `metadata.json`. The Excel Overview shows it,
  or "not recorded" when it was left blank. A run with no cell is described
  in the notes. There is no separate build register yet. The build details
  (MEA, loadings and so on) can live in the lab Excel database, joined on the
  same "Electrolyser ID" column.
- **Generated experiment ID:** `INITIALS_YYYY-MM-DD_HHhMM[_description]`, for
  example `TPS_2026-10-22_14h09_humidity-tests`.
  - The time is local and to the minute. The exact start time stays in the
    metadata.
  - Underscores only separate the parts, so the ID always splits into three
    or four fields.
  - Inside a part, anything other than letters and digits becomes a hyphen.
    Spaces become hyphens, and full stops are removed, because they read as
    file extensions.
  - The description is optional and capped at 40 characters.
  - The `h` sets the time apart from the date, which reads better than
    `T14-09`. Double underscores were rejected because `_` and `__` are hard
    to tell apart by eye.
- **Folder and export names.** The recording folder is named after the ID
  alone. The exports are `<ID>.csv` and `<ID>.xlsx`.
- **Recording panel.**
  - The panel asks for Operator initials, Electrolyser ID and Description,
    with a read-only, live preview of the ID. No field is called "Experiment
    ID", so nobody types the date into it as well.
  - The operator is remembered between sessions in
    `settings/remembered.json`, a convenience file whose loss only empties
    the field.
  - The Electrolyser ID is deliberately not remembered, because builds are
    mostly single-use and a carried-over ID would mislabel the next test.
- **Clashes.** Two starts within the same minute give the same name. The app
  asks before replacing the earlier recording, and shows its start time and
  size. It never deletes a folder that isn't a recording, meaning one without
  a `metadata.json`. The clock change was judged too unlikely to design
  around.
- **Recipes** use the same naming and take the Operation panel's operator,
  electrolyser, description and notes. The description falls back to the
  recipe name.

## Gaps

- Existing recordings keep their old folder names. Re-exporting one writes
  files named after that folder next to the old `experiment.xlsx`.
- There is no list of recently used Electrolyser IDs yet.
