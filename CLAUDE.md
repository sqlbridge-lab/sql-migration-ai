# CLAUDE.md

Claude working guide for the SQLBridge AI project. Apply this together with the
user's global CLAUDE.md principles (think before coding, simplicity first, surgical
changes, goal-driven execution).

## Project Overview

A **learning project** that converts MySQL SQL to PostgreSQL SQL and validates the
correctness and performance of the conversion. Rather than delegating everything to an
LLM, it favors a **hybrid architecture** where anything that can be handled by
deterministic code is handled by code.

- **Parser**: figures out the SQL structure (AST).
- **Rule Engine**: handles deterministic conversion.
- **RAG**: retrieves knowledge — conversion rules, official docs, similar cases.
- **LLM**: assists with complex or unsupported conversions that rules can't handle.
- **Validator**: validates the correctness of the conversion.
- **Performance Analyzer**: analyzes performance based on execution plans.

The LLM is not at the center of every task. Whatever can be handled deterministically
by rules and the parser is handled that way; the LLM is the last-resort assistant.

## Tech Stack

- **Python 3.12+**
- **Ruff**: handles both linting (`ruff check`) and formatting (`ruff format`). The gate
  checks each separately.
- **Pyright**: type checking.
- **pytest**: testing.
- **SQLGlot**: SQL parsing and transpilation.

ruff, pyright, and pytest are declared as optional dev dependencies in `pyproject.toml`.
Install them with `pip install -e ".[dev]"` (or `pip install ruff pyright pytest`). The
RAG/LLM stack is decided in the relevant phase.

## Python Coding Rules

- Use type hints.
- Model domain objects with dataclasses.
- Keep functions small.
- Don't swallow exceptions.
- Use structured logging.
- No over-abstraction or unnecessary design patterns.
- In the MVP stage, prefer easy-to-understand code.

## Java Developer Learning Support

The developer on this project comes from a Java background. Whenever Python syntax or
idioms differ from Java, **explain by comparison** (e.g. dataclass ↔ POJO/record,
Protocol/ABC ↔ interface, comprehensions, generators, context managers, exception
handling, mutable default argument pitfalls, etc.).

## Branch / Commit Rules

- **Branch**: `{name}/{purpose}/{desc}`
- **Commit**: `{purpose}({scope}): {desc}`
- **Example purposes**: feat, fix, refactor, chore, docs, test

## Enabling git hooks

This repo uses committed hooks in `.githooks/`. Enable them once.

```sh
git config core.hooksPath .githooks
```

- `pre-commit`: blocks hardcoded secrets.
- `pre-push`: runs ruff check / ruff format --check / pyright / pytest.

Bypass with `git commit --no-verify` and `git push --no-verify` respectively.

## Workflow

```text
1. Write spec   → write-spec skill (docs/specs, includes task breakdown)
2. Review spec  → Codex (external, user-triggered)
3. Write code   → implement-python skill
4. Review code  → Codex (external) + secondary-review skill when needed
5. Debug        → debug-python skill
6. Commit/push  → pre-commit-check / pre-push-check skills
```

The primary reviewer is Codex (triggered externally by the user). Claude only provides a
supplementary second-pass review through the `secondary-review` skill.
