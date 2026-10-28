# 0022 — Graph reader, and mode buttons deciding what a double-click does

Status: Accepted and implemented.
Written by: Claude, from a design conversation with the project maintainer.
Claude took part in all of the reasoning recorded here and implemented it.

## Context

The maintainer wanted to read values off the live graphs: a crosshair with
a live readout, the option to follow one trace when several run close
together, and a quick way to measure a change between two points. On the
dashboard, double-click already added a note (0019), so the two features
needed a clear way to share the mouse.

## Decision

- **Two mode buttons on each dashboard panel: Graph reader and Add note.**
  - They look pressed in while on, and clicking one again turns it off.
  - They are mutually exclusive: pressing one releases the other.
  - With neither on, a double-click does nothing, and dragging and the mouse
    wheel still pan and zoom.
  - A hint line beside the buttons explains the active mode, so the gestures
    don't have to be remembered.
- **This changes 0019.** On the dashboard, a double-click adds a note only
  while Add note is on. The note box, the right-click note menu and the
  hover summaries are unchanged.
- **Graph reader, with no trace selected, is a screen reader.** It shows the
  time under the cursor and the cursor's height on each value axis. It does
  not snap to data.
- **Selecting a trace.**
  - Double-clicking a trace selects it: it is drawn thicker, the others are
    faded, and the crosshair snaps to its nearest drawn reading.
  - Double-clicking the same spot again cycles through the traces within
    reach, nearest first, which makes closely spaced traces usable.
  - Double-clicking empty space, or pressing Esc, deselects.
- **Measuring.**
  - Shift-click two points to show Δtime, Δvalue and the rate. The rate is
    per minute, or per hour for spans of an hour or more.
  - The points snap to the selected trace if there is one; otherwise they are
    screen positions read on every axis.
  - The points are stored in data terms, so they stay put while the graph
    scrolls and rescales.
- **The single-trace popup** always has the screen reader on, with no
  button, selecting or measuring. Double-click there still adds a note.
- **Data resolution.** The reader uses the ~500 displayed points. The
  maintainer judged that enough, so zoom still doesn't fetch finer data.

## Alternatives considered

- **Single-click to select a trace, keeping double-click for notes at all
  times.** Rejected in favour of explicit modes. The maintainer wanted
  double-click to mean one clear thing at a time, with the active mode
  visible.
