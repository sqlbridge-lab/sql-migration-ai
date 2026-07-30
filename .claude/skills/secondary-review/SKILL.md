---
name: secondary-review
description: Supplementary second-pass local code review by Claude, run after Codex's first-pass review when needed. Manual invocation only.
disable-model-invocation: true
argument-hint: "[--staged] [--file <path>]"
---

# secondary-review

Codex is the first-pass reviewer; this skill is **Claude's supplementary second-pass
review**. It is named `secondary-review` so it does not collide with the built-in
`/review` command.

This skill is **manual-invocation only** (`disable-model-invocation: true`). The principle
is "Codex first, Claude second when needed," so it never triggers automatically.

## Invocation Contract

The target diff depends on the argument.

- **No argument**: `git diff origin/main...HEAD`
  Committed changes since the current branch diverged from `origin/main`. Uncommitted
  (working-tree) changes are **not included** in the default review.
- **`--staged`**: `git diff --cached`
  Staged changes.
- **`--file <path>`**: the given file diffed against the `origin/main...HEAD` base.
  `git diff origin/main...HEAD -- <path>`
  To review a file before committing, combine with `--staged`:
  `git diff --cached -- <path>`.

## Review Procedure

1. Run the diff command matching the contract above to collect the target changes.
2. Review the changes through the lenses below.
3. Report findings sorted by **severity**.

## Review Lenses

- **Hybrid architecture role separation**: are the responsibility boundaries of Parser /
  Rule Engine / RAG / LLM / Validator / Performance Analyzer respected? Was anything that
  could be handled deterministically pushed onto the LLM?
- **Python code quality**: small functions, no over-abstraction, no swallowed exceptions,
  structured logging.
- **Type hints**: are public functions and data models annotated?
- **Test coverage**: are there tests for the changed logic?
- **Secret exposure**: any hardcoded keys, tokens, or passwords?

## Report Format

Tag each finding with a severity.

- `[required]` — must fix (bugs, secret exposure, architecture violations).
- `[recommended]` — should fix (quality, readability, tests).
- `[note]` — for reference (minor suggestions, alternatives).

If there are no findings, say so explicitly. Provide the review only — do not modify code.
