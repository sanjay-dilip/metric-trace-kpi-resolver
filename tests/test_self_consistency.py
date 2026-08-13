"""Tests for src.self_consistency.check_self_consistency (Build 1, Day 4,
Part 1). Formalizes the verification run during development into a permanent
pytest module, rather than a throwaway scratch script -- unlike Days 1-3,
where verification was documented in CONTEXT.md but not committed.

Also covers compute_self_consistency_dollar_impacts (Build 1, Day 6, Task
2): execution-based dollar_impact computation, replacing the Day 4
placeholder 0.0 for Case 4 and Case 7's self-consistency issues.

Also covers assemble_definitional_evidence_with_dollar_impacts (Build 1,
Day 7, Task 1, Part A): folding a suppressed cross-source
DefinitionDifference's dollar value into the surviving SelfConsistencyIssue,
proven against Case 7 (has a real suppressed counterpart) and Case 4
(confirmed to have none, so unaffected)."""

import os
import tempfile

import duckdb
import pytest

from config import DATA_SAMPLE_DIR
from src.definition_diff import diff_definitions
from src.scenario import DashboardSource, DeclaredDefinition
from src.self_consistency import (
    assemble_definitional_evidence_with_dollar_impacts,
    check_self_consistency,
    compute_self_consistency_dollar_impacts,
)
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


def test_case_4_dollar_impact_is_signed_positive_200():
    """Case 4's self-consistency issue: A's SQL as-written excludes only
    cancelled (300.0), but A declares excluding cancelled+refunded (100.0
    if corrected) -- A's own bug currently INFLATES A's reported number,
    which inflates known_gap (A - B) the same way, so per the Day 6
    close-out sign convention (src/schema.py, SelfConsistencyIssue)
    dollar_impact must be POSITIVE 200.0, not just magnitude 200.0."""
    db = str(DATA_SAMPLE_DIR / "case_04_governance_drift_a.duckdb")
    issues = compute_self_consistency_dollar_impacts(CASE_4_GOVERNANCE_DRIFT.source_a, "a", db)

    assert len(issues) == 1
    assert issues[0].declared_field == "excluded_statuses"
    assert issues[0].dollar_impact == 200.0


def test_case_7_dollar_impact_is_signed_negative_100():
    """Case 7's self-consistency issue: A's SQL as-written excludes only
    refunded (300.0), but A declares excluding cancelled only (400.0 if
    corrected) -- A's own bug currently UNDERSTATES A's reported number
    relative to its own declaration, which currently SUPPRESSES known_gap
    (A - B) below what it would be if this cause were fixed, so per the
    sign convention dollar_impact must be NEGATIVE 100.0."""
    db = str(DATA_SAMPLE_DIR / "case_07_precedence_conflict_a.duckdb")
    issues = compute_self_consistency_dollar_impacts(CASE_7_PRECEDENCE_CONFLICT.source_a, "a", db)

    assert len(issues) == 1
    assert issues[0].declared_field == "excluded_statuses"
    assert issues[0].dollar_impact == -100.0


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


def test_dollar_impact_sign_flips_for_a_source_b_issue():
    """None of the 7 committed fixtures produce a self-consistency issue on
    side B, so the source == "b" branch of compute_self_consistency_dollar_impacts's
    sign rule (src/schema.py, SelfConsistencyIssue docstring) is otherwise
    unexercised by any committed test. Constructs a minimal synthetic
    source/seed pair (a temp DuckDB file, not committed data) where B's SQL
    excludes MORE statuses than B declares -- B's own bug currently
    understates B, which per the sign convention should currently INFLATE
    known_gap (A - B), i.e. dollar_impact must be positive, the same
    direction single_cause_attribution's raw (corrected - original) delta
    already points for a source="b" issue (unlike source="a", where the
    sign must be negated)."""
    tmp_path = tempfile.mktemp(suffix=".duckdb")
    con = duckdb.connect(tmp_path)
    try:
        con.execute("CREATE TABLE orders (amount DOUBLE, status VARCHAR, order_date DATE)")
        con.execute(
            "INSERT INTO orders VALUES "
            "(100, 'completed', '2024-02-01'), (50, 'cancelled', '2024-02-01'), (30, 'refunded', '2024-02-01')"
        )
    finally:
        con.close()

    try:
        source_b = DashboardSource(
            label="finance_query",
            sql=(
                "SELECT SUM(amount) AS revenue FROM orders "
                "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
            ),
            declared_definition=DeclaredDefinition(
                date_field="order_date", excluded_statuses=["cancelled"], aggregation="sum"
            ),
        )

        issues = compute_self_consistency_dollar_impacts(source_b, "b", tmp_path)

        assert len(issues) == 1
        assert issues[0].source == "b"
        assert issues[0].declared_field == "excluded_statuses"
        assert issues[0].dollar_impact == 30.0  # (130 corrected) - (100 as-written), unflipped for source "b"
    finally:
        os.unlink(tmp_path)


