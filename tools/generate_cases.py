"""케이스 생성기 — golden 템플릿을 축(axis) 곱집합으로 벌려 대량 케이스를 찍는다.

파라미터화는 **코드 축 조립** 방식이다(설계 스펙 참조). golden YAML은 kind·concepts의
단일 원본이고, 생성 SQL은 코드(`build`)가 원본이다. 생성기는 golden을 `base_id`로 로드해
kind·concepts를 케이스 dict에 한 번 주입하고, `build`는 바뀌는 SQL·제어 필드만 반환한다.

표준 라이브러리 + PyYAML만 쓴다(validate_corpus와 같은 의존성 정책).
"""

from __future__ import annotations

import argparse
import itertools
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# tools/는 패키지가 아니라 스크립트 디렉터리라, validate_corpus를 sys.path로 임포트한다
# (loader.py와 같은 관행).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_corpus import (
    ID_PATTERN,
    load_cases,
    load_concepts,
    validate_corpus,
)

# 하니스가 정확히 이 토큰(중괄호 2겹)만 치환한다(runner.py:294). 파이썬 f-string에서
# `f"{{object_name}}"`는 `{object_name}`(1겹)을 내 토큰을 깨므로, 이 상수를 문자열로
# 삽입한다(f-string 안에 쓰면 `{{{{object_name}}}}`로 이스케이프해야 이 값이 나온다).
OBJECT_NAME_PLACEHOLDER = "{{object_name}}"


@dataclass(frozen=True)
class Axis:
    """바꿀 축 하나. values의 각 원소는 스칼라 또는 튜플(구조화 축)이다.

    Java로 치면 불변 record. `values`는 List<Object>이고, 각 원소가 축이 취하는 값.
    """

    name: str
    values: list[Any]


# build(base_case, combination) → 바뀌는 필드만. kind·concepts·id는 만들지 않는다.
BuildFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
# 곱집합 조합의 유효성 predicate. combination을 받아 유효하면 True.
ValidFn = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class Template:
    """한 golden 템플릿을 어떻게 벌릴지 정의한다.

    build/valid는 함수를 필드로 담는다(Java의 Function<...> 필드와 유사).
    """

    base_id: str  # golden 케이스 id. 여기서 kind·concepts를 로드해 재사용.
    axes: list[Axis]
    build: BuildFn
    valid: ValidFn | None = None  # None이면 모든 조합이 유효.


def safe_id_part(value: Any) -> str:
    """축 값 하나를 kebab-case 조각으로 안전화한다.

    소문자화 → 비영숫자를 `-`로 → 연속 `-` 축약 → 양끝 `-` 제거.
    튜플이면 성분을 순서대로 안전화해 이어붙인다(구조화 축).
    """
    if isinstance(value, tuple):
        return "-".join(safe_id_part(v) for v in value)
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def make_id(base_id: str, combination: dict[str, Any], axis_order: list[str]) -> str:
    """`{base_id}-{axis1}-{axis2}-...` 형식의 결정적 id를 만든다."""
    parts = [base_id]
    parts.extend(safe_id_part(combination[name]) for name in axis_order)
    return "-".join(p for p in parts if p)


@dataclass
class GeneratedCase:
    """생성된 케이스 하나(파일에 dump되기 전의 dict 표현을 감싼다)."""

    id: str
    base_id: str  # 어느 템플릿에서 나왔나 — 개념별 파일 분리·그룹핑용.
    concept: str  # 개념 분포 집계용(케이스의 유일 concept). concepts[0]과 같다.
    data: dict[str, Any]  # YAML로 dump될 케이스 dict


def load_golden(
    case_files: list[Path], whitelist: set[str]
) -> dict[str, dict[str, Any]]:
    """golden 케이스를 base_id로 인덱싱해 반환한다.

    정적 검증을 통과한 golden만 신뢰한다(형식 오류가 있으면 예외로 실패).
    """
    raws, load_result = load_cases(case_files)
    result = validate_corpus(raws, whitelist)
    errors = load_result.errors + result.errors
    if errors:
        raise ValueError("golden 형식 검증 실패:\n" + "\n".join(errors))
    return {raw["id"]: raw for raw in raws}


