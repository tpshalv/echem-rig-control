# 0019 — Operator notes, QC flags and an end-of-run review, kept beside the data

Status: Accepted and implemented.
Written by: Claude, from a design conversation with the project maintainer.
Claude took part in all of the reasoning recorded here and implemented it.

## Context

Much of what explains a run happens on the bench, where the software cannot
see it: a line is disconnected or reconnected, a reservoir is topped up, a
bubble is cleared, a fitting starts to leak. Until now this went into a paper
lab notebook in small handwriting, with times that then had to be matched
against the data by hand. A run's purpose also often changes part-way: the
maintainer starts testing one thing, notices something else, and changes
conditions to test that instead. Reading the data later means knowing both
the original plan and what it turned into.

The maintainer wanted to record these things with the data, from the screen
used to run the rig, without making Operation cluttered. They expected to use
it occasionally, not constantly.

## Decision

- **Two note types only: Lab note (the default) and QC flag.** The type is a
  rough sorting aid, not a taxonomy. A QC flag means "data around here is
  suspect". A larger set of tags was considered and rejected as too much to
  fill in for something used occasionally.
- **Notes can be added two ways, and both use the same dialog.**
  - Typed in a one-line box in the recording panel (Ctrl+N). The note's time
    is taken at the **first keystroke**, not when Enter is pressed, because
    notes are usually written just after the event.
  - Double-clicking a live graph. This covers the common case: "I can see on
    the pressure trace where I connected the line, a few minutes ago". The
    time comes from the frame that was on screen when clicked, so a graph
    that keeps scrolling cannot shift it.
- **A note placed from a graph carries an uncertainty based on the zoom
  level:** about two pixels' worth of time, rounded up to a 1–2–5 step
  (about ±2 s on a 10-minute view, ±2 min on a 24-hour view). This is honest
  about how precisely a mouse click can place a time. It is not a measure of
  how sure the operator is about when the event happened.
- **Graph placement snaps to the nearest real reading of the clicked signal,
  but only if one is within a few pixels.** That way the note sits on a
  reading, not between samples. A click in a gap between sparse readings
  stays where it was made, rather than jumping to a reading the operator
  did not point at. Snapping does not reduce the uncertainty.
- **Snapping to the steepest nearby change was considered and rejected.** It
  would guess at intent, and the approximate click is good enough for
  reinterpreting a run later.
- **Notes are shown as dashed vertical markers on every live graph**, with a
  flag at the top to hover over. Hovering shows a summary (time, time into
  the run, type, text, how it was placed). A click pins the summary. Markers
  too close to tell apart are grouped into one marker with a count, so a
  long view does not become a row of overlapping flags. One "Show notes"
  setting is shared by all graphs.
- **A note slightly newer than the latest reading is held at the graph's
  right edge** until the next reading arrives. Without this, a note typed
  just now would be invisible for up to one polling interval, which makes
  it look as if it was not saved.
- **Mistakes are fixed from the marker's right-click menu: Edit…, Move here
  (then click the right spot), Delete.** Dragging a marker was rejected:
  it is fiddly in Tk, and on a live graph it is too easy to do by accident.
- **Notes have their own append-only journal, `annotations.journal.jsonl`.
  Corrections are new records pointing back to the note they replace;
  nothing is rewritten.** The run folder keeps what was changed and when,
  matching how measurements and events are already treated. Notes are linked
  to the data only by timestamp and never change a reading.
- **Notes can only be added or changed while recording, and only within the
  current run.** The graphs also show monitoring from before the run
  started, so a double-click there is refused straight away, before
  anything is typed. When nothing is recording there is no run journal to
  write to, and a note must never be silently dropped. For the same reason,
  the recorder raises an error for a note outside a run, whereas it quietly
  ignores system events outside a run.
- **Exports:**
  - The wide CSV gets `notes` and `qc_flags` columns. They come **last**, so
    existing signal columns do not shift, and they are **always present** so
    every run has the same layout.
  - Each note goes in the time bin its timestamp rounds to, using the same
    rule as readings. A bin with no readings still gets a row, so no note is
    lost.
  - Because a note can be placed in the past, edited or deleted, any change
    to the notes journal triggers a full CSV rebuild instead of the usual
    append. This is acceptable because notes are rare.
  - Excel gets a Notes sheet.
  - Separate columns were chosen so that analysis can drop QC-flagged
    regions in one step without reading the note text.
- **Optional end-of-run review.** When recording stops, a dialog that can be
  skipped shows:
  - the start notes, read-only
  - the notes made during the run, read-only
  - an **overall QC flag**: Not assessed / None / Minor / Severe / Failure
  - free-text **end notes**

  Showing the start notes next to the end notes is deliberate: it is the
  "before and after" record for runs whose purpose changed part-way.
  "Not assessed" is kept separate from "None", so "I didn't say" is never
  read as "I checked and it was clean".
  - The run-level Notes field was renamed **Start notes** to make the pair
    clear.
  - Recording has already stopped when the dialog appears, so the review
    never delays data being saved.
  - A review can be added or replaced later with **Review run…**.
- **The review is stored in its own append-only `run-review.journal.jsonl`;
  the newest entry wins.** `metadata.json` is not rewritten. Metadata is
  frozen once a run starts on purpose, and the start notes must stay
  exactly as written. Saving a review regenerates only the Excel file,
  where the review leads the Overview sheet. The CSV is not regenerated
  because the review does not change it.

## Alternatives considered

- **Reusing `Event` (source `"operator"`) for notes.** This was the first
  suggestion, because events already reach the run journal and the Excel
  Events sheet. It was rejected when the design firmed up. An event has no
  place for the note type, the uncertainty, the clicked signal, or an ID
  that lets a correction point to its original. Also, the operator's few
  notes would be buried among automatic command events.
- **Showing the software's own events (setpoint changes, recipe steps,
  warnings) on the graphs as well.** Suggested, and declined by the
  maintainer. The aim is to record what the software *cannot* see.
- **A "plan change" note type** that splits a run into phases, with a
  `phase` column in the CSV. Declined by the maintainer as more structure
  than needed. The start and end notes on the review, plus an ordinary note
  at the moment of change, cover it.
- **A verdict (Good / Usable with caveats / Discard) in the review.**
  Replaced, at the maintainer's request, by an overall QC flag on a severity
  scale. That matches the per-note QC flag and says how bad, not just
  whether.
- **Photo and file attachments, structured hardware-configuration fields,
  and searching notes across runs.** Deferred. Each is useful now and then
  but brings storage or UI weight out of proportion to occasional use. The
  journals are plain JSON lines, so searching across runs can be added
  later as an analysis tool.
- **Adding or editing notes after a run ends.** Not supported. Live graph
  history exists only in memory during a session, so after a run there is
  no graph to click on. The end-of-run review, which can also be added
  later, is where after-the-fact comments go.
- **A time range for a QC flag** ("suspect from 14:30 to 14:42"). Deferred
  until it proves to be needed. A flag currently marks a moment.
