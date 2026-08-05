"""한 DB에 SQL을 실행하고 격리(트랜잭션/DROP)를 담당하는 Executor.

MySQL은 PyMySQL, PostgreSQL은 psycopg(v3). 드라이버 예외를 harness.errors의
타입(StatementTimeout/ConnectionFailure/InfrastructureFailure/SqlExecutionFailure)으로
번역해, Runner가 드라이버를 몰라도 stage를 분류할 수 있게 한다.
QueryResult.rows는 드라이버 원본 타입을 유지한다(Comparator 타입 정규화 전제).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Self

import psycopg
import pymysql

from harness.errors import (
    ConnectionFailure,
    InfrastructureFailure,
    SqlExecutionFailure,
    StatementTimeout,
)

STATEMENT_TIMEOUT_SECONDS = 30
_MAX_IDENT = 63
_MYSQL_TIMEOUT_ERRCODE = 3024  # ER_QUERY_TIMEOUT (MAX_EXECUTION_TIME 초과)
ALLOWED_OBJECT_TYPES = {"table"}


@dataclass(frozen=True)
class ConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


MYSQL_CONFIG = ConnectionConfig("127.0.0.1", 13306, "root", "root", "shop")
POSTGRES_CONFIG = ConnectionConfig("127.0.0.1", 15432, "postgres", "postgres", "shop")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]


def safe_object_name(case_id: str, object_name: str) -> str:
    # case_id·object_name 모두 식별자로 정규화한다. 고유명이 DROP 등 DDL에 직접
    # 보간되므로, 허용 문자 외(하이픈·공백·따옴표 등)를 `_`로 바꿔 SQL 주입을 막는다.
    safe_id = re.sub(r"[^0-9a-zA-Z_]", "_", case_id)
    safe_name = re.sub(r"[^0-9a-zA-Z_]", "_", object_name)
    name = f"sqlbridge_{safe_id}_{safe_name}"
    if len(name) <= _MAX_IDENT:
        return name
    suffix = "_" + hashlib.sha1(case_id.encode()).hexdigest()[:8]
    return name[: _MAX_IDENT - len(suffix)] + suffix


class Executor:
    def __init__(self, conn: object, dialect: str) -> None:
        self._conn = conn
        self.dialect = dialect

    @classmethod
    def connect(cls, config: ConnectionConfig, dialect: str) -> Self:
        try:
            if dialect == "mysql":
                conn: object = pymysql.connect(
                    host=config.host,
                    port=config.port,
                    user=config.user,
                    password=config.password,
                    database=config.database,
                    autocommit=False,
                )
            elif dialect == "postgres":
                conn = psycopg.connect(
                    host=config.host,
                    port=config.port,
                    user=config.user,
                    password=config.password,
                    dbname=config.database,
                    autocommit=False,
                )
            else:
                raise ValueError(f"알 수 없는 dialect: {dialect}")
        except (pymysql.err.OperationalError, psycopg.OperationalError) as e:
            raise ConnectionFailure(f"{dialect} 연결 실패: {e}") from e
        return cls(conn, dialect)

    # --- 예외 번역 경계 (P1-4) ---

    @contextmanager
    def _translate_query(self) -> Iterator[None]:
        """쿼리 '본체' 실행용. timeout→StatementTimeout, 연결→ConnectionFailure,
        그 외 SQL 실패→SqlExecutionFailure(stage는 호출측이 결정).
        """
        try:
            yield
        except psycopg.errors.QueryCanceled as e:  # PG statement_timeout
            raise StatementTimeout(str(e)) from e
        except pymysql.err.OperationalError as e:  # MySQL: 3024=timeout
            code = e.args[0] if e.args else None
            if code == _MYSQL_TIMEOUT_ERRCODE:
                raise StatementTimeout(str(e)) from e
            raise SqlExecutionFailure(str(e)) from e
        except psycopg.OperationalError as e:  # PG 연결 단절
            raise ConnectionFailure(str(e)) from e
        except (pymysql.err.MySQLError, psycopg.Error) as e:  # 구문/제약 등
            raise SqlExecutionFailure(str(e)) from e

    @contextmanager
    def _translate_infra(self) -> Iterator[None]:
        """쿼리 본체가 아닌 DB 호출용(timeout SET·commit·rollback·cursor·fetch·close).
        어떤 드라이버 예외든 InfrastructureFailure로 번역(항상 infrastructure/error).
        """
        try:
            yield
        except (pymysql.err.MySQLError, psycopg.Error) as e:
            raise InfrastructureFailure(str(e)) from e

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._conn.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001, S110
            pass  # 닫기 실패는 무시(이미 종료 경로 — 여기서 던지면 원 예외를 가림)

    def _apply_timeout(self, cur: object) -> None:
        ms = STATEMENT_TIMEOUT_SECONDS * 1000
        # timeout SET 자체 실패는 인프라(예: PG aborted 트랜잭션). _translate_infra로 감쌈.
        with self._translate_infra():
            if self.dialect == "postgres":
                cur.execute(f"SET statement_timeout = {ms}")  # type: ignore[attr-defined]
            else:
                # MySQL MAX_EXECUTION_TIME은 read-only SELECT 전용(DML/DDL 비보장).
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {ms}")  # type: ignore[attr-defined]

    def run_query(self, sql: str, params: tuple | None = None) -> QueryResult:
        with self._translate_infra():
            cur = self._conn.cursor()  # type: ignore[attr-defined]
        try:
            self._apply_timeout(cur)
            with self._translate_query():  # 쿼리 본체만 query 번역
                cur.execute(sql, params) if params is not None else cur.execute(sql)
            with self._translate_infra():  # description·fetch는 인프라
                columns = [d[0] for d in cur.description]
                rows = [tuple(r) for r in cur.fetchall()]
            return QueryResult(columns, rows)
        finally:
            with self._translate_infra():
                cur.close()

    def run_statement(self, sql: str) -> None:
        with self._translate_infra():
            cur = self._conn.cursor()  # type: ignore[attr-defined]
        try:
            self._apply_timeout(cur)
            with self._translate_query():
                cur.execute(sql)
        finally:
            with self._translate_infra():
                cur.close()

    def begin(self) -> None:
        pass  # autocommit=False라 첫 실행 시 트랜잭션이 열린다.

    def rollback(self) -> None:
        with self._translate_infra():
            self._conn.rollback()  # type: ignore[attr-defined]

    def commit(self) -> None:
        with self._translate_infra():
            self._conn.commit()  # type: ignore[attr-defined]

    def drop_object(self, object_type: str, name: str) -> None:
        if object_type not in ALLOWED_OBJECT_TYPES:
            raise ValueError(f"지원하지 않는 object type: {object_type}")
        self.run_statement(f"DROP {object_type} IF EXISTS {name}")
