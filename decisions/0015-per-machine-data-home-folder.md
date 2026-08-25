# 0015 — Per-machine data home, separate from the source checkout

Status: Accepted and implemented.
Written by: Claude, from direct discussion with the project maintainer.

## Context

Real rig profiles, application settings, technical logs, and recorded
experiment data have always defaulted to living directly at the
repository root, next to the Python source. The maintainer flagged this
directly: they didn't want settings, rig profiles, or recorded data
sitting next to the source code, and wanted "one obvious home folder"
instead - explicitly framed as cleanup work, not urgent, but wanted
planned properly before being implemented.

This matters more than it might for a typical app because of how this
project is actually deployed (see decisions/0013's context): the
repository gets cloned or copied by USB onto more than one machine, and
gets updated/re-cloned over time. Real per-machine configuration sitting
inside the checkout risks being wiped, confused with the shipped example
files, or accidentally committed.

## Decision

One data-home folder per machine, resolved by a new `app_paths.py`:

1. `ECHEM_RIG_CONTROL_HOME` environment variable, if set - an explicit
   override, e.g. for a deliberately portable/USB-drive deployment where
   everything should travel together.
2. Otherwise `%LOCALAPPDATA%\EchemRigControl` on Windows - the standard
   place Windows keeps per-machine app data that shouldn't roam.
3. Otherwise `~/.echem-rig-control` as a last-resort fallback.

Layout under that folder: `settings/`, `profiles/`, `logs/`,
`experiments/`, and `app-selection.toml` at the top (unchanged in
purpose - still just the pointer to which settings file and rig profile
are currently active).

The application-settings *defaults* for `default_output_directory` and
`technical_log_path` now point into this folder (computed from
`app_paths.py` at definition time) instead of the bare relative strings
`"experiments"` and `"logs/rig-control.log"`. `main()`'s `--selection`
CLI argument now defaults to the data-home's `app-selection.toml` instead
of a checkout-relative literal.

The example/simulation profiles shipped in the repository
(`rig-profile.simulation.toml`, `rig-profile.example.toml`) are
deliberately **not** moved - they're fixtures the test suite and quick
demos reference directly, not a real rig's configuration, and stay
checked into git exactly where they are.

## Reasoning

An OS-standard per-machine location (rather than, say, a folder sitting
next to the repository) was chosen specifically because the repository
itself is not a stable location on this project's own deployment model:
it gets re-cloned and moved. Real configuration needs to survive that
independent of wherever the checkout happens to sit, which argues for
a location keyed to the machine/user, not the checkout.

Only the *defaults* changed, not the resolution mechanism: `--profile`,
`--settings`, and `--selection` continue to override anything explicitly
passed, and a value already present in an existing settings file
(e.g. a custom `default_output_directory`) is untouched - this only
affects what a brand-new settings file, or the CLI with nothing passed
and no selection file yet, resolves to.

## Alternatives considered

- A folder sitting next to (as a sibling of) the repository checkout -
  rejected for the reason above: re-cloning, moving, or deleting the
  checkout would risk taking the data with it or orphaning it, exactly
  the instability being designed against.
- Automatic migration code that detects and moves pre-existing
  checkout-root files on first run - rejected as unnecessary permanent
  complexity for a one-time transition on what is, in practice, a
  handful of machines the maintainer controls directly. A plain manual
  step (documented in ARCHITECTURE.md) was preferred; the maintainer's
  own machine was migrated directly as part of this change.

## Gaps

Implemented in `app_paths.py`, `app_settings.py` (default values only),
and `ui/home/window.py` (`main()`'s `--selection` default). The
maintainer's own machine has been migrated to the new layout as a live
example. Not yet exercised on a second machine or with a real (non-stub)
rig profile.
