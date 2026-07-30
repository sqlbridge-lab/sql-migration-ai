---
name: implement-python
description: Write Pythonic code while explaining how it differs from Java in syntax and idioms. No over-abstraction, MVP first, testable structure. Provide code and tests together.
---

# implement-python

Once a spec is ready, write the actual Python code. The developer on this project comes
from a Java background, so **explain how things differ from Java as you write the code**.

## Code Writing Principles

- **Write Pythonically**: use dataclasses, Protocol/ABC, type hints, list/dict
  comprehensions, iterators/generators, context managers, and other Python idioms.
- **No over-abstraction**: don't preemptively create interfaces, factories, or strategy
  patterns for a single call site. In the MVP stage, easy-to-understand code comes first.
- **Testable dependency structure**: make external resources (files, DBs, LLMs)
  injectable so they can be substituted in tests.
- **Provide code and tests together.** When you write logic, write the pytest tests for
  it too.

## Java Developer Mentoring

Whenever something differs from Java, explain it **by comparison**. Common contrasts:

- **dataclass** ↔ Java's POJO/record. Fields, constructor, and `__eq__` are generated
  without boilerplate.
- **Protocol / ABC** ↔ Java's interface. Protocol uses structural typing (duck typing),
  so it's satisfied without an explicit `implements` declaration.
- **type hints** are for static checkers like Pyright, not runtime enforcement — different
  in nature from Java's compile-time types.
- **comprehension / generator** ↔ Java Stream. `yield`-based generators are lazily
  evaluated.
- **context manager (`with`)** ↔ Java's try-with-resources.
- **exception handling**: Python has no checked exceptions. Don't swallow exceptions;
  catch them only where needed.
- **mutable default argument pitfall**: `def f(x=[])` is evaluated once at definition time
  and shared across calls. If you need a default, use `None` and initialize inside the
  function.

## Procedure

1. Check the target spec/task.
2. Implement the task with minimal code (global principles: simplicity first, surgical
   changes).
3. Briefly explain any new Java↔Python differences that came up.
4. Write pytest tests alongside.
5. Before committing, pass the gate via pre-commit-check; before pushing, via
   pre-push-check.
