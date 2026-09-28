# Instrument captures

Real read-only replies from instruments on this rig, kept as the ground truth
the parsers are written against. Each file is the output of a settings dump,
trimmed to the queries that matter, with a header saying which instrument it
came from and how it was configured at the time.

These are **reference captures**, not a log. One file per model, firmware and
configuration — not one per run. Day-to-day dumps belong outside the checkout,
in `%LOCALAPPDATA%\EchemRigControl\device-dumps\`.

Add a capture when it tells us something the existing ones do not: a new
model, new firmware, or a configuration that parses differently. Tests replay
them through the real parsers, so a capture that stops matching the code fails
the build instead of going quietly stale.

Provenance matters more than tidiness: keep the instrument's exact text,
including odd spacing and Alicat's backtick degree symbol.
