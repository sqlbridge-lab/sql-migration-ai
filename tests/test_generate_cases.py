"""케이스 생성기(tools/generate_cases.py) 검증 — DB 불필요 묶음.

결정성·유일성·토큰 보존·정적 검증·분포 하한·원자성·--out 가드를 확인한다.
DB가 필요한 하니스 게이트·seed preflight는 test_generate_corpus_integration.py에 둔다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from generate_cases import (
    MIN_PER_CONCEPT,
    OBJECT_NAME_PLACEHOLDER,
    PERF_CONCEPTS,
    Axis,
    GeneratedCase,
    Template,
    all_templates,
    check_distribution,
    check_out_guard,
    count_by_concept,
    dump_cases_yaml,
    expand_template,
    generate_all,
    group_by_base_id,
    load_golden,
    make_id,
    render_distribution,
    safe_id_part,
    write_corpus_atomic,
)
from validate_corpus import ID_PATTERN, load_concepts, validate_corpus

GOLDEN_DIR = _ROOT / "corpus" / "cases"
CONCEPTS_PATH = _ROOT / "corpus" / "concepts.yaml"


@pytest.fixture(scope="module")
def whitelist() -> set[str]:
    return load_concepts(CONCEPTS_PATH)


@pytest.fixture(scope="module")
def golden(whitelist: set[str]) -> dict:
    return load_golden(sorted(GOLDEN_DIR.rglob("*.yaml")), whitelist)


# --- safe_id_part / make_id ---


def test_safe_id_part_kebab() -> None:
    assert safe_id_part("Product 0042") == "product-0042"
    assert safe_id_part(10) == "10"
    assert safe_id_part("a__b--c") == "a-b-c"


def test_safe_id_part_tuple_joins_parts() -> None:
    assert safe_id_part(("products", "id, name", "id")) == "products-id-name-id"


def test_make_id_deterministic_and_pattern() -> None:
    combo = {"count": 5, "offset": 10}
    case_id = make_id("limit-pagination", combo, ["count", "offset"])
    assert case_id == "limit-pagination-5-10"
    assert ID_PATTERN.match(case_id)


# --- expand_template: 더미 템플릿(축 2개) ---


def _dummy_build(_base: dict, combo: dict) -> dict:
    return {"mysql": f"SELECT {combo['a']} + {combo['b']}"}


def test_expand_dummy_two_axes(golden: dict) -> None:
    # limit-pagination을 base로 빌려 kind·concepts를 재사용한다(내용은 무관, 골격 검증).
    template = Template(
        base_id="limit-pagination",
        axes=[Axis("a", [1, 2, 3]), Axis("b", [10, 20])],
        build=_dummy_build,
    )
    cases = expand_template(template, golden)
    # 3 × 2 = 6 조합.
    assert len(cases) == 6
    ids = [c.id for c in cases]
    assert ids == [
        "limit-pagination-1-10",
        "limit-pagination-1-20",
        "limit-pagination-2-10",
        "limit-pagination-2-20",
        "limit-pagination-3-10",
        "limit-pagination-3-20",
    ]
    # 모든 id가 ID_PATTERN 통과.
    assert all(ID_PATTERN.match(c.id) for c in cases)


def test_expand_reuses_golden_kind_and_concepts(golden: dict) -> None:
    template = Template(
        base_id="bool-tinyint",  # golden id는 bool-tinyint, concept은 tinyint-bool.
        axes=[Axis("v", [0, 1])],
        build=lambda _base, combo: {"mysql": f"SELECT {combo['v']}"},
    )
    cases = expand_template(template, golden)
    for c in cases:
        # kind·concepts는 golden에서 온 값. build가 만들지 않는다.
        assert c.data["kind"] == "dql"
        assert c.data["concepts"] == ["tinyint-bool"]
        assert c.concept == "tinyint-bool"


def test_expand_valid_predicate_filters(golden: dict) -> None:
    template = Template(
        base_id="limit-pagination",
        axes=[Axis("n", [1, 2, 3, 4])],
        build=lambda _base, combo: {"mysql": f"SELECT {combo['n']}"},
        valid=lambda combo: combo["n"] % 2 == 0,  # 짝수만.
    )
    cases = expand_template(template, golden)
    assert [c.data["mysql"] for c in cases] == ["SELECT 2", "SELECT 4"]


def test_expand_structured_axis_tuple_in_id(golden: dict) -> None:
    template = Template(
        base_id="backtick-identifier",
        axes=[Axis("combo", [("products", "id, name", "id")])],
        build=lambda _base, _combo: {"mysql": "SELECT 1"},
    )
    cases = expand_template(template, golden)
    assert cases[0].id == "backtick-identifier-products-id-name-id"
    assert ID_PATTERN.match(cases[0].id)


def test_expand_unknown_base_id_raises(golden: dict) -> None:
    template = Template(
        base_id="does-not-exist",
        axes=[Axis("n", [1])],
        build=lambda _base, _combo: {"mysql": "SELECT 1"},
    )
    with pytest.raises(ValueError, match="base_id"):
        expand_template(template, golden)


# --- 태스크2: syntax 9개 템플릿 축 정의 ---


@pytest.fixture(scope="module")
def generated(golden: dict) -> list[GeneratedCase]:
    """9개 템플릿을 모두 전개한 케이스(모듈 스코프로 한 번만 계산)."""
    cases: list[GeneratedCase] = []
    for template in all_templates():
        cases.extend(expand_template(template, golden))
    return cases


def test_nine_templates_present() -> None:
    base_ids = {t.base_id for t in all_templates()}
    assert base_ids == {
        "limit-pagination",
        "ifnull-coalesce",
        "backtick-identifier",
        "date-function",
        "enum-type",
        "bool-tinyint",
        "unsigned-type",
        "upsert-on-duplicate",
        "auto-increment",
    }


def test_all_generated_pass_static_validation(
    generated: list[GeneratedCase], whitelist: set[str]
) -> None:
    result = validate_corpus(
        [c.data for c in generated], whitelist, allow_incomplete_coverage=True
    )
    assert result.ok, result.errors[:10]


def test_generated_ids_unique_and_pattern(generated: list[GeneratedCase]) -> None:
    ids = [c.id for c in generated]
    assert len(ids) == len(set(ids)), "생성 id 충돌"
    assert all(ID_PATTERN.match(i) for i in ids)


def test_backtick_no_invalid_table_column_combo(
    generated: list[GeneratedCase],
) -> None:
    # orders.name 같은 무효 조합이 없어야 한다(구조화 축으로 실재 조합만 나열).
    backtick = [c for c in generated if c.concept == "backtick-identifier"]
    for c in backtick:
        sql = c.data["mysql"]
        # orders/payments/reviews/users 테이블에 없는 컬럼을 인용하지 않는다.
        assert "`name`" not in sql or "FROM `products`" in sql


def test_offset_within_seed_range(generated: list[GeneratedCase]) -> None:
    # limit-pagination의 offset은 products 씨드 행 수(1000) 미만이어야 한다.
    limit_cases = [c for c in generated if c.concept == "limit-pagination"]
    for c in limit_cases:
        m = re.search(r"LIMIT (\d+), (\d+)", c.data["mysql"])
        assert m is not None
        offset = int(m.group(1))
        assert offset < 1000


def test_date_function_keeps_fixed_clock(generated: list[GeneratedCase]) -> None:
    date_cases = [c for c in generated if c.concept == "date-function"]
    assert date_cases
    for c in date_cases:
        assert c.data["nondeterministic"] == {"strategy": "fixed_clock"}


def test_upsert_reuses_seed_rows_no_delete(generated: list[GeneratedCase]) -> None:
    # dml은 기존 시드 행 재사용, DELETE/TRUNCATE 없음(격리 계약).
    upsert = [c for c in generated if c.concept == "upsert-on-duplicate"]
    assert upsert
    for c in upsert:
        stmt = c.data["statement"].upper()
        assert "DELETE" not in stmt and "TRUNCATE" not in stmt
        assert "ON DUPLICATE KEY UPDATE" in stmt


# --- 태스크3: {{object_name}} 토큰 전용 검증(P1-1, 리뷰 2차 #4) ---

# 2겹으로 감싸이지 않은 홑중괄호(=이스케이프가 깨진 placeholder)를 잡는 정규식.
# `{{object_name}}`은 매치되지 않고, `{object_name}`(1겹)만 매치된다.
_LONE_BRACE = re.compile(r"(?<!\{)\{[^{}]*\}(?!\})")

# ddl의 placeholder가 들어가는 필드들(runner가 치환하는 대상).
_DDL_TOKEN_FIELDS = ("statement", "exercise", "post_query")


def test_placeholder_constant_is_double_braced() -> None:
    # 상수 자체가 정확히 2겹 토큰이어야 한다(f-string 이스케이프 실수 방지).
    assert OBJECT_NAME_PLACEHOLDER == "{{object_name}}"


def test_ddl_keeps_exact_double_brace_token(generated: list[GeneratedCase]) -> None:
    ddl = [c for c in generated if c.data["kind"] == "ddl"]
    assert ddl
    for c in ddl:
        for field in _DDL_TOKEN_FIELDS:
            sql = c.data[field]
            # (1) 정확한 2겹 토큰이 최소 한 번 남아 있다.
            assert OBJECT_NAME_PLACEHOLDER in sql, (c.id, field)
            # (2-a) 2겹 토큰을 전부 제거한 잔여엔 중괄호가 없다.
            residual = sql.replace(OBJECT_NAME_PLACEHOLDER, "")
            assert "{" not in residual and "}" not in residual, (c.id, field)
            # (2-b) 2겹으로 안 감싸인 홑중괄호가 매치되지 않는다(이스케이프 깨짐 없음).
            assert _LONE_BRACE.search(sql) is None, (c.id, field)


# --- 태스크4: 개념 분포 fail-closed(P1-4) ---


@pytest.fixture(scope="module")
def all_generated(golden: dict) -> list[GeneratedCase]:
    """generate_all(전역 유일성 검사 포함)로 만든 전체 케이스."""
    return generate_all(golden)


def test_min_per_concept_keys_are_syntax_concepts() -> None:
    # MIN_PER_CONCEPT는 syntax 9개 concept만 담고 perf는 제외한다.
    assert set(MIN_PER_CONCEPT) & PERF_CONCEPTS == set()
    assert len(MIN_PER_CONCEPT) == 9


def test_distribution_meets_all_lower_bounds(
    all_generated: list[GeneratedCase],
) -> None:
    counts = count_by_concept(all_generated)
    # 실산출이 하한을 모두 만족(fail-closed 통과)하고 총합 ≥ 1000.
    assert check_distribution(counts, 1000) == []
    assert sum(counts.values()) >= 1000


def test_distribution_flags_under_min() -> None:
    # 한 개념이 하한 미달이면 check_distribution이 오류를 낸다(fail-closed).
    counts = {c: lo for c, lo in MIN_PER_CONCEPT.items()}
    counts["limit-pagination"] = 1  # 하한(66) 미달로 조작.
    errors = check_distribution(counts, 1)
    assert any("limit-pagination" in e for e in errors)


def test_distribution_flags_under_min_cases(
    all_generated: list[GeneratedCase],
) -> None:
    counts = count_by_concept(all_generated)
    # 총합보다 큰 min-cases를 요구하면 실패.
    errors = check_distribution(counts, 100000)
    assert any("총 케이스 미달" in e for e in errors)


def test_render_distribution_lists_all_concepts(
    all_generated: list[GeneratedCase],
) -> None:
    counts = count_by_concept(all_generated)
    md = render_distribution(counts, 1000)
    for concept in MIN_PER_CONCEPT:
        assert concept in md
    assert "합계" in md


# --- 태스크5: 전역 유일성(golden+generated)·결정성 ---


def test_generate_all_ids_globally_unique(
    all_generated: list[GeneratedCase], golden: dict
) -> None:
    gen_ids = {c.id for c in all_generated}
    golden_ids = set(golden.keys())
    # 생성 id끼리 유일하고, golden id와도 겹치지 않는다(합집합 유일).
    assert len(gen_ids) == len(all_generated)
    assert gen_ids & golden_ids == set()


def test_generate_all_rejects_collision_with_generated(golden: dict) -> None:
    # 두 템플릿이 같은 id를 내도록 만들면 충돌로 실패한다(조용한 접미사 금지).
    dup = Template(
        base_id="limit-pagination",
        axes=[Axis("n", [1])],
        build=lambda _b, _c: {"mysql": "SELECT 1"},
    )
    with pytest.raises(ValueError, match="id 충돌"):
        generate_all(golden, templates=[dup, dup])


def test_generate_all_rejects_collision_with_golden(golden: dict) -> None:
    # 축이 없어 생성 id가 base_id(=golden id)와 그대로 겹치면 실패한다.
    clashing = Template(
        base_id="limit-pagination",
        axes=[],  # 축 없음 → id는 "limit-pagination"으로 golden과 충돌.
        build=lambda _b, _c: {"mysql": "SELECT 1"},
    )
    with pytest.raises(ValueError, match="id 충돌"):
        generate_all(golden, templates=[clashing])


def test_generation_is_byte_identical(golden: dict) -> None:
    # 난수 미사용 → 두 번 생성이 완전히 동일한 바이트열(재실행 diff 없음).
    first = dump_cases_yaml(generate_all(golden))
    second = dump_cases_yaml(generate_all(golden))
    assert first == second


# --- 태스크6: 원자적 산출 + --out 가드(P1-5) ---


@pytest.mark.parametrize(
    "rel",
    [
        "corpus/cases",  # golden 자체.
        "corpus/cases/syntax",  # golden 하위.
        "corpus",  # golden 상위.
    ],
)
def test_out_guard_rejects_golden_paths(rel: str) -> None:
    with pytest.raises(ValueError, match="golden"):
        check_out_guard(_ROOT / rel)


def test_out_guard_allows_generated_path() -> None:
    # corpus/generated는 golden과 안 겹치므로 통과(예외 없음).
    check_out_guard(_ROOT / "corpus" / "generated")


def test_write_atomic_creates_tree(
    tmp_path: Path, all_generated: list[GeneratedCase], whitelist: set[str]
) -> None:
    out = tmp_path / "generated"
    counts = count_by_concept(all_generated)
    write_corpus_atomic(
        out, all_generated, whitelist, render_distribution(counts, 1000)
    )
    assert (out / "DISTRIBUTION.md").exists()
    # 개념(=base_id)별 파일이 하나씩 생긴다.
    yaml_files = sorted((out / "syntax").glob("*.yaml"))
    assert len(yaml_files) == len(group_by_base_id(all_generated))


def test_write_atomic_no_partial_on_invalid(
    tmp_path: Path, all_generated: list[GeneratedCase], whitelist: set[str]
) -> None:
    out = tmp_path / "generated"
    # 먼저 정상 산출(교체 대상 존재).
    counts = count_by_concept(all_generated)
    write_corpus_atomic(
        out, all_generated, whitelist, render_distribution(counts, 1000)
    )
    before = dump_cases_yaml(all_generated)

    # 검증 실패 케이스(빈 mysql)를 섞으면 재로드 검증에서 실패해 out이 안 바뀐다.
    broken = GeneratedCase(
        id="broken-case",
        base_id="limit-pagination",
        concept="limit-pagination",
        data={
            "id": "broken-case",
            "kind": "dql",
            "concepts": ["limit-pagination"],
            "mysql": "",  # 빈 SQL → validate_corpus 실패.
        },
    )
    with pytest.raises(ValueError, match="정적 검증 실패"):
        write_corpus_atomic(out, [*all_generated, broken], whitelist, "dummy")
    # out은 이전 정상 산출 그대로(부분 산출 없음).
    after_files = sorted((out / "syntax").glob("*.yaml"))
    assert after_files  # 여전히 존재.
    # limit-pagination 파일 내용이 broken을 포함하지 않는다.
    limit_text = (out / "syntax" / "limit-pagination.yaml").read_text(encoding="utf-8")
    assert "broken-case" not in limit_text
    assert "broken-case" not in before


def test_write_atomic_removes_stale_files(
    tmp_path: Path, all_generated: list[GeneratedCase], whitelist: set[str]
) -> None:
    out = tmp_path / "generated"
    counts = count_by_concept(all_generated)
    md = render_distribution(counts, 1000)
    write_corpus_atomic(out, all_generated, whitelist, md)
    # 재실행 전 stale 파일을 심어둔다.
    stale = out / "syntax" / "stale.yaml"
    stale.write_text("cases: []\n", encoding="utf-8")
    # 재실행하면 디렉터리 단위 교체로 stale이 사라진다.
    write_corpus_atomic(out, all_generated, whitelist, md)
    assert not stale.exists()
