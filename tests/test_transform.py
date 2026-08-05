from harness.transform import PassThroughTransformer, fix_clock


def test_passthrough_returns_input_unchanged():
    sql = "SELECT `id` FROM `products` LIMIT 10, 5"
    assert PassThroughTransformer().transform(sql) == sql


def test_fix_clock_replaces_mysql_now():
    out = fix_clock("SELECT NOW()", "2025-06-01 12:00:00", dialect="mysql")
    assert "NOW" not in out.upper()
    assert "2025-06-01 12:00:00" in out


def test_fix_clock_replaces_current_timestamp():
    out = fix_clock(
        "SELECT CURRENT_TIMESTAMP", "2025-06-01 12:00:00", dialect="postgres"
    )
    assert "CURRENT_TIMESTAMP" not in out.upper()
    assert "2025-06-01 12:00:00" in out


def test_fix_clock_replaces_curdate():
    out = fix_clock("SELECT CURDATE()", "2025-06-01 12:00:00", dialect="mysql")
    assert "CURDATE" not in out.upper()
    assert "2025-06-01" in out


def test_fix_clock_in_where_clause():
    out = fix_clock(
        "SELECT id FROM orders WHERE ordered_at < NOW()",
        "2025-06-01 12:00:00",
        dialect="mysql",
    )
    assert "NOW" not in out.upper()
    assert "2025-06-01 12:00:00" in out


def test_fix_clock_leaves_non_clock_unchanged():
    out = fix_clock(
        "SELECT id FROM products ORDER BY id",
        "2025-06-01 12:00:00",
        dialect="postgres",
    )
    assert "2025-06-01" not in out
