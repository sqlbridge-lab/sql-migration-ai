---
name: pre-push-check
description: Pre-push gate. Check that ruff check / ruff format --check / pyright / pytest pass, and whether docs are in sync.
---

# pre-push-check

Pre-push gate. On top of the automated line of defense (`.githooks/pre-push`), it judges
docs sync, which can't be automated.

## Check 1 — Quality gate

Run the following; if any fails, advise not to push.

```sh
ruff check .
ruff format --check .
pyright
pytest
```

This is identical to the line of defense automated by `.githooks/pre-push`. Use this skill
for a manual check when the hook isn't enabled or you want to verify beforehand.

## Check 2 — Docs sync (not automatable)

Judge whether the code changes are out of sync with specs/docs. This can't be automated by
a hook, so the skill judges it.

- Does this change align with the scope of the specs in `docs/specs/`?
- If behavior or interfaces changed, do the related docs need updating too?

If out of sync, point out which doc should be updated.
