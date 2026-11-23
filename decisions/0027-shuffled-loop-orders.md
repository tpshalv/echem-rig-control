# 0027 — Loops can run their values in a shuffled order, drawn from a recorded seed

Status: Accepted and implemented.
Written by: Claude, from a request by the project maintainer, who agreed the
design below before it was built.

## Context

A recipe loop always ran its values in the order listed. For a sweep such as
"at each current density, measure at each gas flow", running the flows in the
same order every time ties any drift in the cell to flow, so the two cannot
be told apart. Randomising the flow order, and drawing a different order at
each current density, separates them.

Randomising cannot be the default. Some loops need their order: current
density, for example, must rise from low to high. So the choice belongs to
each loop.

Decision 0018 makes `recipes/plan.py` the one walk of a recipe. The runner,
timeline, estimates and supply checks all read it, so they cannot disagree.
An order drawn at random while the recipe runs would break that.

## Decision

- **Each loop has an order** (`LoopOrder`), saved in the recipe file:
  - **As listed**: the default, and what every earlier recipe does.
  - **Shuffled, a new order each time the loop starts**: e.g. a new flow
    order at each current density.
  - **Shuffled once, the same order every time**.
  Files saved before this run their loops as listed.
- **The order comes from a seed, not from chance at run time.** `plan()`
  takes a `seed`. Each shuffled loop draws its order from the seed and from
  where it is in the recipe, and a loop shuffled each time also uses the
  passes of the loops around it. The same seed always gives the same plan,
  so the timeline, estimate, checks and run still agree.
- **The run records its seed.** The recording's metadata keeps
  `recipe_order_seed`, the `recipe_started` event repeats it, and each
  shuffled loop logs a `recipe_loop_order` event with its values in run
  order. Every event already names its loop position, so analysis needs
  nothing new. Running the same recipe with the same seed repeats the order
  exactly.
- **The builder shows the order that will run.** When any loop is shuffled,
  the footer shows "Shuffled loops use order no. N", with a Reshuffle
  button. The number can be typed to repeat an earlier run. The timeline,
  checks, Validate and Start all use it. A new number is drawn when a recipe
  is loaded and after each run, so running again gives a new order.
  A run started without a seed (scripts, tests) draws one.
- **Pass names move with their values.** A loop's pass index in a plan's
  trace is the value's place in the list, so per-pass times and names still
  refer to the same value. The "(2/5)" count in a position is in run order.
- **Supply checks use the drawn order,** at Start, rather than every
  possible order. That is the order that runs.

## Alternatives considered

- **Shuffle while the recipe runs.** Rejected: the timeline and estimate
  would show one order and the run would follow another.
- **Check every possible order.** Rejected: the number of orders grows
  factorially, and only the drawn one runs.
- **Balanced orders (a Latin square), so each value takes each position
  equally often.** Deferred. With few outer values, random orders can cluster
  by chance, and a Latin square fixes that. It can be added as another
  `LoopOrder` if clustering turns out to matter.
- **A serpentine order (forwards, then backwards).** Deferred. It is a cheap
  fixed pattern against drift and could also be another `LoopOrder`.
