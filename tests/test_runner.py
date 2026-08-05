import pytest

from harness.executor import (
    MYSQL_CONFIG,
    POSTGRES_CONFIG,
    Executor,
    safe_object_name,
)
from harness.loader import load_case
from harness.runner import Runner
from harness.transform import PassThroughTransformer


@pytest.fixture
def runner(mysql_up, postgres_up):
    return Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())


@pytest.mark.integration
def test_dql_standard_passes(runner):
    case = load_case(
        {
            "id": "enum-type",
            "kind": "dql",
            "concepts": ["enum-type"],
            "ordered": True,
            "mysql": "SELECT id, status FROM orders WHERE status='paid' ORDER BY id LIMIT 5",
        }
    )
    r = runner.run_case(case)
    assert r.status == "pass", r.reason


@pytest.mark.integration
def test_dql_backtick_fails_at_pg(runner):
    case = load_case(
        {
            "id": "backtick-identifier",
            "kind": "dql",
            "concepts": ["backtick-identifier"],
            "ordered": True,
            "mysql": "SELECT `id` FROM `products` ORDER BY `id` LIMIT 5",
        }
    )
    r = runner.run_case(case)
    assert r.status == "fail" and r.stage == "pg.statement" and r.reason


@pytest.mark.integration
def test_fixed_clock_same_instant(runner):
    case = load_case(
        {
            "id": "date-function",
            "kind": "dql",
            "concepts": ["date-function"],
            "ordered": True,
            "nondeterministic": {"strategy": "fixed_clock"},
            "mysql": "SELECT id FROM orders WHERE ordered_at < NOW() ORDER BY id LIMIT 5",
        }
    )
    r = runner.run_case(case)
    assert r.status == "pass", r.reason  # NOW() 고정 리터럴 치환으로 양 DB 동일 필터


@pytest.mark.integration
def test_dml_upsert_fails_at_pg_and_isolated(runner):
    case = load_case(
        {
            "id": "upsert-on-duplicate",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "INSERT INTO users (id, email, name, created_at) "
            "VALUES (1, 'user1@example.com', 'Upserted', TIMESTAMP '2025-01-01 00:01:00') "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            "post_query": "SELECT name FROM users WHERE id = 1",
        }
    )

    def snapshot(cfg, dialect):
        with Executor.connect(cfg, dialect) as ex:
            return ex.run_query("SELECT name FROM users WHERE id = 1").rows[0][0]

    my_before = snapshot(MYSQL_CONFIG, "mysql")
    pg_before = snapshot(POSTGRES_CONFIG, "postgres")
    r = runner.run_case(case)
    assert my_before == snapshot(MYSQL_CONFIG, "mysql")  # 'User 1' 복원
    assert pg_before == snapshot(POSTGRES_CONFIG, "postgres")
    assert r.status == "fail" and r.stage == "pg.statement"  # ON DUPLICATE PG 미지원


@pytest.mark.integration
def test_ddl_auto_increment_fails_and_no_leftover(runner):
    case = load_case(
        {
            "id": "auto-increment",
            "kind": "ddl",
            "isolation": "fresh",
            "concepts": ["auto-increment"],
            "object": {"type": "table", "name": "tmp_ai"},
            "statement": "CREATE TABLE {{object_name}} "
            "(id INT AUTO_INCREMENT PRIMARY KEY, label VARCHAR(20) NOT NULL)",
            "exercise": "INSERT INTO {{object_name}} (label) VALUES ('a'),('b'),('c')",
            "post_query": "SELECT id, label FROM {{object_name}} ORDER BY id",
        }
    )
    # 영구 테이블로 catalog 부재를 실제로 확인(temp면 연결 종료로 가려짐).
    r = runner.run_case(case)
    assert r.status == "fail" and r.stage == "pg.statement"  # AUTO_INCREMENT PG 미지원
    name = safe_object_name("auto-increment", "tmp_ai")
    for cfg, dialect in [(MYSQL_CONFIG, "mysql"), (POSTGRES_CONFIG, "postgres")]:
        with Executor.connect(cfg, dialect) as ex:
            r2 = ex.run_query(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s",
                (name,),
            )
            assert r2.rows[0][0] == 0  # 양 DB catalog 부재
