"""생성 코퍼스의 하니스 게이트·seed preflight — 실제 DB 컨테이너 필요.

CLI(`python -m harness`)·`load_corpus`는 generated(syntax 9개)를 커버리지 미달로
못 돌린다(리뷰 2차 #1). 그래서 low-level 조립으로 로드한다:
    load_cases → validate_corpus(..., allow_incomplete_coverage=True) → load_case
그다음 Runner.run_case로 돌려 `all(status != "error")`를 본다(P2-8, fail 허용).

`@pytest.mark.integration`이라 컨테이너가 떠 있을 때만 돈다(pre-push는 DB 없이 통과).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from generate_cases import (
    count_by_concept,
    generate_all,
    load_golden,
    render_distribution,
    write_corpus_atomic,
)
from validate_corpus import load_cases, load_concepts, validate_corpus

from harness.executor import MYSQL_CONFIG, POSTGRES_CONFIG, Executor
from harness.loader import Case, load_case
from harness.runner import Runner
from harness.transform import PassThroughTransformer

CONCEPTS_PATH = _ROOT / "corpus" / "concepts.yaml"
GOLDEN_DIR = _ROOT / "corpus" / "cases"


@pytest.fixture(scope="module")
def loaded_cases(tmp_path_factory: pytest.TempPathFactory) -> list[Case]:
    """생성 → temp 산출 → low-level 재로드(load_corpus/CLI 경로를 쓰지 않는다)."""
    whitelist = load_concepts(CONCEPTS_PATH)
    golden = load_golden(sorted(GOLDEN_DIR.rglob("*.yaml")), whitelist)
    cases = generate_all(golden)
    out = tmp_path_factory.mktemp("generated") / "generated"
    counts = count_by_concept(cases)
    write_corpus_atomic(out, cases, whitelist, render_distribution(counts, 1000))

    raws, load_result = load_cases(sorted(out.rglob("*.yaml")))
    result = validate_corpus(raws, whitelist, allow_incomplete_coverage=True)
    assert not (load_result.errors + result.errors)
    return [load_case(r) for r in raws]


@pytest.fixture(scope="module")
def results(loaded_cases: list[Case], mysql_up: None, postgres_up: None) -> list:
    runner = Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())
    return [runner.run_case(c) for c in loaded_cases]


# --- 태스크7: 하니스 게이트(error==0, pass 아님) ---


@pytest.mark.integration
def test_generated_corpus_has_no_errors(results: list) -> None:
    """생성 케이스 전체가 error 없음(fail은 허용 — MySQL 전용 문법의 PG 변환 실패는 정상)."""
    errors = [(r.case_id, r.stage, r.reason) for r in results if r.status == "error"]
    assert errors == [], f"error 발생(케이스/제어/인프라 문제): {errors[:15]}"


@pytest.mark.integration
def test_generated_corpus_is_large(loaded_cases: list[Case]) -> None:
    assert len(loaded_cases) >= 1000


@pytest.mark.integration
def test_seed_invariant_after_generated(results: list) -> None:
    """dml/ddl 실행 후 공유 시드·스키마 불변(격리 계약)."""
    for cfg, dialect in [(MYSQL_CONFIG, "mysql"), (POSTGRES_CONFIG, "postgres")]:
        with Executor.connect(cfg, dialect) as ex:
            assert ex.run_query("SELECT COUNT(*) FROM users").rows[0][0] == 1000
            assert ex.run_query("SELECT COUNT(*) FROM orders").rows[0][0] == 50000
            # upsert 대상 시드 행이 롤백으로 복원됐다.
            assert (
                ex.run_query("SELECT name FROM users WHERE id = 1").rows[0][0]
                == "User 1"
            )


# --- 태스크8: seed preflight(표본 품질 — 0행 아님, P2-6) ---

# 대표 케이스 id: 씨드 전역 범위 안이면서 비어있지 않아야 하는 것들
# (offset이 작은 페이지, 존재하는 ENUM 값, stock>0 경계). 실제 SQL은 로드된 케이스에서 읽는다.
_PREFLIGHT_IDS = [
    "limit-pagination-5-0",  # LIMIT 0, 5 — 첫 페이지.
    "enum-type-paid-eq-20",  # status='paid' — 씨드에 다수 존재.
    "unsigned-type-0-gt-20",  # stock > 0 — 대부분의 행.
]


@pytest.mark.integration
def test_seed_preflight_representative_nonempty(
    loaded_cases: list[Case], mysql_up: None
) -> None:
    """대표 케이스의 MySQL 결과가 0행이 아님을 실측(코드 상수 리뷰로는 못 보는 표본 품질)."""
    by_id = {c.id: c for c in loaded_cases}
    with Executor.connect(MYSQL_CONFIG, "mysql") as ex:
        for case_id in _PREFLIGHT_IDS:
            case = by_id.get(case_id)
            assert case is not None, f"대표 케이스 없음: {case_id}"
            assert case.mysql is not None
            rows = ex.run_query(case.mysql).rows
            assert len(rows) > 0, f"{case_id}: 결과 0행(표본 무의미)"


# --- 리뷰 #6 회귀: upsert 갱신이 실제로 관찰되는가(DO NOTHING 오판 방지) ---


@pytest.mark.integration
def test_upsert_updates_are_actually_observed(
    loaded_cases: list[Case], mysql_up: None
) -> None:
    """대표 upsert 케이스를 트랜잭션 안에서 실행해 post_query 결과가 시드와 달라짐을 실측.

    삽입값이 시드와 같으면(no-op) 또는 post_query가 갱신 컬럼을 안 보면, 변환기가
    ON CONFLICT DO NOTHING으로 잘못 바꿔도 통과하는 오라클 사각지대가 된다(리뷰 #6).
    갱신이 실제로 관찰됨을 확인해 그 사각지대가 없음을 고정한다.
    """
    by_id = {c.id: c for c in loaded_cases}
    upsert_ids = [
        c.id
        for c in loaded_cases
        if c.concepts == ["upsert-on-duplicate"] and "-1-user1" in c.id
    ]
    assert upsert_ids, "대표 upsert 케이스 없음"

    seed_sql = "SELECT id, name, created_at FROM users WHERE id = 1"
    with Executor.connect(MYSQL_CONFIG, "mysql") as ex:
        seed = ex.run_query(seed_sql).rows[0]
        for case_id in upsert_ids:
            case = by_id[case_id]
            assert case.statement is not None
            post_query = case.control_mysql["post_query"]
            ex.begin()
            try:
                ex.run_statement(case.statement)
                after = ex.run_query(post_query).rows[0]
            finally:
                ex.rollback()  # 격리: 시드 복원.
            assert after != seed, (
                f"{case_id}: upsert가 no-op이라 갱신이 관찰되지 않음(오라클 사각지대)"
            )
