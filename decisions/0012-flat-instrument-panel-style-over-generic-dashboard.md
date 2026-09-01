# 0012 — Flat "instrument panel" visual style, rejecting rounded/soft "AI-generic" defaults

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Not implemented in the real Tkinter UI as of this entry — this covers a
mockup-stage visual direction; see Gaps.

## Context

An initial redesign mockup of the Operation screen (rounded cards, soft
drop shadows, pastel-tinted status pills) was explicitly rejected by the
maintainer as "very typical Claude-generated style." A second attempt,
overcorrected the other direction (heavy tracked-uppercase, monospace
used everywhere including body text) was also rejected as "much worse,"
too try-hard, not "pretty standard."

## Decision

Settled on a flat, rule-based "Blueprint" visual direction — pale paper
background, navy ink, hairline dividers, corner-bracket marks, no rounded
corners or drop shadows — paired with plain, conventional typography
(ordinary sentence case, Segoe UI, small tracked-uppercase used only
where a table or form would already use it, e.g. column headers). Status
text stays uncoloured when a reading is normal and only turns red when
something is actually wrong, rather than colour-coding everything.

Separately, corrected an actual mockup mistake during this process: a
window's title bar was styled differently per direction in early
mockups, which is not something a real Tkinter/Windows app can do — the
OS renders the title bar, not the application, so it must always be
plain, standard OS styling regardless of the app's own visual direction.

## Reasoning

The flat, rule-based style was chosen partly because it more
authentically reads as a lab-instrument/control-panel interface rather
than a generic modern web dashboard, and partly because it's more
honestly achievable in real ttk (which can't easily do soft shadows or
gradients) — so this decision aligns "looks right for the subject" and
"is actually buildable" rather than trading one off against the other.
The plain-typography correction specifically rejected using heavy
uppercase/tracking/all-monospace as a way to seem more "designed" —
several rounds of maintainer feedback made clear that ordinary,
conventional type read as more trustworthy than anything visibly
stylised.

## Alternatives considered

Four genuinely different directions were sketched before narrowing:
an "oscilloscope" (near-black, glowing phosphor-green), a "blueprint"
(the one chosen), an "industrial SCADA" (dark, beveled, indicator-style status
dots), and a "bench instrument" (graphite, single restrained accent, no
borders at all, spacing-only hierarchy). Blueprint was picked by the
maintainer directly, not inferred by Claude.

## Gaps

As of this entry, this visual direction exists only as HTML mockups
(published as artifacts during the design conversation), not yet applied
to the real `ttk.Style()` theming or widget code. Whether it was
subsequently implemented, and any real-ttk constraints that changed it in
the process, are not known to Claude.
