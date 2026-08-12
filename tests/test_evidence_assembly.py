"""Tests for src.self_consistency.assemble_definitional_evidence (Build 1,
Day 4, Part 2): the precedence-suppression rule wiring check_self_consistency
(Day 4 Part 1) together with diff_definitions (Day 3)."""

from src.definition_diff import diff_definitions
from src.self_consistency import assemble_definitional_evidence
from tests.fixtures.scenarios import (
    CASE_1_JOIN_TYPE,
    CASE_2_MULTI_CAUSE,
    CASE_4_GOVERNANCE_DRIFT,
    CASE_6_NEGATIVE_CONTROL,
    SCENARIOS,
)


def test_case_4_excluded_statuses_reported_once_via_self_consistency():
    """Case 4's excluded_statuses drift must surface exactly once, through
    self_consistency_issues, never duplicated into definition_differences.

    The double-count risk this rule guards against is not actually
    reachable in Case 4 as constructed: A and B declare the identical
    excluded_statuses, so diff_definitions (declared-vs-declared) already
    returns [] for that field before the precedence rule ever runs --
    diff_declared_definitions compares declared values only and never
    touches A's SQL-implemented value, so there was nothing for it to
    duplicate. The rule is exercised here (nothing incorrectly survives)
    but not proven necessary by this fixture; see the module docstring for
    the hypothetical shape (declared values differing cross-source AND a
    self-consistency issue on the same field) where it would matter.
    """
    dd, sci = assemble_definitional_evidence(
        CASE_4_GOVERNANCE_DRIFT.source_a, CASE_4_GOVERNANCE_DRIFT.source_b
    )

    assert [d.field for d in dd] == []
    assert len(sci) == 1
    assert sci[0].declared_field == "excluded_statuses"
    assert sci[0].source == "a"

    # Confirm diff_definitions never produced this field in the first place.
    raw_cross_source = diff_definitions(
        CASE_4_GOVERNANCE_DRIFT.source_a, CASE_4_GOVERNANCE_DRIFT.source_b
    )
    assert "excluded_statuses" not in {d.field for d in raw_cross_source}


def test_case_2_unchanged_when_nothing_to_suppress():
    """Case 2 has genuine cross-source conflicts and no self-consistency
    issues; the precedence rule must leave diff_definitions's output
    untouched -- identical to Day 3's verified baseline."""
    baseline = diff_definitions(CASE_2_MULTI_CAUSE.source_a, CASE_2_MULTI_CAUSE.source_b)
    dd, sci = assemble_definitional_evidence(
        CASE_2_MULTI_CAUSE.source_a, CASE_2_MULTI_CAUSE.source_b
    )

    assert [d.model_dump() for d in dd] == [d.model_dump() for d in baseline]
    assert sci == []


def test_case_1_negative_control_both_lists_empty():
    dd, sci = assemble_definitional_evidence(
        CASE_1_JOIN_TYPE.source_a, CASE_1_JOIN_TYPE.source_b
    )
    assert dd == []
    assert sci == []


def test_case_6_negative_control_both_lists_empty():
    dd, sci = assemble_definitional_evidence(
        CASE_6_NEGATIVE_CONTROL.source_a, CASE_6_NEGATIVE_CONTROL.source_b
    )
    assert dd == []
    assert sci == []


def test_all_scenarios_run_without_error():
    """Every scenario, including the fully-undeclared Case 6 and the
    hybrid-fallback Case 3, must run through assemble_definitional_evidence
    without raising -- None-handling is this function's own job, not
    something callers must guard against."""
    for scenario in SCENARIOS:
        assemble_definitional_evidence(scenario.source_a, scenario.source_b)
