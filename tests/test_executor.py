import pytest

from harness.errors import ConnectionFailure, SqlExecutionFailure
from harness.executor import (
    ALLOWED_OBJECT_TYPES,
    MYSQL_CONFIG,
    POSTGRES_CONFIG,
    ConnectionConfig,
    Executor,
    safe_object_name,
)

# --- 단위 (DB 불필요) ---


def test_safe_object_name_normalizes_hyphen():
    assert (
        safe_object_name("auto-increment", "tmp_ai")
        == "sqlbridge_auto_increment_tmp_ai"
    )


def test_safe_object_name_truncates_long():
    name = safe_object_name("a" * 100, "tmp")
    assert len(name) <= 63


def test_only_table_object_type_allowed():
    assert ALLOWED_OBJECT_TYPES == {"table"}


# --- 통합 (@pytest.mark.integration) ---


@pytest.mark.integration
def test_mysql_select(mysql_up):
    with Executor.connect(MYSQL_CONFIG, "mysql") as ex:
        r = ex.run_query("SELECT id, name FROM products ORDER BY id LIMIT 3")
        assert r.columns == ["id", "name"]
        assert r.rows[0] == (1, "Product 0001")


@pytest.mark.integration
def test_postgres_select(postgres_up):
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        r = ex.run_query("SELECT id, name FROM products ORDER BY id LIMIT 3")
        assert r.rows[0] == (1, "Product 0001")


@pytest.mark.integration
def test_sql_error_translated(postgres_up):
    with (
        Executor.connect(POSTGRES_CONFIG, "postgres") as ex,
        pytest.raises(SqlExecutionFailure),
    ):
        ex.run_query("SELECT * FROM no_such_table_xyz")


@pytest.mark.integration
def test_connection_failure_translated():
    bad = ConnectionConfig("127.0.0.1", 1, "x", "x", "x")  # 닫힌 포트
    with pytest.raises(ConnectionFailure):
        Executor.connect(bad, "postgres")


@pytest.mark.integration
def test_dml_rollback_restores_seed(postgres_up):
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        before = ex.run_query("SELECT COUNT(*) FROM users").rows[0][0]
        ex.run_statement(
            "INSERT INTO users (id, email, name, created_at) "
            "VALUES (999999, 'rollback@x.com', 'X', TIMESTAMP '2025-01-01 00:00:00')"
        )
        ex.rollback()
        after = ex.run_query("SELECT COUNT(*) FROM users").rows[0][0]
        assert before == after


@pytest.mark.integration
def test_ddl_cleanup_after_pg_abort(postgres_up):
    """PG statement 실패로 트랜잭션이 aborted여도 ROLLBACK→DROP→COMMIT로 정리된다."""
    name = safe_object_name("cleanup-test", "tmp")
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        ex.drop_object("table", name)
        ex.commit()
        ex.run_statement(f"CREATE TABLE {name} (id int)")  # 영구 테이블
        ex.commit()
        try:
            ex.run_statement("SELECT * FROM no_such_xyz")  # 실패 → abort
        except SqlExecutionFailure:
            pass
        ex.rollback()
        ex.drop_object("table", name)
        ex.commit()
        # catalog에서 부재 확인
        r = ex.run_query(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s",
            (name,),
        )
        assert r.rows[0][0] == 0
