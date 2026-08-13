"""Tests for src.self_consistency.check_self_consistency (Build 1, Day 4,
Part 1). Formalizes the verification run during development into a permanent
pytest module, rather than a throwaway scratch script -- unlike Days 1-3,
where verification was documented in CONTEXT.md but not committed.

Also covers compute_self_consistency_dollar_impacts (Build 1, Day 6, Task
2): execution-based dollar_impact computation, replacing the Day 4
placeholder 0.0 for Case 4 and Case 7's self-consistency issues."""

import pytest

from config import DATA_SAMPLE_DIR
from src.self_consistency import check_self_consistency, compute_self_consistency_dollar_impacts
from tests.fixtures.scenarios import (
    CASE_1_JOIN_TYPE,
    CASE_3_HYBRID_FALLBACK,
    CASE_4_GOVERNANCE_DRIFT,
    CASE_7_PRECEDENCE_CONFLICT,
    SCENARIOS,
)


def test_raises_on_missing_declared_definition():
    """No declared_definition means there is nothing to check the SQL
    against; this must fail loud, not silently return []."""
    with pytest.raises(TypeError):
        check_self_consistency(CASE_3_HYBRID_FALLBACK.source_b, "b")


def test_case_4_governance_drift_detects_excluded_statuses_drift():
    """A declares excluding cancelled AND refunded, but A's SQL only
    excludes cancelled -- exactly one issue, on excluded_statuses, medium
    confidence (mechanical, unambiguous extraction)."""
    issues = check_self_consistency(CASE_4_GOVERNANCE_DRIFT.source_a, "a")

    assert len(issues) == 1
    issue = issues[0]
    assert issue.source == "a"
    assert issue.declared_field == "excluded_statuses"
    assert issue.declared_value == "cancelled, refunded"
    assert issue.implemented_value == "cancelled"
    assert issue.confidence == "medium"
    assert issue.dollar_impact == 0.0


def test_case_4_governance_drift_source_b_is_self_consistent():
    """B's SQL matches B's own declared definition -- no issues."""
    assert check_self_consistency(CASE_4_GOVERNANCE_DRIFT.source_b, "b") == []


def test_case_1_negative_control_both_sides_self_consistent():
    """Both sides' SQL matches their own declared definitions -- [] on
    both, same negative-control role Case 1 plays for prior tools."""
    assert check_self_consistency(CASE_1_JOIN_TYPE.source_a, "a") == []
    assert check_self_consistency(CASE_1_JOIN_TYPE.source_b, "b") == []


def test_all_scenarios_run_without_error_where_declared_definition_present():
    """Every source with a declared_definition across all 6 scenarios must
    run through check_self_consistency without raising; sides with no
    declared_definition are not applicable and are skipped here (covered
    by test_raises_on_missing_declared_definition instead)."""
    for scenario in SCENARIOS:
        for side, source in (("a", scenario.source_a), ("b", scenario.source_b)):
            if source.declared_definition is None:
                continue
            check_self_consistency(source, side)


def test_case_4_dollar_impact_matches_expected_200():
    """Case 4's self-consistency issue: A's SQL as-written excludes only
    cancelled (300.0), but A declares excluding cancelled+refunded
    (100.0 if corrected). The Day 4 close-out already confirmed this
    200.0 dollar effect by hand; this proves compute_self_consistency_dollar_impacts
    reproduces it mechanically."""
    db = str(DATA_SAMPLE_DIR / "case_04_governance_drift_a.duckdb")
    issues = compute_self_consistency_dollar_impacts(CASE_4_GOVERNANCE_DRIFT.source_a, "a", db)

    assert len(issues) == 1
    assert issues[0].declared_field == "excluded_statuses"
    assert issues[0].dollar_impact == 200.0


def test_case_7_dollar_impact_matches_expected_100():
    """Case 7's self-consistency issue: A's SQL as-written excludes only
    refunded (300.0), but A declares excluding cancelled only (400.0 if
    corrected). The Day 4 close-out already confirmed this 100.0 dollar
    effect by hand; this proves compute_self_consistency_dollar_impacts
    reproduces it mechanically."""
    db = str(DATA_SAMPLE_DIR / "case_07_precedence_conflict_a.duckdb")
    issues = compute_self_consistency_dollar_impacts(CASE_7_PRECEDENCE_CONFLICT.source_a, "a", db)

    assert len(issues) == 1
    assert issues[0].declared_field == "excluded_statuses"
    assert issues[0].dollar_impact == 100.0


def test_dollar_impact_empty_for_every_other_self_consistent_side():
    """Across all 7 fixtures, only Case 4 side A and Case 7 side A have a
    self-consistency issue at all. Every other side with a declared
    definition must resolve to an empty list -- not silently skipped, not
    silently assumed, actually checked."""
    non_empty_sides = set()
    for scenario in SCENARIOS:
        for side, source in (("a", scenario.source_a), ("b", scenario.source_b)):
            if source.declared_definition is None:
                continue
            db = str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_{side}.duckdb")
            issues = compute_self_consistency_dollar_impacts(source, side, db)
            if issues:
                non_empty_sides.add((scenario.scenario_id, side))

    assert non_empty_sides == {
        ("case_04_governance_drift", "a"),
        ("case_07_precedence_conflict", "a"),
    }
