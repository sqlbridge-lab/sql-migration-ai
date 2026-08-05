"""케이스 YAML을 Case 객체로. 제어 SQL 공통형/DB별 쌍을 DB별로 정규화한다.

형식 검증은 tools/validate_corpus.py를 재사용한다(중복 규칙 금지).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from validate_corpus import (
    load_cases,
    load_concepts,
    validate_corpus,
)

_PAIRABLE = ("setup", "post_query")  # 공통형 또는 _mysql/_postgres 쌍
_COMMON_ONLY = ("exercise",)  # 항상 공통형


@dataclass
class Case:
    id: str
    kind: str
    concepts: list[str]
    mysql: str | None
    statement: str | None
    ordered: bool
    isolation: str | None
    object: dict | None
    nondeterministic: dict | None
    control_mysql: dict[str, str] = field(default_factory=dict)
    control_postgres: dict[str, str] = field(default_factory=dict)


def _normalize_control(raw: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    mysql: dict[str, str] = {}
    postgres: dict[str, str] = {}
    for base in _PAIRABLE:
        if base in raw:
            mysql[base] = raw[base]
            postgres[base] = raw[base]
        elif f"{base}_mysql" in raw:
            mysql[base] = raw[f"{base}_mysql"]
            postgres[base] = raw[f"{base}_postgres"]
    for base in _COMMON_ONLY:
        if base in raw:
            mysql[base] = raw[base]
            postgres[base] = raw[base]
    return mysql, postgres


def load_case(raw: dict[str, Any]) -> Case:
    control_mysql, control_postgres = _normalize_control(raw)
    return Case(
        id=raw["id"],
        kind=raw["kind"],
        concepts=raw["concepts"],
        mysql=raw.get("mysql"),
        statement=raw.get("statement"),
        ordered=raw.get("ordered", False),
        isolation=raw.get("isolation"),
        object=raw.get("object"),
        nondeterministic=raw.get("nondeterministic"),
        control_mysql=control_mysql,
        control_postgres=control_postgres,
    )


def load_corpus(cases_dir: Path, concepts_path: Path) -> list[Case]:
    """코퍼스 디렉터리를 읽어 형식 검증 후 Case 리스트로 반환."""
    whitelist = load_concepts(concepts_path)
    case_files = sorted(cases_dir.rglob("*.yaml"))
    raws, load_result = load_cases(case_files)
    result = validate_corpus(raws, whitelist)
    errors = load_result.errors + result.errors
    if errors:
        raise ValueError("코퍼스 형식 검증 실패:\n" + "\n".join(errors))
    return [load_case(r) for r in raws]