def expand_template(
    template: Template, golden: dict[str, dict[str, Any]]
) -> list[GeneratedCase]:
    """한 템플릿을 축 곱집합으로 전개해 GeneratedCase 리스트를 만든다.

    - kind·concepts는 golden(base_id)에서 가져와 생성기가 한 번 주입(단일 원본).
    - build는 base_case(참조)와 combination(축 값)을 받아 바뀌는 필드만 반환.
    - id는 축 조합으로 결정적 부여. valid predicate로 무효 조합을 버린다.
    """
    base_case = golden.get(template.base_id)
    if base_case is None:
        raise ValueError(f"golden에 base_id `{template.base_id}`가 없다")
    kind = base_case["kind"]
    concepts = base_case["concepts"]

    axis_order = [axis.name for axis in template.axes]
    cases: list[GeneratedCase] = []
    # itertools.product는 축 값들의 데카르트 곱을 결정적 순서로 낸다(자바 스트림 flatMap
    # 중첩과 유사하지만 순서가 고정). 축이 없으면 빈 곱집합 = 조합 1개(빈 dict).
    for combo_values in itertools.product(*(axis.values for axis in template.axes)):
        combination = dict(zip(axis_order, combo_values, strict=True))
        if template.valid is not None and not template.valid(combination):
            continue
        changed = template.build(base_case, combination)
        case_data: dict[str, Any] = {
            "id": make_id(template.base_id, combination, axis_order),
            "kind": kind,
            "concepts": list(concepts),
            **changed,
        }
        cases.append(
            GeneratedCase(case_data["id"], template.base_id, concepts[0], case_data)
        )
    return cases


# ---------------------------------------------------------------------------
# syntax 9개 템플릿 축 정의
#
# 축 값은 씨드 결정적 범위 안(02-seed.sql)으로 제한한다 — 생성 SQL이 실제 실행돼야
# error==0이기 때문. 씨드 범위 표는 설계 스펙 "씨드 범위에 파라미터 묶기" 참조.
# build는 SQL·제어·ordered·nondeterministic 등 바뀌는 필드만 반환한다(kind·concepts는
# 생성기가 golden에서 주입).
# ---------------------------------------------------------------------------

# 공통 LIMIT count 후보(작은 페이지 크기들).
_LIMIT_COUNTS = [1, 5, 10, 20, 50, 100]

# 비교 연산자: id-안전 slug → SQL 기호. 기호를 그대로 id에 넣으면 kebab화 시 빈
# 문자열이 돼 케이스 id가 충돌하므로, 축 값은 slug로 두고 여기서 SQL로 매핑한다.
_OP_SQL = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


def _limit_template() -> Template:
    """limit-pagination: `LIMIT offset, count` 두-인자 문법(MySQL 고유).

    count(6) × offset(24, 씨드 1000 미만) = 144 ≥ 66.
    """
    # offset은 products(1000행) 미만. 경계 0 포함. 넉넉히 벌려 총 min-cases에 기여.
    offsets = [
        0,
        1,
        2,
        5,
        10,
        20,
        30,
        50,
        70,
        100,
        150,
        200,
        250,
        300,
        350,
        400,
        500,
        600,
        700,
        800,
        850,
        900,
        950,
        999,
    ]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        count, offset = combo["count"], combo["offset"]
        return {
            "ordered": True,
            "mysql": (
                f"SELECT id, name\nFROM products\nORDER BY id\n"
                f"LIMIT {offset}, {count}\n"
            ),
        }

    return Template(
        base_id="limit-pagination",
        axes=[Axis("count", _LIMIT_COUNTS), Axis("offset", offsets)],
        build=build,
    )


