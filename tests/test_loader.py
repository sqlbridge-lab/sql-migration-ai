from pathlib import Path

from harness.loader import load_case, load_corpus


def test_dql_basic_and_ordered():
    c = load_case(
        {
            "id": "x",
            "kind": "dql",
            "concepts": ["limit-pagination"],
            "mysql": "SELECT 1",
            "ordered": True,
        }
    )
    assert c.id == "x" and c.kind == "dql" and c.mysql == "SELECT 1"
    assert c.ordered is True
    assert c.control_mysql == {} and c.control_postgres == {}


def test_ordered_defaults_false_when_absent():
    c = load_case(
        {"id": "x", "kind": "dql", "concepts": ["limit-pagination"], "mysql": "S"}
    )
    assert c.ordered is False


def test_dml_ordered_loaded():
    c = load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "X",
            "post_query": "SELECT 1",
            "ordered": True,
        }
    )
    assert c.ordered is True


def test_common_control_copied_to_both():
    c = load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "UPDATE t SET x=2",
            "post_query": "SELECT x FROM t",
        }
    )
    assert c.control_mysql["post_query"] == "SELECT x FROM t"
    assert c.control_postgres["post_query"] == "SELECT x FROM t"


def test_db_specific_pair_split():
    c = load_case(
        {
            "id": "p",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "X",
            "post_query": "SELECT 1",
            "setup_mysql": "SET @x=1",
            "setup_postgres": "SELECT 1",
        }
    )
    assert c.control_mysql["setup"] == "SET @x=1"
    assert c.control_postgres["setup"] == "SELECT 1"


def test_dml_exercise_loaded_both():
    c = load_case(
        {
            "id": "e",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "X",
            "post_query": "SELECT 1",
            "exercise": "INSERT INTO t VALUES (9)",
        }
    )
    assert c.control_mysql["exercise"] == "INSERT INTO t VALUES (9)"
    assert c.control_postgres["exercise"] == "INSERT INTO t VALUES (9)"


def test_ddl_setup_and_object():
    c = load_case(
        {
            "id": "d",
            "kind": "ddl",
            "isolation": "fresh",
            "concepts": ["auto-increment"],
            "statement": "CREATE ...",
            "object": {"type": "table", "name": "tmp"},
            "setup": "SELECT 1",
        }
    )
    assert c.control_mysql["setup"] == "SELECT 1"
    assert c.object == {"type": "table", "name": "tmp"}


def test_load_real_corpus():
    root = Path(__file__).resolve().parent.parent
    cases = load_corpus(root / "corpus" / "cases", root / "corpus" / "concepts.yaml")
    assert len(cases) == 14
    date_case = next(c for c in cases if c.id == "date-function")
    assert date_case.nondeterministic == {"strategy": "fixed_clock"}
    assert date_case.ordered is True  # Task 0에서 명시함
    upsert = next(c for c in cases if c.id == "upsert-on-duplicate")
    assert "setup" not in upsert.control_mysql  # Task 0에서 setup 제거
    assert upsert.control_mysql["post_query"]
