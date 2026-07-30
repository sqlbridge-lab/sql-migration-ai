---
name: debug-python
description: Debug Python bugs systematically. Before rewriting whole code, work through current problem, root cause, minimal fix, and structural improvement in order.
---

# debug-python

When you hit a bug, **don't rewrite the whole thing.** Pinpoint the cause and fix it with
the minimal change.

## Procedure (in order)

1. **Current problem**: what is behaving wrong? Clarify how to reproduce it and the
   expected vs. actual value. If possible, first write a test that reproduces the failure.
2. **Root cause**: why does it happen? Don't guess — pinpoint the root cause using logs,
   stack traces, and value inspection.
3. **Minimal fix**: the smallest change that targets only the cause. Don't "improve"
   adjacent code (global principle: surgical changes).
4. **Structural improvement**: **propose** what should change so the same bug can't recur.
   Present it separately from the minimal fix and don't apply it immediately.

## Verification

- Confirm the reproduction test written in step 1 now passes.
- Confirm existing tests still pass (regression guard).
- No shotgun fixes that change multiple places at once without pinpointing the cause.
