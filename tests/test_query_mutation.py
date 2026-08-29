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
    apply_filter_correction,
    apply_join_type_correction,
    construct_corrected_query,
)
from src.schema import SQLStructuralDifference
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.ambiguous_scenarios import AMBIGUOUS_ATTRIBUTION
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

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written_x_only) == 200.0


def test_aggregation_correction_matches_hand_verified_case_2_y_only():
    """Task 2's hand-written y_only_sql for Case 2 swaps A's COUNT(DISTINCT)
    to a plain COUNT -- same result the mechanical mutation should reach."""
    db = str(DATA_SAMPLE_DIR / "case_02_multi_cause_a.duckdb")
    hand_written_y_only = (
        "SELECT COUNT(customer_id) AS active_customers FROM customers "
        "WHERE status NOT IN ('churned') AND signup_date >= '2024-01-01'"
    )
    mechanical = apply_aggregation_correction(CASE_2_MULTI_CAUSE.source_a.sql, "count")

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written_y_only) == 420.0


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


def test_excluded_statuses_correction_with_empty_target_removes_filter_entirely():
    """Build 3, Day 1, Part 5: an empty target_statuses list ("corrected
    toward zero exclusions", src.definition_diff's '(none)' inference
    result) must remove the status-exclusion predicate entirely -- not
    rewrite it to a vacuous NOT IN (), the locked design decision this
    session implements. Covers both AND-chain shapes: a status predicate
    AND-ed with another condition (Case 13's own source_a.sql shape)
    collapses to just the remaining condition; a status predicate that is
    the query's ONLY WHERE condition drops the WHERE clause altogether."""
    multi_condition_sql = (
        "SELECT SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01' AND status NOT IN ('churned')"
    )
    corrected = apply_excluded_statuses_correction(multi_condition_sql, [])
    assert "status" not in corrected.lower()
    assert "order_date" in corrected

    single_condition_sql = "SELECT SUM(amount) AS revenue FROM orders WHERE status NOT IN ('churned')"
    corrected_single = apply_excluded_statuses_correction(single_condition_sql, [])
    assert "where" not in corrected_single.lower()


def test_excluded_statuses_correction_zero_predicate_add_matches_hand_verified_result():
    """Build 3, Day 2, Part 14: apply_excluded_statuses_correction's new
    zero-predicate ADD case, hand-verified against real Case 2 seed data
    the same way every other correction function in this module is. A
    query with NO status predicate at all (real Case 2 customers table,
    date filter only, real count 350) mechanically gains a new NEQ
    exclusion predicate and reproduces the hand-written equivalent's real
    executed result (350 -> 300, excluding 'churned') -- not just an AST
    shape assertion."""
    db = str(DATA_SAMPLE_DIR / "case_02_multi_cause_a.duckdb")
    no_filter_sql = "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers WHERE signup_date >= '2024-01-01'"
    hand_written = (
        "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers "
        "WHERE signup_date >= '2024-01-01' AND status <> 'churned'"
    )

    mechanical = apply_excluded_statuses_correction(no_filter_sql, ["churned"])

    assert _execute_scalar(db, no_filter_sql) == 350.0
    assert _execute_scalar(db, mechanical) == _execute_scalar(db, hand_written) == 300.0


def test_excluded_statuses_correction_zero_predicate_add_multiple_statuses():
    """Same zero-predicate ADD case, multiple target statuses -- must build
    a NOT IN (...) predicate, not just the single-value NEQ shape."""
    corrected = apply_excluded_statuses_correction(
        "SELECT COUNT(id) FROM t WHERE signup_date <= '2024-06-01'", ["lost", "open"]
    )
    assert "NOT status IN ('lost', 'open')" in corrected


def test_excluded_statuses_correction_zero_predicate_no_where_clause_at_all():
    """The zero-predicate ADD case must also handle a query with no WHERE
    clause whatsoever, mirroring apply_filter_correction's own verified
    "create a WHERE from scratch" behavior (Part 13) -- not assumed to
    transfer, confirmed directly."""
    corrected = apply_excluded_statuses_correction("SELECT COUNT(id) FROM t", ["suspended"])
    assert corrected == "SELECT COUNT(id) FROM t WHERE status <> 'suspended'"


def test_excluded_statuses_correction_zero_predicate_empty_target_is_a_noop():
    """Zero existing predicates AND an empty target (already correct
    toward zero exclusions) must return the SQL unchanged, not raise --
    this shape shouldn't arise from a real diff_definitions comparison,
    but is handled explicitly rather than silently mishandled."""
    sql = "SELECT COUNT(id) FROM t WHERE signup_date <= '2024-06-01'"
    assert apply_excluded_statuses_correction(sql, []) == sql


