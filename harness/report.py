"""CaseResult 리스트를 터미널 요약과 exit code로."""

from __future__ import annotations

from collections import Counter

from harness.runner import CaseResult


def summarize(results: list[CaseResult]) -> str:
    counts = Counter(r.status for r in results)
    header = (
        f"총 {len(results)}건 — pass={counts.get('pass', 0)} "
        f"fail={counts.get('fail', 0)} error={counts.get('error', 0)}"
    )
    lines = [header, ""]
    for r in results:
        mark = {"pass": "✓", "fail": "✗", "error": "!"}.get(r.status, "?")
        line = f"  {mark} {r.case_id:30s} {r.status}"
        if r.stage:
            line += f" [{r.stage}]"
        if r.reason:
            line += f" — {r.reason}"
        lines.append(line)
    return "\n".join(lines)


def exit_code(results: list[CaseResult]) -> int:
    return 0 if all(r.status == "pass" for r in results) else 1
