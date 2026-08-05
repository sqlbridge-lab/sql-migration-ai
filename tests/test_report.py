from harness.report import exit_code, summarize
from harness.runner import CaseResult


def _r(status, stage=None, reason=None):
    return CaseResult("c", status, stage, reason)


def test_exit_code_zero_all_pass():
    assert exit_code([_r("pass"), _r("pass")]) == 0


def test_exit_code_nonzero_any_fail():
    assert exit_code([_r("pass"), _r("fail", "compare", "x")]) != 0


def test_exit_code_nonzero_any_error():
    assert exit_code([_r("error", "infrastructure", "x")]) != 0


def test_summarize_counts_and_reason():
    out = summarize([_r("pass"), _r("fail", "pg.statement", "boom")])
    assert "pass" in out and "fail" in out and "boom" in out
