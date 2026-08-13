"""Tests for src.query_mutation (Build 1, Day 6, Task 1): the rule-based
corrected-query mutator that replaces Task 2's hand-authored counterfactual
SQL. Each covered category is validated the same way -- confirm the
mechanically-constructed query reproduces the same executed result as the
already hand-verified "X-only"/"Y-only" SQL from tests/test_reconciliation.py
-- plus a fail-loud check for an unsupported category."""

import duckdb
import pytest

from config import DATA_SAMPLE_DIR
from src.definition_diff import diff_definitions
from src.query_mutation import (
    apply_aggregation_correction,
    apply_date_field_correction,
    apply_excluded_statuses_correction,
    apply_join_type_correction,
    construct_corrected_query,
)
from src.schema import SQLStructuralDifference
from tests.fixtures.scenarios import CASE_1_JOIN_TYPE, CASE_2_MULTI_CAUSE, CASE_3_HYBRID_FALLBACK


def _execute_scalar(db_path: str, sql: str) -> float:
    con = duckdb.connect(db_path, read_only=True)
    try:
        return float(con.execute(sql).fetchone()[0])
    finally:
        con.close()


def test_excluded_statuses_correction_matches_hand_verified_case_2_x_only():
    """Task 2's hand-written x_only_sql for Case 2 excludes churned+trial via
    COUNT(DISTINCT ...) -- same result the mechanical mutation should reach
    when applied to source A's original (churned-only) SQL."""
    db = str(DATA_SAMPLE_DIR / "case_02_multi_cause_a.duckdb")
    hand_written_x_only = (
        "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned', 'trial') AND signup_date >= '2024-01-01'"
    )
    mechanical = apply_excluded_statuses_correction(CASE_2_MULTI_CAUSE.source_a.sql, ["churned", "trial"])

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written_x_only) == 3.0


def test_aggregation_correction_matches_hand_verified_case_2_y_only():
    """Task 2's hand-written y_only_sql for Case 2 swaps A's COUNT(DISTINCT)
    to a plain COUNT -- same result the mechanical mutation should reach."""
    db = str(DATA_SAMPLE_DIR / "case_02_multi_cause_a.duckdb")
    hand_written_y_only = (
        "SELECT COUNT(customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned') AND signup_date >= '2024-01-01'"
    )
    mechanical = apply_aggregation_correction(CASE_2_MULTI_CAUSE.source_a.sql, "count")

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written_y_only) == 7.0


def test_date_field_correction_matches_hand_verified_case_3_x_only():
    """Task 2's hand-written x_only_sql for Case 3 swaps A's order_date for
    created_at -- same result the mechanical mutation should reach."""
    db = str(DATA_SAMPLE_DIR / "case_03_hybrid_fallback_a.duckdb")
    hand_written_x_only = (
        "SELECT SUM(amount) AS revenue FROM orders WHERE status != 'cancelled' AND created_at >= '2024-01-01'"
    )
    mechanical = apply_date_field_correction(CASE_3_HYBRID_FALLBACK.source_a.sql, "created_at")

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written_x_only) == 1150.0


def test_excluded_statuses_correction_matches_hand_verified_case_3_y_only():
    """Task 2's hand-written y_only_sql for Case 3 widens A's exclusion set
    to cancelled+refunded -- same result the mechanical mutation should reach."""
    db = str(DATA_SAMPLE_DIR / "case_03_hybrid_fallback_a.duckdb")
    hand_written_y_only = (
        "SELECT SUM(amount) AS revenue FROM orders "
        "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
    )
    mechanical = apply_excluded_statuses_correction(CASE_3_HYBRID_FALLBACK.source_a.sql, ["cancelled", "refunded"])

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written_y_only) == 250.0


def test_join_type_correction_matches_hand_verified_case_1_source_b():
    """Case 1's single_cause_attribution test compares source A's LEFT JOIN
    against source B's INNER JOIN directly; mechanically correcting A's join
    to INNER should reproduce B's exact executed result (300.0)."""
    db = str(DATA_SAMPLE_DIR / "case_01_join_type_a.duckdb")
    mechanical = apply_join_type_correction(CASE_1_JOIN_TYPE.source_a.sql, "INNER")

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, CASE_1_JOIN_TYPE.source_b.sql) == 300.0


def test_construct_corrected_query_dispatches_real_definition_differences():
    """The dispatcher, fed the actual DefinitionDifference objects Day 3's
    diff_definitions produces for Case 2, should reach the same results as
    the direct per-category mutation calls above."""
    db = str(DATA_SAMPLE_DIR / "case_02_multi_cause_a.duckdb")
    differences = diff_definitions(CASE_2_MULTI_CAUSE.source_a, CASE_2_MULTI_CAUSE.source_b)
    fields = {d.field for d in differences}
    assert fields == {"excluded_statuses", "aggregation"}

    results = {
        d.field: _execute_scalar(db, construct_corrected_query(CASE_2_MULTI_CAUSE.source_a.sql, d))
        for d in differences
    }
    assert results == {"excluded_statuses": 3.0, "aggregation": 7.0}


def test_construct_corrected_query_fails_loudly_on_unsupported_category():
    """'filter' is a real SQLStructuralDifference category (src/schema.py)
    with no mutation rule -- the dispatcher must raise, not silently pass
    the SQL through unmodified."""
    unsupported = SQLStructuralDifference(
        category="filter",
        description="source_a filters on 'region', source_b has no equivalent filter",
        query_a_snippet="region = 'us'",
        query_b_snippet="(no filter on this column)",
    )
    with pytest.raises(ValueError, match="filter"):
        construct_corrected_query(CASE_2_MULTI_CAUSE.source_a.sql, unsupported)


@pytest.mark.parametrize("category", ["distinct", "grouping", "other"])
def test_construct_corrected_query_fails_loudly_on_every_uncovered_category(category):
    """Same fail-loud contract for the other three uncovered
    SQLStructuralDifference categories, not just 'filter'."""
    unsupported = SQLStructuralDifference(
        category=category,
        description="test",
        query_a_snippet="a",
        query_b_snippet="b",
    )
    with pytest.raises(ValueError):
        construct_corrected_query(CASE_2_MULTI_CAUSE.source_a.sql, unsupported)