def _ifnull_template() -> Template:
    """ifnull-coalesce: IFNULL(nullable, replacement).

    (table, col, replacement)를 구조화 축으로 묶는다 — replacement가 컬럼 타입에
    종속되므로(paid_at=timestamp, parent_id=정수) 독립 축이면 타입 불일치가 난다.
    컬럼 2개 × 리터럴 6 = 12튜플 × LIMIT(8) = 96 ≥ 66.
    """
    # payments.paid_at은 paid=0 행에서 NULL, categories.parent_id는 루트 5행에서 NULL.
    ts = "TIMESTAMP '{v}'"
    paid_at_reps = [ts.format(v=v) for v in _IFNULL_TS_LITERALS]
    parent_reps = [str(v) for v in _IFNULL_INT_LITERALS]
    col_reps = [("payments", "paid_at", r) for r in paid_at_reps] + [
        ("categories", "parent_id", r) for r in parent_reps
    ]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        table, col, rep = combo["col_rep"]
        limit = combo["limit"]
        return {
            "ordered": True,
            "mysql": (
                f"SELECT id, IFNULL({col}, {rep}) AS effective\n"
                f"FROM {table}\nORDER BY id\nLIMIT {limit}\n"
            ),
        }

    return Template(
        base_id="ifnull-coalesce",
        axes=[
            Axis("col_rep", col_reps),
            Axis("limit", [1, 5, 10, 20, 50, 100, 150, 200]),
        ],
        build=build,
    )


# ifnull 대체 리터럴 — 컬럼 타입별로 6개씩.
_IFNULL_TS_LITERALS = [
    "2000-01-01 00:00:00",
    "2020-06-15 12:30:00",
    "2025-01-01 00:00:00",
    "2025-02-04 00:00:00",
    "1999-12-31 23:59:59",
    "2100-01-01 00:00:00",
]
_IFNULL_INT_LITERALS = [0, 1, 5, 10, 20, 100]


def _backtick_template() -> Template:
    """backtick-identifier: 백틱 식별자 인용(MySQL 고유).

    (table, projection, order_col) 구조화 축으로 실재 조합만 나열(P1-3). LIMIT과 곱.
    실재 조합 ≥8 × LIMIT(6) = ≥48 ≥ 40.
    """
    # 실재하는 (테이블, 인용할 컬럼들, 정렬 컬럼) 조합만. orders.name 같은 무효 조합 배제.
    combos = [
        ("products", ["id", "name"], "id"),
        ("products", ["id", "price"], "id"),
        ("products", ["id", "stock"], "id"),
        ("orders", ["id", "status"], "id"),
        ("orders", ["id", "user_id"], "id"),
        ("orders", ["id", "total"], "id"),
        ("payments", ["id", "method"], "id"),
        ("payments", ["id", "paid"], "id"),
        ("reviews", ["id", "rating"], "id"),
        ("users", ["id", "email"], "id"),
    ]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        table, cols, order_col = combo["combo"]
        limit = combo["limit"]
        proj = ", ".join(f"`{c}`" for c in cols)
        return {
            "ordered": True,
            "mysql": (
                f"SELECT {proj}\nFROM `{table}`\n"
                f"ORDER BY `{order_col}`\nLIMIT {limit}\n"
            ),
        }

    return Template(
        base_id="backtick-identifier",
        axes=[Axis("combo", combos), Axis("limit", _LIMIT_COUNTS)],
        build=build,
    )


def _date_function_template() -> Template:
    """date-function: DATE_ADD/DATE_SUB 등 날짜 함수 + NOW() 비결정(fixed_clock 유지).

    날짜함수(3) × INTERVAL(5) × LIMIT(5) = 75 ≥ 66.
    """
    # 셋 다 `func(date, INTERVAL ...)` 형식(ADDDATE는 DATE_ADD 동의어). 3 × 5 × 8 = 120.
    funcs = ["DATE_ADD", "DATE_SUB", "ADDDATE"]
    intervals = [
        "INTERVAL 1 DAY",
        "INTERVAL 7 DAY",
        "INTERVAL 1 MONTH",
        "INTERVAL 12 HOUR",
        "INTERVAL 30 MINUTE",
    ]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        func, interval, limit = combo["func"], combo["interval"], combo["limit"]
        return {
            "ordered": True,
            "nondeterministic": {"strategy": "fixed_clock"},
            "mysql": (
                f"SELECT id, {func}(ordered_at, {interval}) AS due_at\n"
                f"FROM orders\nWHERE ordered_at < NOW()\n"
                f"ORDER BY id\nLIMIT {limit}\n"
            ),
        }

    return Template(
        base_id="date-function",
        axes=[
            Axis("func", funcs),
            Axis("interval", intervals),
            Axis("limit", [1, 5, 10, 20, 50, 100, 150, 200]),
        ],
        build=build,
    )


