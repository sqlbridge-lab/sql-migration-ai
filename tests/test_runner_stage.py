"""stage 분류 행렬을 Fake Executor 주입으로 DB 없이 고정한다(P2-4).

FakeExecutor는 스크립트({sql_substring: result|exception})대로 응답/예외를 내는
가짜 Executor다. Runner에 executor_factory로 주입해 각 stage 분기를 단위 검증한다.
"""

from collections.abc import Callable
from typing import Any, cast

from harness.errors import (
    ConnectionFailure,
    InfrastructureFailure,
    SqlExecutionFailure,
    StatementTimeout,
)
from harness.executor import ConnectionConfig, Executor, QueryResult
from harness.loader import load_case
from harness.runner import Runner
from harness.transform import Transformer


def _runner(transformer: Any, factory: Any) -> Runner:
    """Fake(전송기·executor_factory)를 주입한 Runner. 테스트 더블이라 타입을 캐스팅한다."""
    return Runner(
        None,
        None,
        cast(Transformer, transformer),
        executor_factory=cast(Callable[[ConnectionConfig, str], Executor], factory),
    )


class FakeExecutor:
    def __init__(self, script, *, rollback_error=None, commit_error=None):
        self.script = script  # {sql_substring: result|exception}
        self.rollback_error = rollback_error
        self.commit_error = commit_error
        self.dialect = "fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def begin(self):
        pass

    def rollback(self):
        if self.rollback_error:
            raise self.rollback_error

    def commit(self):
        if self.commit_error:
            raise self.commit_error

    def drop_object(self, t, n):
        pass

    def _resolve(self, sql):
        for key, val in self.script.items():
            if key in sql:
                if isinstance(val, Exception):
                    raise val
                return val
        return QueryResult(["c"], [(1,)])  # 기본

    def run_query(self, sql, params=None):
        return self._resolve(sql)

    def run_statement(self, sql):
        self._resolve(sql)


def make_runner(my_script, pg_script, *, my_kwargs=None, pg_kwargs=None):
    my_kwargs = my_kwargs or {}
    pg_kwargs = pg_kwargs or {}

    def factory(config, dialect):
        if dialect == "mysql":
            return FakeExecutor(my_script, **my_kwargs)
        return FakeExecutor(pg_script, **pg_kwargs)

    class RaisingTransformer:
        def transform(self, s):
            return s

    return _runner(RaisingTransformer(), factory)


def _dql(mysql="SELECT 1"):
    return load_case(
        {
            "id": "c",
            "kind": "dql",
            "concepts": ["limit-pagination"],
            "mysql": mysql,
            "ordered": True,
        }
    )


def test_stage_pg_statement_fail():
    r = make_runner({}, {"SELECT 1": SqlExecutionFailure("syntax")}).run_case(_dql())
    assert r.status == "fail" and r.stage == "pg.statement"


def test_stage_mysql_statement_error():
    r = make_runner({"SELECT 1": SqlExecutionFailure("bad")}, {}).run_case(_dql())
    assert r.status == "error" and r.stage == "mysql.statement"


def test_stage_infrastructure_on_timeout():
    r = make_runner({"SELECT 1": StatementTimeout("t")}, {}).run_case(_dql())
    assert r.status == "error" and r.stage == "infrastructure"


def test_stage_infrastructure_on_connection():
    def factory(config, dialect):
        raise ConnectionFailure("down")

    class T:
        def transform(self, s):
            return s

    r = _runner(T(), factory).run_case(_dql())
    assert r.status == "error" and r.stage == "infrastructure"


def test_stage_transform_fail():
    class BadT:
        def transform(self, s):
            raise ValueError("parse error")

    def factory(config, dialect):
        return FakeExecutor({})

    r = _runner(BadT(), factory).run_case(_dql())
    assert r.status == "fail" and r.stage == "transform"


def test_stage_compare_fail():
    my = {"SELECT 1": QueryResult(["c"], [(1,)])}
    pg = {"SELECT 1": QueryResult(["c"], [(2,)])}
    r = make_runner(my, pg).run_case(_dql())
    assert r.status == "fail" and r.stage == "compare"


def test_stage_pass():
    my = {"SELECT 1": QueryResult(["c"], [(7,)])}
    pg = {"SELECT 1": QueryResult(["c"], [(7,)])}
    r = make_runner(my, pg).run_case(_dql())
    assert r.status == "pass" and r.stage is None


def _dml_case():
    return load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "UPDATE x",
            "post_query": "SELECT name",
        }
    )


def test_stage_control_error_dml():
    my = {"SELECT name": SqlExecutionFailure("bad control")}
    r = make_runner(my, {}).run_case(_dml_case())
    assert r.status == "error" and r.stage == "control"


def test_stage_infrastructure_failure_maps_to_infra():
    # 제어 SQL에서 InfrastructureFailure(예: timeout SET 실패)는 control이 아니라 infra.
    my = {"SELECT name": InfrastructureFailure("timeout set failed")}
    r = make_runner(my, {}).run_case(_dml_case())
    assert r.status == "error" and r.stage == "infrastructure"


# --- cleanup 4우선순위(P1-2) ---


def test_cleanup_fail_on_body_success_becomes_infra():
    # ① 본체 성공 + rollback(cleanup) 실패 → infrastructure/error
    body = {"SELECT name": QueryResult(["name"], [("a",)])}
    r = make_runner(
        body,
        body,
        my_kwargs={"rollback_error": InfrastructureFailure("rollback boom")},
    ).run_case(_dml_case())
    assert r.status == "error" and r.stage == "infrastructure"
    assert "cleanup" in (r.reason or "")


def test_cleanup_success_on_body_fail_keeps_original_stage():
    # ② 본체 실패 + cleanup 성공 → 원 stage 그대로
    my = {"UPDATE x": SqlExecutionFailure("mysql bad")}
    r = make_runner(my, {}).run_case(_dml_case())
    assert r.status == "error" and r.stage == "mysql.statement"
    assert "cleanup" not in (r.reason or "")


def test_cleanup_fail_on_body_fail_keeps_stage_and_appends_reason():
    # ③ 본체 실패(PG) + cleanup 실패 → 원 stage(pg.statement) 유지 + reason 누적
    my = {"SELECT name": QueryResult(["name"], [("a",)])}
    pg = {"UPDATE x": SqlExecutionFailure("pg syntax")}
    r = make_runner(
        my,
        pg,
        pg_kwargs={"rollback_error": InfrastructureFailure("rollback boom")},
    ).run_case(_dml_case())
    assert r.status == "fail" and r.stage == "pg.statement"
    assert "cleanup" in (r.reason or "")
