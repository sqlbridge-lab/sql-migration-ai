"""validate_corpus 정적 검증기 테스트.

검증기가 정상 케이스는 통과시키고, 각 오류 유형을 실제로 잡아내는지 확인한다.
케이스는 dict로 직접 넘긴다(파일 I/O 없이 순수 함수 검증).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from validate_corpus import (
    load_concepts,
    validate_case,
    validate_corpus,
)

WHITELIST = {"limit-pagination", "covering-index", "auto-increment"}


def valid_dql() -> dict:
    return {
        "id": "limit-pagination",
        "kind": "dql",
        "concepts": ["limit-pagination"],
        "ordered": True,
        "mysql": "SELECT id FROM products ORDER BY id LIMIT 10, 5",
    }


def valid_dml() -> dict:
    return {
        "id": "upsert-case",
        "kind": "dml",
        "concepts": ["limit-pagination"],
        "isolation": "fresh",
        "setup": "INSERT INTO users (email) VALUES ('a@b.c')",
        "statement": "INSERT INTO users (email) VALUES ('a@b.c') ON DUPLICATE KEY UPDATE email = email",
        "post_query": "SELECT email FROM users",
    }


def valid_ddl() -> dict:
    return {
        "id": "auto-increment",
        "kind": "ddl",
        "concepts": ["auto-increment"],
        "isolation": "fresh",
        "object": {"type": "table", "name": "tmp_ai"},
        "statement": "CREATE TEMPORARY TABLE {{object_name}} (id INT AUTO_INCREMENT PRIMARY KEY)",
        "exercise": "INSERT INTO {{object_name}} () VALUES ()",
        "post_query": "SELECT id FROM {{object_name}}",
    }


# --- 정상 케이스는 통과 ---


@pytest.mark.parametrize("factory", [valid_dql, valid_dml, valid_ddl])
def test_valid_cases_pass(factory):
    result = validate_case(factory(), WHITELIST)
    assert result.ok, result.errors


# --- 각 오류 유형 검출 ---


def test_missing_required_field():
    case = valid_dql()
    del case["mysql"]
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("mysql" in e for e in result.errors)


def test_unknown_field_typo():
    case = valid_dql()
    case["orderd"] = True  # ordered 오타
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("orderd" in e for e in result.errors)


def test_forbidden_field_for_kind():
    case = valid_dql()
    case["perf"] = {"relations": [{"name": "products"}]}  # perf는 dql 허용
    assert validate_case(case, WHITELIST).ok
    case2 = valid_dql()
    case2["statement"] = "SELECT 1"  # statement는 dql 금지 → unknown으로 잡힘
    result = validate_case(case2, WHITELIST)
    assert not result.ok
    assert any("statement" in e for e in result.errors)


def test_incomplete_db_pair():
    case = valid_dml()
    del case["setup"]
    case["setup_mysql"] = "SELECT 1"  # postgres 짝 없음
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("setup" in e for e in result.errors)


def test_both_common_and_pair():
    case = valid_dml()
    case["setup_mysql"] = "SELECT 1"
    case["setup_postgres"] = "SELECT 1"  # 공통형 setup과 쌍을 함께 씀
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("함께" in e for e in result.errors)


def test_bad_id_not_kebab():
    case = valid_dql()
    case["id"] = "Limit_Pagination"
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("kebab" in e for e in result.errors)


def test_unregistered_concept():
    case = valid_dql()
    case["concepts"] = ["nonexistent-concept"]
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("미등록" in e for e in result.errors)


def test_bad_access_value():
    case = valid_dql()
    case["perf"] = {"relations": [{"name": "products", "access": "wrong"}]}
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("access" in e for e in result.errors)


def test_exclude_columns_requires_columns():
    case = valid_dql()
    case["nondeterministic"] = {"strategy": "exclude_columns"}  # columns 없음
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("columns" in e for e in result.errors)


def test_ddl_missing_object():
    case = valid_ddl()
    del case["object"]
    result = validate_case(case, WHITELIST)
    assert not result.ok
    assert any("object" in e for e in result.errors)


# --- dml pairable 규칙 (setup optional, post_query required) ---


def test_dml_without_setup_passes():
    case = {
        "id": "u",
        "kind": "dml",
        "isolation": "fresh",
        "concepts": ["limit-pagination"],
        "statement": "X",
        "post_query": "SELECT 1",
    }
    assert validate_case(case, WHITELIST).ok


def test_dml_without_post_query_fails():
    case = {
        "id": "u",
        "kind": "dml",
        "isolation": "fresh",
        "concepts": ["limit-pagination"],
        "statement": "X",
    }
    assert not validate_case(case, WHITELIST).ok


# --- 전역 규칙 ---


def test_duplicate_id():
    a = valid_dql()
    b = valid_dql()  # 같은 id
    result = validate_corpus([a, b], WHITELIST, allow_incomplete_coverage=True)
    assert not result.ok
    assert any("중복 id" in e for e in result.errors)


def test_coverage_fail_by_default():
    case = valid_dql()  # limit-pagination만 커버, 나머지 미커버
    result = validate_corpus([case], WHITELIST)
    assert not result.ok
    assert any("커버되지 않은" in e for e in result.errors)


def test_coverage_warns_when_allowed():
    case = valid_dql()
    result = validate_corpus([case], WHITELIST, allow_incomplete_coverage=True)
    assert result.ok
    assert any("커버되지 않은" in w for w in result.warnings)


# --- 실제 코퍼스 파일이 통과하는지 (통합) ---


def test_real_corpus_passes():
    root = Path(__file__).resolve().parents[1]
    whitelist = load_concepts(root / "corpus" / "concepts.yaml")
    from validate_corpus import load_cases

    files = sorted((root / "corpus" / "cases").rglob("*.yaml"))
    cases, load_result = load_cases(files)
    assert load_result.ok, load_result.errors
    result = validate_corpus(cases, whitelist)
    assert result.ok, result.errors
