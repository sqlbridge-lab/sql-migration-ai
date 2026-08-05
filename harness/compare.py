"""두 결과셋 동일성 비교. DB·드라이버를 전혀 모르는 순수 함수.

float 근사 비교 때문에 '행 동등성'이 정확한 해시 동등성이 아니다. 그래서
unordered 비교에 Counter(해시)나 탐욕적 매칭을 쓸 수 없다(탐욕은 반례가 있다:
A=[0.9e-9, 0], B=[0, 1.8e-9], tol=1e-9 → 0.9e-9가 먼저 0을 소비하면 실패).
완전 매칭 존재 여부를 이분 그래프 최대 매칭으로 판정한다.

값 비교: 정수·Decimal은 정확 비교(오차 없음), float가 관여할 때만 1e-9 오차.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

FLOAT_TOL = 1e-9


@dataclass
class Comparison:
    equal: bool
    reason: str | None


def _value_equal(a: object, b: object) -> bool:
    # NULL
    if a is None or b is None:
        return a is None and b is None

    # bool ↔ 정수 0/1 (근사 아님: 상대가 정확히 0/1인 정수/bool일 때만)
    if isinstance(a, bool) or isinstance(b, bool):
        av = _bool_to_int(a)
        bv = _bool_to_int(b)
        if av is None or bv is None:
            return False
        return av == bv

    # datetime: 두 컨테이너 UTC 고정 → 같은 순간이면 동일
    if isinstance(a, datetime) and isinstance(b, datetime):
        return _same_instant(a, b)

    # Decimal: 스케일만 흡수(값 붕괴 금지) — Decimal끼리 또는 Decimal↔정수 정확 비교
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return a == b  # Decimal ==는 스케일 무시 수치 비교(10.00 == 10.0)
    if isinstance(a, Decimal) and isinstance(b, int):
        return a == b
    if isinstance(b, Decimal) and isinstance(a, int):
        return a == b

    # float가 관여하면 오차 비교
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float, Decimal)) and isinstance(
            b, (int, float, Decimal)
        ):
            return math.isclose(
                float(a), float(b), rel_tol=FLOAT_TOL, abs_tol=FLOAT_TOL
            )
        return False

    # 정수 등 나머지는 정확 비교
    return a == b


def _bool_to_int(v: object) -> int | None:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return v if v in (0, 1) else None
    return None


def _same_instant(a: datetime, b: datetime) -> bool:
    # naive는 UTC로 간주(두 컨테이너 UTC 고정). aware는 그대로.
    aa = a if a.tzinfo else a.replace(tzinfo=UTC)
    bb = b if b.tzinfo else b.replace(tzinfo=UTC)
    return aa == bb


def row_equal(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(_value_equal(x, y) for x, y in zip(a, b))


def compare(
    columns_a: list[str],
    rows_a: list[tuple],
    columns_b: list[str],
    rows_b: list[tuple],
    *,
    ordered: bool,
    exclude_columns: list[str] | None = None,
) -> Comparison:
    if columns_a != columns_b:
        return Comparison(False, f"컬럼 불일치: {columns_a} vs {columns_b}")

    if exclude_columns:
        rows_a, _ = _drop_columns(columns_a, rows_a, exclude_columns)
        rows_b, _ = _drop_columns(columns_b, rows_b, exclude_columns)

    if len(rows_a) != len(rows_b):
        return Comparison(False, f"행 개수 다름: {len(rows_a)} vs {len(rows_b)}")

    if ordered:
        for i, (ra, rb) in enumerate(zip(rows_a, rows_b)):
            if not row_equal(ra, rb):
                return Comparison(False, f"{i}행 불일치: {ra!r} != {rb!r}")
        return Comparison(True, None)

    return _multiset_equal(rows_a, rows_b)


def _drop_columns(
    columns: list[str], rows: list[tuple], exclude: list[str]
) -> tuple[list[tuple], list[str]]:
    keep = [i for i, c in enumerate(columns) if c not in exclude]
    return [tuple(r[i] for i in keep) for r in rows], [columns[i] for i in keep]


def _multiset_equal(rows_a: list[tuple], rows_b: list[tuple]) -> Comparison:
    """이분 그래프 최대 매칭(Kuhn). rows_a[i]↔rows_b[j]가 row_equal이면 간선.
    완전 매칭(모든 a가 매칭)이면 equal(개수는 이미 같음).
    """
    n = len(rows_a)
    match_b: list[int] = [-1] * n  # rows_b[j]에 매칭된 rows_a 인덱스

    def try_augment(i: int, seen: list[bool]) -> bool:
        for j in range(n):
            if row_equal(rows_a[i], rows_b[j]) and not seen[j]:
                seen[j] = True
                if match_b[j] == -1 or try_augment(match_b[j], seen):
                    match_b[j] = i
                    return True
        return False

    for i in range(n):
        if not try_augment(i, [False] * n):
            return Comparison(False, f"매칭 안 되는 행: {rows_a[i]!r}")
    return Comparison(True, None)
