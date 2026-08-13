"""Tests for src.self_consistency.assemble_structural_and_definitional_evidence
(Build 1, Day 5, Task 1b): implements decision 10 (docs/decisions.md) --
sql_diff's `distinct` finding is suppressed in favor of definition_diff's
`aggregation` finding when both trace to the same underlying COUNT/COUNT
DISTINCT fact. A Case 2 audit found decision 10 had been recorded in the
decision log with no enforcing code anywhere; this closes that gap.

Also covers decision 12 (Build 1, Day 7, Task 1b): the same class of
collision, discovered a second time -- sql_diff's `date_field` finding and
definition_diff's `date_field` finding both trace to the same underlying
date-column swap for Case 3 (order_date vs. created_at), with no
suppression rule reconciling them before this task. Resolved the same way
decision 10 was, by the same function, with the same "same underlying
fact" precision standard (_same_date_field_fact, side-matched exact
equality, not mere category co-presence)."""

from src.definition_diff import diff_definitions
from src.self_consistency import assemble_structural_and_definitional_evidence
from src.scenario import DashboardSource, DeclaredDefinition
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.scenarios import CASE_2_MULTI_CAUSE, CASE_3_HYBRID_FALLBACK, SCENARIOS


def test_case_2_distinct_suppressed_aggregation_and_excluded_statuses_survive():
    """The real collision: A is COUNT(DISTINCT customer_id), B is
    COUNT(customer_id). sql_diff fires `distinct`, definition_diff fires
    `aggregation` (count_distinct vs count) for the same fact -- `distinct`
    must be removed, `aggregation` must survive untouched, and the unrelated
    `excluded_statuses` finding must pass through regardless."""
    sql_diffs = diff_sql(
        parse_sql(CASE_2_MULTI_CAUSE.source_a.sql), parse_sql(CASE_2_MULTI_CAUSE.source_b.sql)
    )
    def_diffs = diff_definitions(CASE_2_MULTI_CAUSE.source_a, CASE_2_MULTI_CAUSE.source_b)

    assert "distinct" in {d.category for d in sql_diffs}
    assert {d.field for d in def_diffs} == {"excluded_statuses", "aggregation"}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "distinct" not in {d.category for d in sql_after}
    assert {d.field for d in def_after} == {"excluded_statuses", "aggregation"}


def test_does_not_over_fire_on_unrelated_distinct_and_aggregation_findings():
    """A distinct-category finding and an aggregation-category finding can
    co-occur without tracing to the same fact: A is COUNT(DISTINCT
    customer_id), B is MAX(customer_id) -- a genuinely different aggregation
    function, not just "the same function without DISTINCT". Suppression
    must not fire here; both findings are independently real."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT COUNT(DISTINCT customer_id) AS x FROM customers "
            "WHERE status != 'churned' AND signup_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date", excluded_statuses=["churned"], aggregation="count_distinct"
        ),
    )
    source_b = DashboardSource(
        label="finance_query",
        sql=(
            "SELECT MAX(customer_id) AS x FROM customers "
            "WHERE status != 'churned' AND signup_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date", excluded_statuses=["churned"], aggregation="max"
        ),
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert "distinct" in {d.category for d in sql_diffs}
    assert "aggregation" in {d.field for d in def_diffs}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "distinct" in {d.category for d in sql_after}
    assert def_after == def_diffs


def test_case_3_date_field_suppressed_excluded_statuses_survives():
    """The real collision (decision 12): Case 3's A uses order_date, B uses
    created_at -- sql_diff fires `date_field` structurally, definition_diff
    fires `date_field` definitionally (inferred, since B has no declared
    definition), both describing the exact same column swap. `date_field`
    must be removed from sql_differences; definition_differences (both
    date_field and the unrelated excluded_statuses finding) must survive
    untouched."""
    sql_diffs = diff_sql(parse_sql(CASE_3_HYBRID_FALLBACK.source_a.sql), parse_sql(CASE_3_HYBRID_FALLBACK.source_b.sql))
    def_diffs = diff_definitions(CASE_3_HYBRID_FALLBACK.source_a, CASE_3_HYBRID_FALLBACK.source_b)

    assert "date_field" in {d.category for d in sql_diffs}
    assert {d.field for d in def_diffs} == {"date_field", "excluded_statuses"}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "date_field" not in {d.category for d in sql_after}
    assert {d.field for d in def_after} == {"date_field", "excluded_statuses"}


def test_does_not_over_fire_on_unrelated_date_field_findings():
    """A date_field structural finding and a date_field definitional
    finding can co-occur without tracing to the same fact: A's WHERE clause
    references TWO date-like columns (order_date, updated_at -- a
    genuinely ambiguous structural finding, not one clean column swap),
    while A/B's DECLARED date_field values differ on a completely
    unrelated pair (order_date vs. ship_date). Both sides are self-
    consistent (declared values match their own SQL's unambiguous or
    best-guess implementation), so Day 4's precedence rule does not
    interfere -- this isolates decision 12's own suppression logic.
    Suppression must not fire; both findings are independently real."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND updated_at <= '2024-06-01'"
        ),
        declared_definition=DeclaredDefinition(date_field="order_date", excluded_statuses=[], aggregation="sum"),
    )
    source_b = DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE ship_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(date_field="ship_date", excluded_statuses=[], aggregation="sum"),
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert "date_field" in {d.category for d in sql_diffs}
    assert "date_field" in {d.field for d in def_diffs}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "date_field" in {d.category for d in sql_after}
    assert def_after == def_diffs


def test_no_regressions_across_all_7_fixtures():
    """Case 2 (decision 10, distinct/aggregation) and Case 3 (decision 12,
    date_field/date_field) are the only fixtures affected by this rule;
    every other fixture's sql_differences/definition_differences must pass
    through unchanged."""
    affected = {
        "case_02_multi_cause": ["distinct"],
        "case_03_hybrid_fallback": ["date_field"],
    }
    for scenario in SCENARIOS:
        sql_diffs = diff_sql(parse_sql(scenario.source_a.sql), parse_sql(scenario.source_b.sql))
        def_diffs = diff_definitions(scenario.source_a, scenario.source_b)

        sql_after, def_after = assemble_structural_and_definitional_evidence(
            sql_diffs, def_diffs
        )

        suppressed_categories = affected.get(scenario.scenario_id, [])
        expected_categories = [d.category for d in sql_diffs if d.category not in suppressed_categories]
        assert [d.category for d in sql_after] == expected_categories
        assert [d.field for d in def_after] == [d.field for d in def_diffs]
