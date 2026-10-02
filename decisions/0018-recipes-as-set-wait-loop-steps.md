# 0018 — Recipes are nested Set, Wait and Loop steps, edited as Loop … Next rows

Status: Accepted and implemented.
Written by: Claude, from a request by the project maintainer.

## Context

The first recipe format split a recipe into five flat lists: fixed
settings, sweeps, startup actions, a timed sequence, and finishing actions.
Sweeps nested only by their order in a list, and timed steps could only run
inside the innermost sweep. A recipe such as "at each current, settle, then
for each gas flow, measure" could not be written. The builder used one form
for all five lists, so the same inputs meant different things depending on
which button was pressed. The maintainer found it confusing and expected
other users to as well.

## Decision

- A recipe is one ordered list of steps (format version 2):
  - **Set**: write one or more settings, top to bottom with no wait
    between them (e.g. a supply's setpoint, its limit, then output on).
    Order inside the step counts for the power-supply checks in 0017. The
    editor groups a Set step by device. Each device card lists its settings
    in a fixed order (a supply's setpoint, then limit, then output) and you
    tick the ones to change, so the safe order inside a device is the only
    order you can make.
    A Set step may also have a `hold`, an embedded Wait that runs after its
    settings are written (e.g. hotplate to 50 °C, settle 10 min). It goes
    through the same code as a Wait step, so it gets the same events, the
    same Stop handling and counts in the same estimates. It only delays the
    next step; the settings stay either way. A separate Wait step is still
    there for waiting without changing anything.
  - **Wait**: hold, marked **Settle** or **Measure**, with an optional label.
  - **Loop**: for each value in order, set one setting, then run the steps
    inside the loop.
  - **Repeat**: run the steps inside it N times.

  Loops nest by containing other loops. Fixed, startup and finishing
  settings are Set steps placed before or after a loop.
- The builder edits the recipe as a flat outline. Each loop opens with a
  Loop row and closes with a `Next` row, like a BASIC `FOR … NEXT`. Rows
  inside a loop are indented and tinted per nesting level. A Next row closes
  the nearest open loop above it, so the innermost loop runs through all its
  values before the loop outside it moves on.
- Moving with ▲/▼ (`recipes/outline.py`) always keeps the outline valid:
  - A Set or Wait row passing a Loop row enters that loop. Passing a Next
    row leaves it.
  - A Loop row moves its whole loop with it.
  - A Next row changes where its loop ends. It can take in or leave out a
    whole inner loop, but cannot move past the outer loop's Next row.
- A Loop's values are either a list (any order, repeats kept) or a
  from/to/step range. The range is expanded to a list before it is saved.
- A numeric setting can be reached by a `Ramp` instead of a jump, e.g. for
  break-in. A ramp is a staircase between two endpoints, so it runs up or
  down. It is defined by step size, number of steps, a list of setpoints
  (Set steps only), or a rate (fine steps every update interval). Every
  value on the way is held; the target is not, so the step's own hold or
  the loop's steps follow on from it.
- A ramp starts from the last value the recipe set for that setting. The
  recipe has no branching, so this is always known exactly: a loop's first
  value ramps on from wherever the previous step left it, and each later
  value from the one before. An optional fixed start makes the setting
  jump there first. That start is required only when nothing earlier sets
  the value, because the recipe never reads the instrument to guess. The
  "+ Ramp" button adds a Set step with a fixed start, for full control of
  both endpoints.
- `recipes/plan.py` is the one in-order walk of a recipe. It expands loops
  and ramps into what is written and what is waited for. The runner
  executes it; the timeline, time estimate, per-step times and supply
  checks read it, so they cannot disagree. Because ramp times can differ
  per pass, estimates come from this walk, not from multiplying passes by
  time per pass. Ramps are recorded as `recipe_ramp` events and their holds
  as Settle waits.
- Every recipe has an end state, shown as a fixed last row. It sets how the
  rig is left when the run finishes, when Stop is pressed and if the recipe
  fails. Each device goes to its safe state (off), is left as it is, or is
  set to given values. Any device not listed goes to safe state, so
  forgetting one is never the dangerous choice. Stop skips all remaining
  steps, ramps and holds included, and applies it at once; end-state
  settings never ramp. New recipes leave MFCs and back-pressure
  controllers as they are, because stopping the gas or closing the outlet
  can draw liquid back towards the MFC; changing that, leaving a supply
  on, or flowing gas into a closed outlet asks for confirmation. Start
  shows a one-line summary of the end state to confirm. A device whose
  end-state write fails falls back to its own safe state. If control is
  taken away mid-run (global safe state or fault lock), recipe writes are
  refused, so devices fall back too: an emergency always wins. The global
  safe state is unchanged, for emergencies. Recipe files saved before end
  states existed send everything to safe state, as they did when written.
- Each wait is recorded as a `recipe_wait` event with its `purpose` and loop
  position, so recorded data can separate settling from measuring.
- Any step may have a human-readable `name`, and a loop may name each pass
  (`pass_names`, one per value, e.g. "break in, 100 mA, 200 mA"). Names
  appear in the builder list, the timeline, the run status, and the loop
  positions recorded with each event.
- The builder's side panel is a compact summary of the selected step. You
  can change values there by double-clicking them. Double-clicking the step
  opens a pop-out editor with every option, with OK and Cancel.
- Version 1 files are refused with a clear message rather than converted,
  because no version 1 recipes were ever saved outside development.
- The "delay between iterations only" option was removed. It existed only to
  avoid a final gap in a loop, which can now be done by placing the wait
  where it is needed.

## Alternatives considered

- **A tree widget with drag-and-drop nesting.** Rejected because nesting by
  dragging is hard to see and hard to do precisely in Tk. Loop and Next
  rows make where a loop starts and ends explicit, and plain up/down moves
  are enough to edit it.
- **Next rows that attach to whichever loop is nearest after a move**
  (pure BASIC semantics). Rejected: moving a loop's first row would silently
  change which Next closed which loop. Instead, the rules above keep every
  Loop paired with its own Next.
