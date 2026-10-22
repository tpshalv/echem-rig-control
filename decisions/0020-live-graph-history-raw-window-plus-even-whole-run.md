# 0020 — Live graph history: a raw window plus an even whole-run copy

Status: Accepted and implemented.
Written by: Claude, from a diagnosis and design conversation with the project
maintainer. Claude took part in all of the reasoning recorded here and
implemented it.

## Context

The maintainer noticed that the live graphs' resolution changed a lot
between time ranges (for example, a good 10-minute view, about ten points on
"Last 8 hours", then a fine "Whole run") and also flickered between good and
poor on a single range while it was open.

The history behind both the live dashboard and the double-click trend popup
was a raw buffer of 500 readings, plus 10 s / 60 s / 600 s min-max bucket
levels, plus a self-condensing overview. Each query used the finest store
that "reached back" to the start of the range. Simulating that store showed
the causes:

- "Last hour" equals the 10 s level's capacity, and "Last 8 hours" equals the
  60 s level's. So the "reaches back" test passed or failed depending on
  bucket timing, and the views flipped between about 500 and about 174 points
  from one refresh to the next.
- The thinning step to 500 points had an integer-rounding cliff: 500 rows
  passed through unchanged, but 501 came out as about 334.
- The overview was thinned by point count every time it filled, so old data
  was thinned again and again. After two hours, the first hour had about
  three points.
- A gap in readings made every fine level fail the test. The query then
  dropped to the 10-minute level (about 6 points per hour) or to the whole
  overview.
- 500 raw readings at 1 Hz did not even cover "Last 10 minutes".

The maintainer asked whether this was overcomplicated, given that about 500
points is easy to draw.

## Decision

- Each channel keeps **every reading from the last 8 hours** (the longest
  fixed range) as-is. It also keeps a **fixed-size copy of the whole run**
  (10,000 samples). When that copy fills, each group of four samples is
  reduced to its min and max. So the resolution stays even across the run,
  spikes survive, and memory does not grow with run length.
- Readings are stored as compact arrays (timestamp, value, quality code),
  about 17 bytes each, instead of row objects. Eight hours at 1 Hz is about
  0.5 MB per channel.
- A graph query takes the slice for the selected range, using the whole-run
  copy only for anything older than the raw window. It then keeps the min and
  max of about 247 equal time bins. The bins sit on an absolute time grid, so
  refreshes don't reshuffle the trace. Bin width comes from the data actually
  in the range, so a 15-minute run in the 8-hour view still gets the full
  point budget. A slice of 500 readings or fewer is returned unthinned.
- The graphs no longer thin the data a second time.

## Reasoning

What the old design actually protected against was memory: decision 0007
came from an overnight memory crash. Drawing speed was never the issue.
Compact arrays make the raw window cheap enough to keep. Binning by time at
query time means the number of points no longer depends on which store
answered. That one property removes the level-switching, the rounding cliff
and the effect of gaps together. A query costs about 1–2 ms per trace, because
the per-bin min and max run as C-level `min`/`max` over array slices.

## Alternatives considered

- **Keep the bucket levels and fix the selection test.** Rejected. Every
  symptom came from choosing between stores, and the fix would still leave
  four stores where two are enough.
- **Keep everything raw.** Rejected. It grows without limit on multi-day
  runs, which is the failure 0007 exists to prevent.
- **One evenly thinned store only.** Rejected. On a long run, short ranges
  would lose detail: after a day, "Last 10 minutes" would get a sample every
  few seconds.

## Gaps

- Zooming into a range on the dashboard still filters that range's 500
  points. It does not fetch finer data for the zoomed window.
- The "Trend history readings" setting now only sets how many recent raw
  readings `measurement_history` returns. It no longer affects what the
  graphs show.
