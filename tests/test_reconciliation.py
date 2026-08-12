"""Tests for src.reconciliation (Build 1, Day 5, Task 2): the pairwise
Shapley-averaging engine that replaces fixed-order sequential subtraction
(decision 11, docs/decisions.md), proven against Case 2 and Case 3 -- the
two cases whose real execution disproved naive subtraction -- plus Case 1's
single-cause degeneration behavior."""

import math

from config import DATA_SAMPLE_DIR
from src.reconciliation import shapley_pair_attribution, single_cause_attribution

_TOLERANCE = 1e-9


def test_case_2_shapley_attribution_matches_hand_verified_values():
    """A: COUNT(DISTINCT customer_id), excludes churned only.
    B: COUNT(customer_id), excludes churned+trial.
    Real execution (Day 4 close-out) already proved these interact --
    this confirms the engine reproduces those exact figures."""
    db = str(DATA_SAMPLE_DIR / "case_02_multi_cause_a.duckdb")
    baseline_sql = (
        "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned') AND signup_date >= '2024-01-01'"
    )
    x_only_sql = (
        "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned', 'trial') AND signup_date >= '2024-01-01'"
    )
    y_only_sql = (
        "SELECT COUNT(customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned') AND signup_date >= '2024-01-01'"
    )
    both_sql = (
        "SELECT COUNT(customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned', 'trial') AND signup_date >= '2024-01-01'"
    )

    x_attr, y_attr = shapley_pair_attribution(db, baseline_sql, x_only_sql, y_only_sql, both_sql)

    assert math.isclose(x_attr, -2.5, abs_tol=_TOLERANCE)
    assert math.isclose(y_attr, 1.5, abs_tol=_TOLERANCE)
    assert math.isclose(x_attr + y_attr, -1.0, abs_tol=_TOLERANCE)  # both(4) - baseline(5)

    # Confirm the Shapley figures genuinely differ from either naive fixed order.
    naive_excl_first = (-2.0, 1.0)
    naive_agg_first = (-3.0, 2.0)
    assert (x_attr, y_attr) != naive_excl_first
    assert (x_attr, y_attr) != naive_agg_first


def test_case_3_shapley_attribution_matches_hand_verified_values():
    """A: order_date, excludes cancelled only. B: created_at, excludes
    cancelled+refunded. The naive additive sum was off by 400.0 here
    (Day 4 close-out) -- confirms the engine reproduces the averaged
    figures that account for that."""
    db = str(DATA_SAMPLE_DIR / "case_03_hybrid_fallback_a.duckdb")
    baseline_sql = (
        "SELECT SUM(amount) AS revenue FROM orders "
        "WHERE status != 'cancelled' AND order_date >= '2024-01-01'"
    )
    x_only_sql = (
        "SELECT SUM(amount) AS revenue FROM orders "
        "WHERE status != 'cancelled' AND created_at >= '2024-01-01'"
    )
    y_only_sql = (
        "SELECT SUM(amount) AS revenue FROM orders "
        "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
    )
    both_sql = (
        "SELECT SUM(amount) AS revenue FROM orders "
        "WHERE status NOT IN ('cancelled', 'refunded') AND created_at >= '2024-01-01'"
    )

    x_attr, y_attr = shapley_pair_attribution(db, baseline_sql, x_only_sql, y_only_sql, both_sql)

    assert math.isclose(x_attr, 400.0, abs_tol=_TOLERANCE)
    assert math.isclose(y_attr, -500.0, abs_tol=_TOLERANCE)
    assert math.isclose(x_attr + y_attr, -100.0, abs_tol=_TOLERANCE)  # both(450) - baseline(550)

    naive_date_first = (600.0, -700.0)
    naive_status_first = (200.0, -300.0)
    assert (x_attr, y_attr) != naive_date_first
    assert (x_attr, y_attr) != naive_status_first


def test_case_1_single_cause_degenerates_to_full_delta_without_pairwise_engine():
    """Case 1 has exactly one cause (join_type). single_cause_attribution
    is used directly -- the pairwise engine is never invoked, per this
    module's documented design choice."""
    db = str(DATA_SAMPLE_DIR / "case_01_join_type_a.duckdb")
    a_sql = (
        "SELECT SUM(o.amount) AS revenue FROM orders o "
        "LEFT JOIN customers c ON o.customer_id = c.customer_id "
        "WHERE o.order_date >= '2024-01-01' AND o.status NOT IN ('cancelled', 'refunded')"
    )
    b_sql = (
        "SELECT SUM(o.amount) AS revenue FROM orders o "
        "INNER JOIN customers c ON o.customer_id = c.customer_id "
        "WHERE o.order_date >= '2024-01-01' AND o.status NOT IN ('cancelled', 'refunded')"
    )

    attribution = single_cause_attribution(db, a_sql, b_sql)

    assert math.isclose(attribution, -300.0, abs_tol=_TOLERANCE)  # B(300) - A(600)
