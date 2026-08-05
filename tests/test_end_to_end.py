from pathlib import Path

import pytest

from harness.executor import MYSQL_CONFIG, POSTGRES_CONFIG, Executor
from harness.loader import load_corpus
from harness.runner import Runner
from harness.transform import PassThroughTransformer

# pass-through 기준 기대 스냅샷. Task 8(Step 3) 실행으로 확정.
EXPECTED_STATUS = {
    "limit-pagination": "fail",
    "ifnull-coalesce": "fail",
    "backtick-identifier": "fail",
    # fix_clock가 대상 dialect로 파싱·재생성하며 DATE_ADD/INTERVAL을 PG 문법으로
    # 전사(transpile)해, pass-through인데도 PG에서 실행돼 결과가 일치한다(error 아님, 실측 pass).
    "date-function": "pass",
    "enum-type": "pass",
    "bool-tinyint": "fail",  # P3: PG boolean
    "unsigned-type": "pass",
    "upsert-on-duplicate": "fail",
    "auto-increment": "fail",
    "covering-index": "pass",
    "multi-join": "pass",
    "keyset-vs-offset": "pass",
    "non-sargable-like": "pass",
    "groupby-aggregate": "pass",
}


@pytest.fixture(scope="module")
def results(mysql_up, postgres_up):
    root = Path(__file__).resolve().parent.parent
    cases = load_corpus(root / "corpus" / "cases", root / "corpus" / "concepts.yaml")
    runner = Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())
    return [runner.run_case(c) for c in cases]


@pytest.mark.integration
def test_no_errors(results):
    errors = [(r.case_id, r.stage, r.reason) for r in results if r.status == "error"]
    assert errors == [], f"error 발생(케이스/환경 문제): {errors}"


@pytest.mark.integration
def test_expected_status_snapshot(results):
    assert {r.case_id: r.status for r in results} == EXPECTED_STATUS


@pytest.mark.integration
def test_all_fails_have_stage_and_reason(results):
    for r in results:
        if r.status == "fail":
            assert r.stage and r.reason, f"{r.case_id}: fail인데 stage/reason 없음"


@pytest.mark.integration
def test_seed_invariant_both_dbs(results):
    """dml/ddl 실행 후 양 DB 공유 시드·값 불변(P2-5)."""
    for cfg, dialect in [(MYSQL_CONFIG, "mysql"), (POSTGRES_CONFIG, "postgres")]:
        with Executor.connect(cfg, dialect) as ex:
            assert ex.run_query("SELECT COUNT(*) FROM users").rows[0][0] == 1000
            assert ex.run_query("SELECT COUNT(*) FROM orders").rows[0][0] == 50000
            # upsert 대상 값 복원 확인
            assert (
                ex.run_query("SELECT name FROM users WHERE id = 1").rows[0][0]
                == "User 1"
            )


@pytest.mark.integration
def test_cli_exit_code_nonzero(mysql_up, postgres_up):
    """실제 CLI main()이 fail 존재 시 non-zero 종료(P2-6)."""
    from harness.__main__ import main

    assert main([]) == 1
