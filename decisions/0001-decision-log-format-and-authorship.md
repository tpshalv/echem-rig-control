# 0001 — Decision log: format, scope, and authorship

Status: Accepted

## What this is

This project's software is being built with the help of at least two AI
assistants working in separate sessions with no shared memory: Claude
(used here for architecture review, design discussion, and drafting
implementation handover prompts) and a separate ChatGPT session that does
most of the actual code implementation, sometimes making its own
implementation-level decisions Claude was never part of.

This folder exists because that setup has a real failure mode: a decision
made and reasoned through in one session can be silently contradicted or
"cleaned up" by a later one that never saw the reasoning, because nothing
survives except the code itself — and code cannot contain the reasoning
for a path that was *rejected*, only the path that was taken.

## What belongs in an entry, and what doesn't

Capture: the problem that prompted the decision, what was decided, why,
and what alternatives were considered and rejected.

Do **not** restate how the current code works, what a module does, or its
structure — that's what the source is for, and a description of it here
would drift out of date and become actively misleading. If you're reading
this to understand *how something works*, stop and go read the code
instead. Read this only to understand *why* it's shaped that way.

## Rules

- Entries are numbered sequentially and never edited after being written.
- If a decision is later reversed or replaced, write a **new** entry and
  mark the old one `Superseded by 000X` — don't rewrite history.
- Every entry names which assistant wrote it and how much of the
  reasoning it actually witnessed. Where an entry covers a decision made
  or implemented in a session it wasn't part of, it says so explicitly in
  a **Gaps** section, and leaves that space for whoever has the missing
  context (the other assistant, or the human) to fill in — rather than
  guessing.

## Retrospective note

Entries 0002–0012 were written by Claude, reconstructing decisions
already made earlier in one specific conversation with the project's
maintainer. They cover only what was explicitly discussed and reasoned
through in that conversation. Several of them touch on code that was
actually implemented by ChatGPT in separate sessions Claude did not
observe — those entries say so and leave a **Gaps** section for that
context to be added later, rather than inventing it.