def test_case_4_has_no_raw_cross_source_counterpart_to_suppress():
    """Verified independently (not assumed): Case 4's A and B declare the
    IDENTICAL excluded_statuses set, so diff_definitions's raw output is
    already empty for that field before precedence suppression ever runs --
    there is nothing to fold in."""
    raw = diff_definitions(CASE_4_GOVERNANCE_DRIFT.source_a, CASE_4_GOVERNANCE_DRIFT.source_b)
    assert raw == []


def test_case_7_has_a_real_raw_cross_source_counterpart_to_suppress():
    """Unlike Case 4: Case 7's A and B declare genuinely different
    excluded_statuses, so diff_definitions's raw output is non-empty for
    that field -- this is the real difference assemble_definitional_evidence
    suppresses and assemble_definitional_evidence_with_dollar_impacts must
    fold the dollar value of."""
    raw = diff_definitions(CASE_7_PRECEDENCE_CONFLICT.source_a, CASE_7_PRECEDENCE_CONFLICT.source_b)
    assert [d.field for d in raw] == ["excluded_statuses"]


def test_case_4_dollar_impact_unaffected_by_suppressed_cause_folding():
    """Case 4 has no suppressed cross-source counterpart (previous test), so
    assemble_definitional_evidence_with_dollar_impacts must produce the
    exact same dollar_impact as the unwrapped compute_self_consistency_dollar_impacts
    (200.0) -- folding a nonexistent suppressed cause must be a no-op. Since
    Build 1, Day 7, Task 3 recalibrated Case 4's known_gap to real seed
    execution (Decision 13's resolution), this dollar_impact now equals
    known_gap exactly too -- re-checked explicitly, not assumed to still
    hold from the pre-recalibration figure (200.0 vs. a mismatched-scale
    11500.0 known_gap)."""
    db_a = str(DATA_SAMPLE_DIR / "case_04_governance_drift_a.duckdb")
    db_b = str(DATA_SAMPLE_DIR / "case_04_governance_drift_b.duckdb")

    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_4_GOVERNANCE_DRIFT.source_a, CASE_4_GOVERNANCE_DRIFT.source_b, db_a, db_b
    )

    assert dd == []
    assert len(sci) == 1
    assert sci[0].dollar_impact == 200.0  # unchanged from compute_self_consistency_dollar_impacts alone
    assert sci[0].dollar_impact == CASE_4_GOVERNANCE_DRIFT.known_gap


def test_case_7_dollar_impact_combines_self_consistency_and_suppressed_cross_source():
    """Case 7's self-consistency correction alone is -100.0 (Day 6
    close-out). The suppressed cross-source excluded_statuses difference,
    applied on top of the self-consistency-corrected SQL toward B's
    declared value, contributes +300.0. Combined: +200.0 -- which equals
    Case 7's known_gap exactly, since excluded_statuses is the only
    differing field anywhere in Case 7's evidence."""
    db_a = str(DATA_SAMPLE_DIR / "case_07_precedence_conflict_a.duckdb")
    db_b = str(DATA_SAMPLE_DIR / "case_07_precedence_conflict_b.duckdb")

    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_7_PRECEDENCE_CONFLICT.source_a, CASE_7_PRECEDENCE_CONFLICT.source_b, db_a, db_b
    )

    assert dd == []  # still suppressed from the reported findings list
    assert len(sci) == 1
    assert sci[0].declared_field == "excluded_statuses"
    assert sci[0].dollar_impact == 200.0  # -100.0 (self-consistency) + 300.0 (folded suppressed cross-source)
    assert sci[0].dollar_impact == CASE_7_PRECEDENCE_CONFLICT.known_gap