def _enum_template() -> Template:
    """enum-type: orders.status ENUM 리터럴 비교.

    status(5) × 비교연산(2: =, !=) × LIMIT(10) = 100 ≥ 66.
    """
    statuses = ["pending", "paid", "shipped", "delivered", "cancelled"]
    # op 축은 id-안전 slug. build에서 SQL 연산자로 매핑한다(기호는 kebab화하면
    # 빈 문자열이 돼 id가 충돌하므로 slug를 쓴다).
    op_slugs = ["eq", "ne"]
    limits = [1, 5, 10, 20, 30, 50, 70, 100, 150, 200]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        status, op, limit = combo["status"], _OP_SQL[combo["op"]], combo["limit"]
        return {
            "ordered": True,
            "mysql": (
                f"SELECT id, status\nFROM orders\n"
                f"WHERE status {op} '{status}'\n"
                f"ORDER BY id\nLIMIT {limit}\n"
            ),
        }

    return Template(
        base_id="enum-type",
        axes=[Axis("status", statuses), Axis("op", op_slugs), Axis("limit", limits)],
        build=build,
    )


def _bool_tinyint_template() -> Template:
    """tinyint-bool: TINYINT(1)=bool 필터.

    paid(2: 0/1) × 대상컬럼(3) × LIMIT(12) = 72 ≥ 40.
    """
    # payments의 bool성 필터 3가지 형태(paid 컬럼 하나지만 비교 리터럴/표현을 벌린다).
    # pred 축은 id-안전 slug. isnull은 paid_at의 NULL 여부로 bool을 우회 관찰한다.
    pred_slugs = ["eq", "ne", "isnull"]
    limits = [1, 5, 10, 20, 30, 50, 70, 100, 150, 200, 300, 500]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        v, pred, limit = combo["v"], combo["pred"], combo["limit"]
        if pred == "isnull":
            # paid=1 ↔ paid_at NOT NULL(씨드 규칙). bool을 nullable 컬럼으로 관찰.
            where = "paid_at IS NULL" if v == 0 else "paid_at IS NOT NULL"
        else:
            where = f"paid {_OP_SQL[pred]} {v}"
        return {
            "ordered": True,
            "mysql": (
                f"SELECT id, paid\nFROM payments\nWHERE {where}\n"
                f"ORDER BY id\nLIMIT {limit}\n"
            ),
        }

    return Template(
        base_id="bool-tinyint",
        axes=[
            Axis("v", [0, 1]),
            Axis("pred", pred_slugs),
            Axis("limit", limits),
        ],
        build=build,
    )


def _unsigned_template() -> Template:
    """unsigned-type: products.stock INT UNSIGNED 경계 조회.

    stock 임계(경계 0·999 포함 12값) × 연산자(6) × LIMIT(5) = 360 ≥ 66.
    """
    thresholds = [0, 1, 5, 10, 50, 100, 200, 300, 500, 700, 900, 999]
    op_slugs = ["lt", "le", "eq", "gt", "ge", "ne"]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        threshold, op, limit = (
            combo["threshold"],
            _OP_SQL[combo["op"]],
            combo["limit"],
        )
        return {
            "ordered": True,
            "mysql": (
                f"SELECT id, stock\nFROM products\n"
                f"WHERE stock {op} {threshold}\n"
                f"ORDER BY id\nLIMIT {limit}\n"
            ),
        }

    return Template(
        base_id="unsigned-type",
        axes=[
            Axis("threshold", thresholds),
            Axis("op", op_slugs),
            Axis("limit", [5, 10, 20, 50, 100]),
        ],
        build=build,
    )


