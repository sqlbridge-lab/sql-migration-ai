"""코퍼스 정적 검증 CLI.

corpus/cases/**/*.yaml 케이스가 case-schema.md 규격을 지키는지 확인한다.
- 최상위 구조 / kind별 필수·금지·unknown 필드 / 제어 SQL 공통형·DB별 쌍
- 필드 값(빈 SQL, concepts 형식, id kebab-case, 허용값, perf, nondeterministic)
- 전역 ID 유일성 / concepts 화이트리스트 / 개념 커버리지

표준 라이브러리 + PyYAML만 사용한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard

import yaml

KINDS = {"dql", "dml", "ddl"}
ISOLATION_VALUES = {"fresh"}
ACCESS_VALUES = {"index_only", "index", "any"}
NONDET_STRATEGIES = {"fixed_clock", "fixed_seed", "exclude_columns"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# 제어 SQL 필드 중 "공통형 또는 DB별 쌍"으로 적을 수 있는 것들의 기준 이름.
# 실제 케이스에서는 `setup` 또는 `setup_mysql`+`setup_postgres` 형태로 나타난다.
PAIRABLE_FIELDS = {"setup", "post_query"}

# kind별 필드 규격. 공통 필드(id/kind/concepts/note)는 아래에서 따로 처리한다.
# pairable 필드는 base 이름으로 적고, 검증 시 base 또는 _mysql/_postgres 쌍을 허용한다.
COMMON_FIELDS = {"id", "kind", "concepts", "note"}


@dataclass(frozen=True)
class KindSpec:
    required: set[str]
    optional: set[str]
    # 공통형/쌍으로 허용하는 pairable 필드(예: setup, post_query)
    pairable_required: set[str] = field(default_factory=set)
    pairable_optional: set[str] = field(default_factory=set)


KIND_SPECS: dict[str, KindSpec] = {
    "dql": KindSpec(
        required={"mysql"},
        optional={"ordered", "nondeterministic", "perf"},
    ),
    "dml": KindSpec(
        required={"statement", "isolation"},
        optional={"nondeterministic", "exercise", "ordered"},
        pairable_required={"post_query"},  # 검증 대상 — 없으면 상태 비교 불가
        pairable_optional={"setup"},  # 기존 시드 재사용 케이스는 setup 불필요
    ),
    "ddl": KindSpec(
        required={"statement", "isolation", "object"},
        optional={"exercise", "ordered"},
        pairable_optional={"setup", "post_query"},
    ),
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _pair_names(base: str) -> tuple[str, str]:
    return f"{base}_mysql", f"{base}_postgres"


def _allowed_field_names(spec: KindSpec) -> set[str]:
    """이 kind에서 나타날 수 있는 모든 필드 이름(공통형·쌍 포함)."""
    names = set(COMMON_FIELDS) | spec.required | spec.optional
    for base in spec.pairable_required | spec.pairable_optional:
        names.add(base)
        mysql, postgres = _pair_names(base)
        names.add(mysql)
        names.add(postgres)
    return names


def _check_pairable(
    case: dict[str, Any],
    base: str,
    *,
    required: bool,
    ctx: str,
    result: ValidationResult,
) -> None:
    """pairable 필드가 '공통형 또는 완전한 쌍' 규칙을 지키는지 검사."""
    has_common = base in case
    mysql, postgres = _pair_names(base)
    has_mysql = mysql in case
    has_postgres = postgres in case

    if has_common and (has_mysql or has_postgres):
        result.errors.append(
            f"{ctx}: `{base}`와 `{base}_mysql`/`{base}_postgres`를 함께 쓸 수 없다"
        )
        return
    if has_mysql != has_postgres:
        result.errors.append(
            f"{ctx}: `{base}` DB별 쌍은 둘 다 있어야 한다(`{mysql}`, `{postgres}`)"
        )
        return
    if required and not (has_common or (has_mysql and has_postgres)):
        result.errors.append(
            f"{ctx}: `{base}`(공통형) 또는 `{base}_mysql`+`{base}_postgres`가 필요하다"
        )


def _nonempty_str(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and value.strip() != ""


def _check_sql_fields(case: dict[str, Any], ctx: str, result: ValidationResult) -> None:
    """SQL 성격의 필드는 비어있지 않은 문자열이어야 한다."""
    sql_like = {
        "mysql",
        "statement",
        "setup",
        "post_query",
        "exercise",
        "setup_mysql",
        "setup_postgres",
        "post_query_mysql",
        "post_query_postgres",
    }
    for name in sql_like & case.keys():
        if not _nonempty_str(case[name]):
            result.errors.append(f"{ctx}: `{name}`는 비어있지 않은 문자열이어야 한다")


def _check_concepts(
    case: dict[str, Any], whitelist: set[str], ctx: str, result: ValidationResult
) -> None:
    concepts = case.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        result.errors.append(f"{ctx}: `concepts`는 비어있지 않은 리스트여야 한다")
        return
    for c in concepts:
        if not _nonempty_str(c):
            result.errors.append(
                f"{ctx}: `concepts` 항목은 비어있지 않은 문자열이어야 한다: {c!r}"
            )
        elif c not in whitelist:
            result.errors.append(f"{ctx}: 미등록 concept `{c}` (concepts.yaml에 없음)")


def _check_object(case: dict[str, Any], ctx: str, result: ValidationResult) -> None:
    obj = case.get("object")
    if not isinstance(obj, dict):
        result.errors.append(f"{ctx}: `object`는 {{type, name}} 객체여야 한다")
        return
    for key in ("type", "name"):
        if not _nonempty_str(obj.get(key)):
            result.errors.append(
                f"{ctx}: `object.{key}`가 비어있지 않은 문자열이어야 한다"
            )


def _check_nondeterministic(
    case: dict[str, Any], ctx: str, result: ValidationResult
) -> None:
    nd = case.get("nondeterministic")
    if nd is None:
        return
    if not isinstance(nd, dict):
        result.errors.append(f"{ctx}: `nondeterministic`는 객체여야 한다")
        return
    strategy = nd.get("strategy")
    if strategy not in NONDET_STRATEGIES:
        result.errors.append(
            f"{ctx}: `nondeterministic.strategy`는 {sorted(NONDET_STRATEGIES)} 중 하나여야 한다"
        )
    if strategy == "exclude_columns":
        cols = nd.get("columns")
        if not isinstance(cols, list) or not cols:
            result.errors.append(
                f"{ctx}: `exclude_columns` 전략은 비어있지 않은 `columns` 리스트가 필요하다"
            )


def _check_perf(case: dict[str, Any], ctx: str, result: ValidationResult) -> None:
    perf = case.get("perf")
    if perf is None:
        return
    relations = perf.get("relations") if isinstance(perf, dict) else None
    if not isinstance(relations, list) or not relations:
        result.errors.append(f"{ctx}: `perf.relations`는 비어있지 않은 리스트여야 한다")
        return
    for i, rel in enumerate(relations):
        rctx = f"{ctx}: perf.relations[{i}]"
        if not isinstance(rel, dict):
            result.errors.append(f"{rctx}: relation 항목은 객체여야 한다")
            continue
        if not _nonempty_str(rel.get("name")):
            result.errors.append(f"{rctx}: `name`이 필요하다")
        if "access" in rel and rel["access"] not in ACCESS_VALUES:
            result.errors.append(
                f"{rctx}: `access`는 {sorted(ACCESS_VALUES)} 중 하나여야 한다"
            )
        if "forbid_full_scan" in rel and not isinstance(rel["forbid_full_scan"], bool):
            result.errors.append(f"{rctx}: `forbid_full_scan`은 bool이어야 한다")
        if "max_examined_rows" in rel and not isinstance(rel["max_examined_rows"], int):
            result.errors.append(f"{rctx}: `max_examined_rows`는 정수여야 한다")


def validate_case(case: Any, whitelist: set[str]) -> ValidationResult:
    """케이스 하나를 검증한다. (파일 I/O 없음 — 테스트에서 dict를 직접 넘길 수 있다.)"""
    result = ValidationResult()

    if not isinstance(case, dict):
        result.errors.append(f"케이스는 매핑이어야 한다: {case!r}")
        return result

    case_id = case.get("id")
    ctx = f"case `{case_id}`" if _nonempty_str(case_id) else "case <id 없음>"

    # id 형식
    if not _nonempty_str(case_id):
        result.errors.append(f"{ctx}: `id`가 필요하다")
    elif not ID_PATTERN.match(case_id):
        result.errors.append(f"{ctx}: `id`는 kebab-case여야 한다")

    kind = case.get("kind")
    if kind not in KINDS:
        result.errors.append(
            f"{ctx}: `kind`는 {sorted(KINDS)} 중 하나여야 한다: {kind!r}"
        )
        return result  # kind를 모르면 이후 필드 검증 불가

    spec = KIND_SPECS[kind]
    present = set(case.keys())

    # unknown field
    allowed = _allowed_field_names(spec)
    for name in present - allowed:
        result.errors.append(f"{ctx}: 알 수 없는 필드 `{name}`")

    # 필수(비 pairable) 필드
    for name in spec.required:
        if name not in case:
            result.errors.append(f"{ctx}: 필수 필드 `{name}` 누락")

    # 금지 필드 = allowed에 없는 것 중 다른 kind의 필드 → unknown으로 이미 잡힘.
    # pairable 규칙
    for base in spec.pairable_required:
        _check_pairable(case, base, required=True, ctx=ctx, result=result)
    for base in spec.pairable_optional:
        _check_pairable(case, base, required=False, ctx=ctx, result=result)

    # isolation 허용값
    if "isolation" in case and case["isolation"] not in ISOLATION_VALUES:
        result.errors.append(f"{ctx}: `isolation`은 {sorted(ISOLATION_VALUES)}만 허용")

    # ordered 타입
    if "ordered" in case and not isinstance(case["ordered"], bool):
        result.errors.append(f"{ctx}: `ordered`는 bool이어야 한다")

    # object (ddl)
    if "object" in case:
        _check_object(case, ctx, result)

    _check_sql_fields(case, ctx, result)
    _check_concepts(case, whitelist, ctx, result)
    _check_nondeterministic(case, ctx, result)
    _check_perf(case, ctx, result)

    return result


def load_concepts(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {c["id"] for c in data["concepts"]}


def load_cases(case_files: list[Path]) -> tuple[list[dict[str, Any]], ValidationResult]:
    """모든 케이스 파일을 읽어 케이스 리스트로 편다. 최상위 구조 오류도 모은다."""
    result = ValidationResult()
    cases: list[dict[str, Any]] = []
    for f in case_files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or list(data.keys()) != ["cases"]:
            result.errors.append(f"{f}: 최상위는 정확히 `cases:` 리스트 하나여야 한다")
            continue
        if not isinstance(data["cases"], list):
            result.errors.append(f"{f}: `cases`는 리스트여야 한다")
            continue
        cases.extend(data["cases"])
    return cases, result


def validate_corpus(
    cases: list[Any],
    whitelist: set[str],
    *,
    allow_incomplete_coverage: bool = False,
) -> ValidationResult:
    """케이스 리스트 전체를 검증한다(개별 케이스 + 전역 규칙)."""
    result = ValidationResult()

    seen_ids: set[str] = set()
    covered: set[str] = set()
    for case in cases:
        r = validate_case(case, whitelist)
        result.errors.extend(r.errors)
        result.warnings.extend(r.warnings)

        if isinstance(case, dict):
            cid = case.get("id")
            if _nonempty_str(cid):
                if cid in seen_ids:
                    result.errors.append(f"중복 id `{cid}`")
                seen_ids.add(cid)
            concepts = case.get("concepts")
            if isinstance(concepts, list):
                covered.update(
                    c for c in concepts if isinstance(c, str) and c in whitelist
                )

    # 커버리지
    missing = whitelist - covered
    if missing:
        msg = f"커버되지 않은 개념: {sorted(missing)}"
        if allow_incomplete_coverage:
            result.warnings.append(msg)
        else:
            result.errors.append(msg)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="코퍼스 케이스 정적 검증")
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=Path("corpus/cases"),
        help="케이스 YAML 루트 (기본: corpus/cases)",
    )
    parser.add_argument(
        "--concepts",
        type=Path,
        default=Path("corpus/concepts.yaml"),
        help="개념 화이트리스트 (기본: corpus/concepts.yaml)",
    )
    parser.add_argument(
        "--allow-incomplete-coverage",
        action="store_true",
        help="개념 커버리지 미달을 실패가 아닌 경고로 처리",
    )
    args = parser.parse_args(argv)

    whitelist = load_concepts(args.concepts)
    case_files = sorted(args.cases_dir.rglob("*.yaml"))
    cases, load_result = load_cases(case_files)

    result = validate_corpus(
        cases, whitelist, allow_incomplete_coverage=args.allow_incomplete_coverage
    )
    result.errors = load_result.errors + result.errors

    for w in result.warnings:
        print(f"[warn] {w}")
    for e in result.errors:
        print(f"[error] {e}")

    if result.ok:
        print(f"OK: 케이스 {len(cases)}개, 개념 {len(whitelist)}개 검증 통과")
        return 0
    print(f"FAIL: 오류 {len(result.errors)}건")
    return 1


if __name__ == "__main__":
    sys.exit(main())
