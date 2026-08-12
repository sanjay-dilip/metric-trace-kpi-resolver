"""Tests for src.self_consistency.assemble_structural_and_definitional_evidence
(Build 1, Day 5, Task 1b): implements decision 10 (docs/decisions.md) --
sql_diff's `distinct` finding is suppressed in favor of definition_diff's
`aggregation` finding when both trace to the same underlying COUNT/COUNT
DISTINCT fact. A Case 2 audit found decision 10 had been recorded in the
decision log with no enforcing code anywhere; this closes that gap."""

from src.definition_diff import diff_definitions
from src.self_consistency import assemble_structural_and_definitional_evidence
from src.scenario import DashboardSource, DeclaredDefinition
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.scenarios import CASE_2_MULTI_CAUSE, SCENARIOS


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


def test_no_regressions_across_all_7_fixtures():
    """Only Case 2 should be affected by this rule; every other fixture's
    sql_differences/definition_differences must pass through unchanged."""
    for scenario in SCENARIOS:
        sql_diffs = diff_sql(parse_sql(scenario.source_a.sql), parse_sql(scenario.source_b.sql))
        def_diffs = diff_definitions(scenario.source_a, scenario.source_b)

        sql_after, def_after = assemble_structural_and_definitional_evidence(
            sql_diffs, def_diffs
        )

        if scenario.scenario_id == "case_02_multi_cause":
            assert [d.category for d in sql_after] == []
        else:
            assert [d.category for d in sql_after] == [d.category for d in sql_diffs]
        assert [d.field for d in def_after] == [d.field for d in def_diffs]
