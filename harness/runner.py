"""케이스를 로드→변환→양쪽 실행→비교해 CaseResult를 만드는 조립기.

kind별 경로(dql/dml/ddl)를 조립하고, 예외를 CaseResult로 변환하며 stage로 분류한다.
핵심은 '변환기 품질(fail)과 그 외(error)'를 가르는 것이다(스펙 에러 처리 표).
executor_factory를 주입받아(기본 Executor.connect) Fake로 stage 행렬을 단위 테스트한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from harness.compare import compare
from harness.errors import (
    ConnectionFailure,
    InfrastructureFailure,
    SqlExecutionFailure,
    StatementTimeout,
)
from harness.executor import (
    ALLOWED_OBJECT_TYPES,
    ConnectionConfig,
    Executor,
    QueryResult,
    safe_object_name,
)
from harness.loader import Case
from harness.transform import Transformer, fix_clock

FIXED_CLOCK_TS = "2025-06-01 12:00:00"


@dataclass
class CaseResult:
    case_id: str
    status: str
    stage: str | None
    reason: str | None


class _StageError(Exception):
    def __init__(self, stage: str, status: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage
        self.status = status
        self.reason = reason


class Runner:
    def __init__(
        self,
        mysql_config: ConnectionConfig | None,
        postgres_config: ConnectionConfig | None,
        transformer: Transformer,
        executor_factory: Callable[
            [ConnectionConfig, str], Executor
        ] = Executor.connect,
    ) -> None:
        self._mysql_config = mysql_config
        self._postgres_config = postgres_config
        self._transformer = transformer
        self._factory = executor_factory

    def run_case(self, case: Case) -> CaseResult:
        try:
            if case.kind == "dql":
                return self._run_dql(case)
            if case.kind == "dml":
                return self._run_dml(case)
            if case.kind == "ddl":
                return self._run_ddl(case)
            raise _StageError("control", "error", f"알 수 없는 kind: {case.kind}")
        except _StageError as e:
            return CaseResult(case.id, e.status, e.stage, e.reason)
        except Exception as e:  # noqa: BLE001  # 마지막 방어선(P1-4): 번역 누락 등
            return CaseResult(case.id, "error", "infrastructure", f"예상 밖 오류: {e}")

    # --- 공통 헬퍼 ---

    def _transform_or_stage(self, mysql_sql: str) -> str:
        """변환 예외(ParseError 등)를 transform/fail로 잡는다(P1-1)."""
        try:
            return self._transformer.transform(mysql_sql)
        except Exception as e:
            raise _StageError("transform", "fail", f"변환 실패: {e}") from e

    def _maybe_fix_clock(self, case: Case, sql: str, dialect: str) -> str:
        nd = case.nondeterministic
        if nd and nd.get("strategy") == "fixed_clock":
            return fix_clock(sql, FIXED_CLOCK_TS, dialect=dialect)
        return sql

    def _connect(self, config: ConnectionConfig | None, dialect: str) -> Executor:
        try:
            return self._factory(config, dialect)  # type: ignore[arg-type]
        except ConnectionFailure as e:
            raise _StageError("infrastructure", "error", str(e)) from e

    def _run_verified(self, ex: Executor, dialect: str, sql: str) -> None:
        """피검증 statement 실행. MySQL 실패=error, PG 실패=fail. timeout/infra=infra."""
        try:
            ex.run_statement(sql)
        except (StatementTimeout, ConnectionFailure, InfrastructureFailure) as e:
            raise _StageError(
                "infrastructure", "error", f"{type(e).__name__}: {e}"
            ) from e
        except SqlExecutionFailure as e:
            if dialect == "mysql":
                raise _StageError(
                    "mysql.statement", "error", f"MySQL 피검증 실패: {e}"
                ) from e
            raise _StageError("pg.statement", "fail", f"PG 피검증 실패: {e}") from e

    def _run_control(
        self, ex: Executor, dialect: str, sql: str, *, query: bool = False
    ) -> QueryResult | None:
        """제어 SQL 실행. 본체 실패=control/error, timeout/infra=infrastructure/error."""
        try:
            return ex.run_query(sql) if query else ex.run_statement(sql)
        except (StatementTimeout, ConnectionFailure, InfrastructureFailure) as e:
            raise _StageError(
                "infrastructure", "error", f"{type(e).__name__}: {e}"
            ) from e
        except SqlExecutionFailure as e:
            raise _StageError(
                "control", "error", f"{dialect} 제어 SQL 실패: {e}"
            ) from e

    def _compare_results(
        self, case: Case, my_res: QueryResult, pg_res: QueryResult
    ) -> CaseResult:
        exclude = None
        nd = case.nondeterministic
        if nd and nd.get("strategy") == "exclude_columns":
            exclude = nd.get("columns")
        cmp = compare(
            my_res.columns,
            my_res.rows,
            pg_res.columns,
            pg_res.rows,
            ordered=case.ordered,
            exclude_columns=exclude,
        )
        if cmp.equal:
            return CaseResult(case.id, "pass", None, None)
        return CaseResult(case.id, "fail", "compare", cmp.reason)

    # --- dql ---

    def _run_dql(self, case: Case) -> CaseResult:
        assert case.mysql is not None
        # 변환은 DB 연결 전(P1-1). 예외는 transform/fail.
        pg_sql = self._transform_or_stage(case.mysql)
        my_sql = self._maybe_fix_clock(case, case.mysql, "mysql")
        pg_sql = self._maybe_fix_clock(case, pg_sql, "postgres")

        with (
            self._connect(self._mysql_config, "mysql") as my,
            self._connect(self._postgres_config, "postgres") as pg,
        ):
            my_res = self._run_verified_query(my, "mysql", my_sql)
            pg_res = self._run_verified_query(pg, "postgres", pg_sql)
        return self._compare_results(case, my_res, pg_res)

    def _run_verified_query(self, ex: Executor, dialect: str, sql: str) -> QueryResult:
        try:
            return ex.run_query(sql)
        except (StatementTimeout, ConnectionFailure, InfrastructureFailure) as e:
            raise _StageError(
                "infrastructure", "error", f"{type(e).__name__}: {e}"
            ) from e
        except SqlExecutionFailure as e:
            if dialect == "mysql":
                raise _StageError(
                    "mysql.statement", "error", f"MySQL 피검증 실패: {e}"
                ) from e
            raise _StageError("pg.statement", "fail", f"PG 피검증 실패: {e}") from e

    # --- cleanup 4우선순위 헬퍼 (P1-2) ---

    def _cleanup(self, steps: list[Callable[[], None]]) -> str | None:
        """cleanup 단계를 모두 시도. 성공 시 None, 실패 시 누적 메시지 반환(삼키지 않음)."""
        errors: list[str] = []
        for step in steps:
            try:
                step()
            except Exception as e:  # noqa: BLE001  # cleanup 오류는 삼키지 않고 누적
                errors.append(f"{type(e).__name__}: {e}")
        return "; ".join(errors) if errors else None

    def _finalize(
        self,
        case: Case,
        body_error: _StageError | None,
        cleanup_error: str | None,
        result: CaseResult | None,
    ) -> CaseResult:
        """본체 결과/예외 + cleanup 결과를 4규칙으로 종합(P1-2)."""
        if body_error is None:
            if cleanup_error is not None:  # ① 본체 성공 + cleanup 실패
                return CaseResult(
                    case.id, "error", "infrastructure", f"cleanup 실패: {cleanup_error}"
                )
            assert result is not None
            return result  # 정상
        # 본체 실패
        if cleanup_error is None:  # ② 본체 실패 + cleanup 성공
            return CaseResult(
                case.id, body_error.status, body_error.stage, body_error.reason
            )
        # ③ 본체 실패 + cleanup 실패 → 원 stage 유지 + reason 누적
        return CaseResult(
            case.id,
            body_error.status,
            body_error.stage,
            f"{body_error.reason} | cleanup 실패: {cleanup_error}",
        )

    # --- dml ---

    def _run_dml(self, case: Case) -> CaseResult:
        assert case.statement is not None
        pg_stmt = self._transform_or_stage(case.statement)  # P1-1: dml도 연결 전 변환
        with (
            self._connect(self._mysql_config, "mysql") as my,
            self._connect(self._postgres_config, "postgres") as pg,
        ):
            my_res = self._run_state_path(case, my, "mysql", case.statement)
            if isinstance(my_res, CaseResult):  # 본체 실패가 CaseResult로 돌아옴
                return my_res
            pg_res = self._run_state_path(case, pg, "postgres", pg_stmt)
            if isinstance(pg_res, CaseResult):
                return pg_res
        return self._compare_state(case, my_res, pg_res)

    def _run_state_path(
        self, case: Case, ex: Executor, dialect: str, statement: str
    ) -> QueryResult | None | CaseResult:
        """본체 실행 후 cleanup(rollback). 반환: QueryResult|None(성공) 또는 CaseResult(실패)."""
        control = case.control_mysql if dialect == "mysql" else case.control_postgres
        body_error: _StageError | None = None
        result: QueryResult | None = None
        try:
            ex.begin()
            if "setup" in control:
                self._run_control(
                    ex, dialect, self._maybe_fix_clock(case, control["setup"], dialect)
                )
            self._run_verified(
                ex, dialect, self._maybe_fix_clock(case, statement, dialect)
            )
            if "exercise" in control:
                self._run_control(
                    ex,
                    dialect,
                    self._maybe_fix_clock(case, control["exercise"], dialect),
                )
            if "post_query" in control:
                result = self._run_control(
                    ex,
                    dialect,
                    self._maybe_fix_clock(case, control["post_query"], dialect),
                    query=True,
                )
        except _StageError as e:
            body_error = e
        cleanup_error = self._cleanup([ex.rollback])  # 격리: 반드시 롤백(삼키지 않음)
        if body_error is not None or cleanup_error is not None:
            return self._finalize(case, body_error, cleanup_error, None)
        return result  # 성공: QueryResult|None

    def _compare_state(
        self,
        case: Case,
        my_res: QueryResult | None,
        pg_res: QueryResult | None,
    ) -> CaseResult:
        if my_res is None or pg_res is None:
            return CaseResult(
                case.id, "pass", None, None
            )  # post_query 없으면 성공=pass
        return self._compare_results(case, my_res, pg_res)

    # --- ddl ---

    def _run_ddl(self, case: Case) -> CaseResult:
        assert case.statement is not None and case.object is not None
        obj_type = case.object["type"]
        if obj_type not in ALLOWED_OBJECT_TYPES:
            raise _StageError(
                "control", "error", f"지원하지 않는 object type: {obj_type}"
            )
        name = safe_object_name(case.id, case.object["name"])
        my_stmt = case.statement.replace(
            "{{object_name}}", name
        )  # 고유명 치환(연결 전)
        pg_stmt = self._transform_or_stage(my_stmt)  # P1-3: 연결 전 변환

        with (
            self._connect(self._mysql_config, "mysql") as my,
            self._connect(self._postgres_config, "postgres") as pg,
        ):
            my_res = self._run_ddl_path(case, my, "mysql", obj_type, name, my_stmt)
            if isinstance(my_res, CaseResult):
                return my_res
            pg_res = self._run_ddl_path(case, pg, "postgres", obj_type, name, pg_stmt)
            if isinstance(pg_res, CaseResult):
                return pg_res
        return self._compare_state(case, my_res, pg_res)

    def _run_ddl_path(
        self,
        case: Case,
        ex: Executor,
        dialect: str,
        obj_type: str,
        name: str,
        statement: str,
    ) -> QueryResult | None | CaseResult:
        """본체 후 cleanup(ROLLBACK→DROP→COMMIT). 성공: QueryResult|None, 실패: CaseResult."""

        def subst(sql: str) -> str:
            return sql.replace("{{object_name}}", name)

        control = case.control_mysql if dialect == "mysql" else case.control_postgres

        # ④ pre-clean: 실패 시 본체 미실행 + infrastructure(DROP IF EXISTS는 객체 없어도 성공).
        pre_error = self._cleanup([lambda: ex.drop_object(obj_type, name), ex.commit])
        if pre_error is not None:
            return CaseResult(
                case.id, "error", "infrastructure", f"pre-clean 실패: {pre_error}"
            )

        body_error: _StageError | None = None
        result: QueryResult | None = None
        try:
            if "setup" in control:
                self._run_control(
                    ex,
                    dialect,
                    subst(self._maybe_fix_clock(case, control["setup"], dialect)),
                )
            self._run_verified(
                ex, dialect, self._maybe_fix_clock(case, statement, dialect)
            )
            if "exercise" in control:
                self._run_control(
                    ex,
                    dialect,
                    subst(self._maybe_fix_clock(case, control["exercise"], dialect)),
                )
            if "post_query" in control:
                result = self._run_control(
                    ex,
                    dialect,
                    subst(self._maybe_fix_clock(case, control["post_query"], dialect)),
                    query=True,
                )
        except _StageError as e:
            body_error = e
        # ②③ aborted 정리: ROLLBACK → 새 트랜잭션 DROP → COMMIT (삼키지 않고 누적).
        cleanup_error = self._cleanup(
            [ex.rollback, lambda: ex.drop_object(obj_type, name), ex.commit]
        )
        if body_error is not None or cleanup_error is not None:
            return self._finalize(case, body_error, cleanup_error, None)
        return result
