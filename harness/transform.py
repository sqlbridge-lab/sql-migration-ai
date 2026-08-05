"""피검증 SQL 변환 계층 + fixed_clock 전처리.

이번 단계엔 실제 변환 엔진이 없어 PassThroughTransformer가 입력을 그대로 돌려준다.
변환 엔진(이슈 C)이 나오면 같은 Transformer Protocol로 갈아끼운다.
fix_clock은 변환기와 무관한 '오라클 고정' 전처리다(비결정 시각 함수 → 고정 리터럴).
"""

from __future__ import annotations

from typing import Protocol

import sqlglot
from sqlglot import exp


class Transformer(Protocol):
    """MySQL SQL을 PostgreSQL SQL로 바꾸는 계약(Java의 interface에 해당)."""

    def transform(self, mysql_sql: str) -> str: ...


class PassThroughTransformer:
    """변환 엔진이 없을 때 입력을 그대로 반환하는 자리표시 구현."""

    def transform(self, mysql_sql: str) -> str:
        return mysql_sql


# 고정할 현재시각 함수 allowlist. SQLGlot이 Anonymous로 파싱하는 함수는 함수명으로,
# 전용 노드로 파싱하는 함수는 노드 타입으로 판별한다(구현 중 실측으로 확정).
_CLOCK_ANON_NAMES = {"now", "sysdate"}


def fix_clock(sql: str, ts: str, *, dialect: str) -> str:
    """현재시각 함수(allowlist)를 고정 리터럴로 치환한다.

    PostgreSQL은 SET으로 now()를 고정할 수 없어, 양 DB를 동일하게 다루려고
    실행 전 SQL 자체를 치환한다. dialect로 파싱·재생성 방언을 맞춘다.
    지원: NOW/CURRENT_TIMESTAMP/LOCALTIMESTAMP/SYSDATE(→timestamp),
    CURDATE/CURRENT_DATE(→date).
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    ts_literal = exp.cast(exp.Literal.string(ts), "TIMESTAMP")
    date_literal = exp.cast(exp.Literal.string(ts[:10]), "DATE")

    def _replace(node):  # type: ignore[no-untyped-def]  # sqlglot.exp.Expression 미노출
        if isinstance(node, (exp.CurrentTimestamp, exp.Localtimestamp)):
            return ts_literal.copy()
        if isinstance(node, exp.CurrentDate):
            return date_literal.copy()
        if isinstance(node, exp.Anonymous):
            name = node.this.lower() if isinstance(node.this, str) else ""
            if name in _CLOCK_ANON_NAMES:
                return ts_literal.copy()
        return node

    return tree.transform(_replace).sql(dialect=dialect)