def test_excluded_statuses_correction_multiple_predicates_still_raises_unaffected():
    """The existing multiple-predicates-raise discipline (populated path)
    is untouched by Part 14's zero-predicate branch -- confirm directly,
    not just by re-running Cases 2/4/7's own single-predicate fixtures."""
    with pytest.raises(ValueError, match="requires exactly one status-filter predicate; found 2"):
        apply_excluded_statuses_correction(
            "SELECT SUM(amount) FROM t WHERE status NOT IN ('a') AND status != 'b'", ["x"]
        )


def test_join_type_correction_matches_hand_verified_case_1_source_b():
    """Case 1's single_cause_attribution test compares source A's LEFT JOIN
    against source B's INNER JOIN directly; mechanically correcting A's join
    to INNER should reproduce B's exact executed result (300.0)."""
    db = str(DATA_SAMPLE_DIR / "case_01_join_type_a.duckdb")
    mechanical = apply_join_type_correction(CASE_1_JOIN_TYPE.source_a.sql, "INNER")

    assert _execute_scalar(db, mechanical) == _execute_scalar(db, CASE_1_JOIN_TYPE.source_b.sql) == 300.0


def test_filter_correction_matches_hand_verified_ambiguous_attribution_add_direction():
    """Build 3, Day 2, Part 13: apply_filter_correction's ADD direction,
    hand-verified against real data the same way every other correction
    function in this module is. AMBIGUOUS_ATTRIBUTION's real, RAW
    (pre-suppression) `filter` finding (source_a lacks a status filter,
    source_b's is `NOT status IN ('lost', 'open')`) is a genuine
    ADD-direction case -- construct_corrected_query, given that finding,
    should add source_b's exact predicate to source_a's SQL and reproduce
    the real, execution-verified result (24000.0 -> 16500.0, excluding
    'lost'/'open' deals). Uses the RAW diff_sql output directly, not
    assemble_structural_and_definitional_evidence's post-suppression
    result: Build 3, Day 2, Part 15 (decision 22) now suppresses this
    exact finding for AMBIGUOUS_ATTRIBUTION (confidence='high', same fact
    as its excluded_statuses finding) -- this test validates
    apply_filter_correction's own mechanics on a real, genuinely-occurring
    filter finding, independent of whether current suppression rules keep
    or remove it downstream."""
    filter_difference = next(
        d
        for d in diff_sql(parse_sql(AMBIGUOUS_ATTRIBUTION.source_a.sql), parse_sql(AMBIGUOUS_ATTRIBUTION.source_b.sql))
        if d.category == "filter"
    )

    db = str(DATA_SAMPLE_DIR / "ambiguous_attribution_a.duckdb")
    mechanical = construct_corrected_query(AMBIGUOUS_ATTRIBUTION.source_a.sql, filter_difference)

    assert _execute_scalar(db, AMBIGUOUS_ATTRIBUTION.source_a.sql) == 24000.0
    assert _execute_scalar(db, mechanical) == 16500.0

    direct = apply_filter_correction(AMBIGUOUS_ATTRIBUTION.source_a.sql, filter_difference.query_b_snippet)
    assert _execute_scalar(db, direct) == 16500.0


def test_filter_correction_rejects_adding_a_second_predicate_on_an_already_filtered_column():
    """Defensive check: src.sql_diff._diff_filters is presence-only, so a
    genuine `filter` finding should never pair a real predicate on BOTH
    sides of the same column -- if it somehow did, apply_filter_correction
    must raise rather than silently double-adding or overwriting."""
    already_filtered_sql = "SELECT SUM(amount) AS revenue FROM deals WHERE status = 'active'"
    with pytest.raises(ValueError, match="already has its own predicate"):
        apply_filter_correction(already_filtered_sql, "NOT status IN ('lost', 'open')")


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
    assert results == {"excluded_statuses": 200.0, "aggregation": 420.0}


def test_construct_corrected_query_filter_reverse_direction_still_fails_loudly():
    """'filter' gained a mutation rule in Build 3, Day 2, Part 13
    (apply_filter_correction) -- but ADD direction only, correcting a side
    MISSING the filter toward the other side's real predicate. This
    fixture's shape is the REVERSE direction (source_a HAS the filter,
    source_b's snippet is the "(no filter on this column)" placeholder --
    the default target, per construct_corrected_query's "adopt B's value"
    convention), which apply_filter_correction explicitly does not
    support -- the dispatcher must still raise, not silently pass the SQL
    through unmodified or misinterpret the placeholder as a real target."""
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
    """Same fail-loud contract for the three SQLStructuralDifference
    categories with NO mutation rule at all -- distinct from 'filter'
    (Part 13, above), which now has a rule for one direction only."""
    unsupported = SQLStructuralDifference(
        category=category,
        description="test",
        query_a_snippet="a",
        query_b_snippet="b",
    )
    with pytest.raises(ValueError):
        construct_corrected_query(CASE_2_MULTI_CAUSE.source_a.sql, unsupported)