def _upsert_template() -> Template:
    """upsert-on-duplicate: INSERT ... ON DUPLICATE KEY UPDATE(dml).

    (id, email, name)을 구조화 축으로 묶어 같은 시드 행 하나를 가리킨다(P1-3) —
    두 시드 행의 PK·UNIQUE를 동시에 건드리는 불가능 조합 차단. 시드행(15) × 갱신컬럼(4) = 60.
    """
    # 기존 시드 행 15개(id=1..15)를 재사용. email은 씨드 규칙 user{n}@example.com.
    rows = [(i, f"user{i}@example.com", f"User {i}") for i in range(1, 16)]
    # ON DUPLICATE KEY UPDATE로 갱신할 컬럼/식 4가지.
    updates = [
        ("name", "VALUES(name)"),
        ("name", "'Upserted'"),
        ("email", "VALUES(email)"),
        ("created_at", "VALUES(created_at)"),
    ]

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        uid, email, name = combo["row"]
        upd_col, upd_expr = combo["update"]
        return {
            "isolation": "fresh",
            "statement": (
                f"INSERT INTO users (id, email, name, created_at)\n"
                f"VALUES ({uid}, '{email}', '{name}', "
                f"TIMESTAMP '2025-01-01 00:01:00')\n"
                f"ON DUPLICATE KEY UPDATE {upd_col} = {upd_expr}\n"
            ),
            "post_query": f"SELECT id, email, name FROM users WHERE id = {uid}\n",
        }

    return Template(
        base_id="upsert-on-duplicate",
        axes=[Axis("row", rows), Axis("update", updates)],
        build=build,
    )


