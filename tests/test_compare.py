from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from harness.compare import compare, row_equal


def test_identical_rows_equal():
    cols = ["id", "name"]
    rows = [(1, "a"), (2, "b")]
    r = compare(cols, rows, cols, list(rows), ordered=True)
    assert r.equal and r.reason is None


def test_column_name_mismatch_fails():
    r = compare(["id"], [(1,)], ["other"], [(1,)], ordered=True)
    assert not r.equal
    assert "컬럼" in (r.reason or "")


def test_ordered_true_order_matters():
    assert not compare(["id"], [(1,), (2,)], ["id"], [(2,), (1,)], ordered=True).equal


def test_ordered_false_order_ignored():
    assert compare(["id"], [(1,), (2,)], ["id"], [(2,), (1,)], ordered=False).equal


def test_duplicate_count_mismatch_fails():
    assert not compare(["id"], [(1,), (1,)], ["id"], [(1,), (2,)], ordered=False).equal


# P1-4: 정수·Decimal 정확 비교
def test_large_ints_not_approximated():
    assert not row_equal((10**12,), (10**12 + 1,))  # 다른 큰 정수는 다름


def test_decimal_scale_equal_but_value_exact():
    assert row_equal((Decimal("10.00"),), (Decimal("10.0"),))  # 스케일만 흡수
    # float로 붕괴시키면 두 값이 같아질 만큼 큰 정수 — Decimal 정확 비교라 다르게 판정
    assert not row_equal((Decimal(9007199254740992),), (Decimal(9007199254740993),))


def test_int_one_vs_bool_true_equal():
    assert row_equal((1,), (True,))
    assert not row_equal((2,), (True,))  # 2는 True 아님
    assert not row_equal((True,), (1.0000000005,))  # bool은 float 근사 안 함


def test_datetime_same_instant_diff_repr_equal():
    utc = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    kst = datetime(2025, 6, 1, 21, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    assert row_equal((utc,), (kst,))  # 같은 순간


def test_datetime_naive_treated_utc():
    naive = datetime(2025, 6, 1, 12, 0, 0)  # noqa: DTZ001  # naive 의도(UTC 취급 검증)
    aware = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    assert row_equal((naive,), (aware,))


def test_float_within_and_outside_tolerance():
    assert row_equal((3.3333333331,), (3.3333333329,))
    assert not row_equal((3.30,), (3.31,))


# P1-3: 탐욕이면 실패하지만 최대 매칭이면 성공하는 반례
def test_bipartite_matching_beats_greedy():
    cols = ["v"]
    a = [(0.9e-9,), (0.0,)]
    b = [(0.0,), (1.8e-9,)]  # 완전 매칭: 0.9e-9↔1.8e-9, 0↔0
    assert compare(cols, a, cols, b, ordered=False).equal


def test_float_approx_unordered_matches():
    cols = ["v"]
    a = [(3.0000000001,), (5.0,)]
    b = [(5.0,), (3.0000000002,)]
    assert compare(cols, a, cols, b, ordered=False).equal


def test_duplicate_float_rows_count_matters():
    cols = ["v"]
    a = [(3.0000000001,), (3.0000000002,)]
    b = [(3.0,), (3.0,)]
    assert compare(cols, a, cols, b, ordered=False).equal
    assert not compare(cols, [(3.0000000001,)], cols, b, ordered=False).equal


def test_null_rows_compared():
    assert compare(["a", "b"], [(None, 1)], ["a", "b"], [(None, 1)], ordered=True).equal
    assert not row_equal((None,), (1,))


def test_column_count_mismatch_fails():
    assert not compare(["a"], [(1,)], ["a", "b"], [(1, 2)], ordered=True).equal


def test_exclude_columns_drops_column():
    cols = ["id", "created_at"]
    a = [(1, "2025-06-01"), (2, "2025-06-02")]
    b = [(1, "2099-01-01"), (2, "2099-01-02")]
    assert compare(cols, a, cols, b, ordered=True, exclude_columns=["created_at"]).equal


def test_exclude_columns_missing_target_is_noop():
    # 없는 열 제외는 무해(전체 비교)
    cols = ["id"]
    assert compare(
        cols, [(1,)], cols, [(1,)], ordered=True, exclude_columns=["nope"]
    ).equal
