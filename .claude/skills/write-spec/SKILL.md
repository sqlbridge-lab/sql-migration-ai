---
name: write-spec
description: For each phase/task, write a spec document under docs/specs. Include MVP scope/non-scope and a task checklist, breaking work into steps so code isn't dumped all at once.
---

# write-spec

When starting a new phase or task, don't write code right away — first write a spec
document under `docs/specs/`. The spec includes a **planning breakdown (task checklist)**.

## Principles

- **Don't dump code all at once — go step by step.** The spec breaks a large goal into
  small tasks.
- **State the MVP scope and non-scope explicitly.** Spelling out what you won't do
  prevents scope creep.
- After writing the spec, hand it off to **Codex review** (Codex is the first-pass
  reviewer).

## Procedure

1. Write a paragraph or two of background: what you're building and why.
2. State the **scope / non-scope**.
3. Describe the design and approach. If there are alternatives, note the trade-offs.
4. Break it into a **task checklist**. For each task, note how it will be verified
   (test/check).
5. Hand off to Codex review.

## File Location / Name

Save as `docs/specs/YYYY-MM-DD-<slug>.md`.

## Document Template

```markdown
# <Title>

## Background
Why are we doing this? What problem does it solve?

## Scope
What we're building this time.

## Non-scope
What we're not building this time (including what's deferred to a later phase).

## Design
The approach. Which part of the hybrid architecture
(Parser/Rule Engine/RAG/LLM/Validator/Performance Analyzer) it belongs to.

## Tasks
- [ ] Task 1 → verify: <how to check>
- [ ] Task 2 → verify: <how to check>

## Rationale (trade-offs)
Alternatives and why this was chosen.
```