def _auto_increment_template() -> Template:
    """auto-increment: AUTO_INCREMENT → PG IDENTITY(ddl).

    전용 TEMPORARY TABLE 내부만 벌린다(공유 스키마 불변). id 타입·라벨 타입·컬럼 개수
    변형 = 30 조합 ≥ 30. {{object_name}} 토큰은 OBJECT_NAME_PLACEHOLDER로 정확 보존.
    """
    id_types = ["INT", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT"]
    label_types = ["VARCHAR(10)", "VARCHAR(20)", "VARCHAR(50)"]
    extra_cols = [False, True]  # 추가 컬럼 유무.

    obj = OBJECT_NAME_PLACEHOLDER  # 정확 보존(f-string 밖에서 상수로 삽입).

    def build(_base: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        id_type, label_type, extra = (
            combo["id_type"],
            combo["label_type"],
            combo["extra"],
        )
        extra_line = ",\n  note VARCHAR(30) NOT NULL DEFAULT ''" if extra else ""
        insert_cols = "label, note" if extra else "label"
        insert_vals = (
            "('a', 'x'), ('b', 'y'), ('c', 'z')" if extra else "('a'), ('b'), ('c')"
        )
        select_cols = "id, label, note" if extra else "id, label"
        return {
            "isolation": "fresh",
            "object": {"type": "table", "name": "tmp_ai"},
            "statement": (
                f"CREATE TEMPORARY TABLE {obj} (\n"
                f"  id {id_type} AUTO_INCREMENT PRIMARY KEY,\n"
                f"  label {label_type} NOT NULL{extra_line}\n"
                f")\n"
            ),
            "exercise": (f"INSERT INTO {obj} ({insert_cols}) VALUES {insert_vals}\n"),
            "post_query": f"SELECT {select_cols} FROM {obj} ORDER BY id\n",
        }

    return Template(
        base_id="auto-increment",
        axes=[
            Axis("id_type", id_types),
            Axis("label_type", label_types),
            Axis("extra", extra_cols),
        ],
        build=build,
    )


def all_templates() -> list[Template]:
    """syntax 9개 템플릿(perf는 생성 제외 — golden 커버)."""
    return [
        _limit_template(),
        _ifnull_template(),
        _backtick_template(),
        _date_function_template(),
        _enum_template(),
        _bool_tinyint_template(),
        _unsigned_template(),
        _upsert_template(),
        _auto_increment_template(),
    ]


# ---------------------------------------------------------------------------
# 개념 분포 하한 (fail-closed, P1-4)
#
# 아래 숫자는 스펙이 확정한 계약이다. 구현 중 임의 조정 금지 — 값을 바꾸려면 스펙을
# 고쳐 재리뷰를 받아야 한다(fail-open 완전 차단). 키는 concept id다(base_id 아님).
# ---------------------------------------------------------------------------

MIN_PER_CONCEPT: dict[str, int] = {
    "limit-pagination": 66,
    "ifnull-coalesce": 66,
    "backtick-identifier": 40,
    "date-function": 66,
    "enum-type": 66,
    "tinyint-bool": 40,
    "unsigned-type": 66,
    "upsert-on-duplicate": 40,
    "auto-increment": 30,
}  # 합 480(하한). 실산출은 곱집합으로 이보다 커 총 --min-cases(1000) 이상.

# perf 6개는 golden 커버(생성 0). 대량 생성에서 제외.
PERF_CONCEPTS = {
    "covering-index",
    "multi-join",
    "keyset-pagination",
    "offset-pagination",
    "non-sargable-like",
    "groupby-aggregate",
}


def generate_all(
    golden: dict[str, dict[str, Any]],
    templates: list[Template] | None = None,
) -> list[GeneratedCase]:
    """템플릿을 전개하고 전역 유일성(golden+generated)을 검사한다(태스크5).

    id가 golden id나 다른 생성 id와 충돌하면 ValueError(조용한 접미사 금지 — 충돌은
    축 정의 버그라 드러낸다). 각 id는 ID_PATTERN도 자기검사한다.
    templates는 테스트 주입용(기본 all_templates()).
    """
    if templates is None:
        templates = all_templates()
    seen: set[str] = set(golden.keys())  # golden id를 먼저 넣어 합집합 기준 유일성.
    cases: list[GeneratedCase] = []
    for template in templates:
        for case in expand_template(template, golden):
            if not ID_PATTERN.match(case.id):
                raise ValueError(f"생성 id가 kebab-case가 아니다: {case.id}")
            if case.id in seen:
                raise ValueError(f"id 충돌: {case.id}")
            seen.add(case.id)
            cases.append(case)
    return cases


def count_by_concept(cases: list[GeneratedCase]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cases:
        counts[c.concept] = counts.get(c.concept, 0) + 1
    return counts


def check_distribution(counts: dict[str, int], min_cases: int) -> list[str]:
    """개념 하한·총 min-cases를 검사해 위반 메시지 리스트를 반환(빈 리스트=통과, fail-closed)."""
    errors: list[str] = []
    for concept, lower in MIN_PER_CONCEPT.items():
        got = counts.get(concept, 0)
        if got < lower:
            errors.append(f"개념 `{concept}` 하한 미달: {got} < {lower}")
    total = sum(counts.values())
    if total < min_cases:
        errors.append(f"총 케이스 미달: {total} < {min_cases}")
    return errors


def render_distribution(counts: dict[str, int], min_cases: int) -> str:
    """DISTRIBUTION.md 내용을 만든다(리뷰·추적용). 개념별 산출량·하한·총합 표."""
    lines = [
        "# 생성 케이스 분포",
        "",
        "`tools/generate_cases.py`가 자동 생성한다. 손으로 고치지 않는다.",
        "",
        "| 개념 | 산출 | 하한 |",
        "|------|-----:|-----:|",
    ]
    for concept, lower in MIN_PER_CONCEPT.items():
        lines.append(f"| {concept} | {counts.get(concept, 0)} | {lower} |")
    total = sum(counts.values())
    lines.append(f"| **합계** | **{total}** | **{min_cases}** |")
    lines.append("")
    return "\n".join(lines)


def dump_cases_yaml(cases: list[GeneratedCase]) -> str:
    """케이스 리스트를 `cases:` 최상위 YAML로 직렬화(결정적 옵션 고정)."""
    doc = {"cases": [c.data for c in cases]}
    return yaml.safe_dump(
        doc,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    )


def group_by_base_id(cases: list[GeneratedCase]) -> dict[str, list[GeneratedCase]]:
    """base_id별로 그룹핑한다(개념=파일 하나). 삽입 순서 유지(결정적)."""
    groups: dict[str, list[GeneratedCase]] = {}
    for c in cases:
        groups.setdefault(c.base_id, []).append(c)
    return groups


# ---------------------------------------------------------------------------
# 원자적 산출 + --out 가드 (P1-5)
# ---------------------------------------------------------------------------

# 프로젝트 루트 기준 golden 루트. --out이 이 경로와 겹치면 거부한다.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_ROOT = _PROJECT_ROOT / "corpus" / "cases"


def check_out_guard(out: Path) -> None:
    """--out이 golden(corpus/cases) 자체·상위·하위 경로면 거부한다(P1-5).

    resolve()로 절대경로화한 뒤 is_relative_to를 양방향으로 검사한다:
    - out이 golden의 하위(또는 자체) → golden 훼손 위험.
    - golden이 out의 하위 → 디렉터리 단위 교체가 golden을 지운다.
    """
    out_abs = out.resolve()
    golden_abs = _GOLDEN_ROOT.resolve()
    if out_abs.is_relative_to(golden_abs) or golden_abs.is_relative_to(out_abs):
        raise ValueError(
            f"--out({out_abs})이 golden 경로({golden_abs})와 겹친다 — golden 훼손 방지"
        )


def _write_generated_tree(
    dest: Path, groups: dict[str, list[GeneratedCase]], distribution_md: str
) -> None:
    """dest 아래 syntax/{base_id}.yaml + DISTRIBUTION.md를 쓴다(개념별 파일)."""
    syntax_dir = dest / "syntax"
    syntax_dir.mkdir(parents=True, exist_ok=True)
    for base_id, cases in groups.items():
        (syntax_dir / f"{base_id}.yaml").write_text(
            dump_cases_yaml(cases), encoding="utf-8"
        )
    (dest / "DISTRIBUTION.md").write_text(distribution_md, encoding="utf-8")


def write_corpus_atomic(
    out: Path,
    cases: list[GeneratedCase],
    whitelist: set[str],
    distribution_md: str,
) -> None:
    """원자적 산출: temp 전개 → 재로드 → validate → 디렉터리 단위 교체(P1-5).

    실패 시 temp 폐기, out 무손상(부분 산출 없음). out은 호출 전 check_out_guard로
    검사돼 있어야 한다.
    """
    groups = group_by_base_id(cases)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "generated"
        _write_generated_tree(tmp_dir, groups, distribution_md)

        # 재직렬화 검증(P1-5): 실제 산출 파일을 다시 읽어 최상위 구조·직렬화까지 통과 확인.
        raws, load_result = load_cases(sorted(tmp_dir.rglob("*.yaml")))
        result = validate_corpus(raws, whitelist, allow_incomplete_coverage=True)
        errors = load_result.errors + result.errors
        if errors:
            raise ValueError("재로드 정적 검증 실패:\n" + "\n".join(errors))

        # 성공 시에만 디렉터리 단위 교체(stale 파일 제거 포함).
        if out.exists():
            shutil.rmtree(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_dir), str(out))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(
    out: Path,
    min_cases: int,
    check_only: bool,
    concepts_path: Path,
) -> int:
    """생성·검증·(선택)기록을 수행하고 exit code를 반환한다.

    fail-closed: 개념 하한·min-cases 미달이면 파일을 쓰지 않고 non-zero.
    """
    whitelist = load_concepts(concepts_path)
    golden = load_golden(sorted(_GOLDEN_ROOT.rglob("*.yaml")), whitelist)
    cases = generate_all(golden)  # 전역 유일성 검사 포함.

    counts = count_by_concept(cases)
    distribution_md = render_distribution(counts, min_cases)
    print(distribution_md)

    dist_errors = check_distribution(counts, min_cases)
    if dist_errors:
        for e in dist_errors:
            print(f"[error] {e}", file=sys.stderr)
        return 1

    if check_only:
        print(f"OK(check-only): 케이스 {len(cases)}개 생성·검증 통과")
        return 0

    check_out_guard(out)
    write_corpus_atomic(out, cases, whitelist, distribution_md)
    print(f"OK: {out}에 케이스 {len(cases)}개 기록")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="코퍼스 케이스 생성기")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("corpus/generated"),
        help="생성 케이스 출력 디렉터리 (기본: corpus/generated)",
    )
    parser.add_argument(
        "--min-cases",
        type=int,
        default=1000,
        help="총 케이스 하한 (미달 시 실패)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="파일을 쓰지 않고 생성·검증만 (fail-closed 게이트)",
    )
    parser.add_argument(
        "--concepts",
        type=Path,
        default=Path("corpus/concepts.yaml"),
        help="개념 화이트리스트 (기본: corpus/concepts.yaml)",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.out, args.min_cases, args.check_only, args.concepts)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
