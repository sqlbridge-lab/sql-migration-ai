---
name: pre-commit-check
description: Just before committing, inspect the staged diff only. Check for secret exposure and commit message format. Do not run tests, modify files, or unstage.
---

# pre-commit-check

Pre-commit gate. Inspects **the staged diff only**. Does not run tests, modify files, or
unstage (those belong to other gates/skills).

## Check 1 — Secret / sensitive data exposure

Scan the added lines of `git diff --cached` for hardcoded secrets.

- api-key, secret, password, token, credential, etc. with an actual value attached →
  **block**.
- `${ENV}`, `<PLACEHOLDER>`, dummy values (test/dummy/example, etc.) → pass.

If found, point out which line and advise switching to an environment variable /
placeholder. (`.githooks/pre-commit` automates this same line of defense.)

## Check 2 — Commit message format

Confirm the commit message follows `{purpose}({scope}): {desc}`.

- purpose: feat, fix, refactor, chore, docs, test
- Example: `feat(parser): add SELECT parsing via SQLGlot`

If it doesn't match, suggest the correct form.

## What it does not do

- Does not run tests (that's pre-push-check).
- Does not modify or unstage files.
