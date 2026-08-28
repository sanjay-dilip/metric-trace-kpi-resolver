"""Tests for tests.fixtures.benchmark_pipeline.assemble_investigation_evidence_for_benchmark
(Build 3, Day 2, Part 4, A3-ii; return type corrected in Part 5)."""

import pytest

from src.definition_diff import diff_definitions
from src.reconciliation_assembly import assemble_investigation_evidence
from src.schema import InvestigationEvidence
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.ambiguous_scenarios import (
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_REVENUE_RECOGNITION,
)
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES, BenchmarkEntry
from tests.fixtures.benchmark_pipeline import (
    PartialInvestigationEvidence,
    assemble_investigation_evidence_for_benchmark,
)
from tests.fixtures.scenarios import SCENARIOS


def test_ambiguous_revenue_recognition_returns_partial_evidence_via_wrapper():
    """The whole point of A3-ii: a genuinely ambiguous scenario whose
    interacting causes exceed the 2-cause Shapley pairing this project
    supports must still surface its individually-found causes through the
    benchmark wrapper, rather than the caller getting nothing but a raised
    exception. Part 5: the returned object must be a
    PartialInvestigationEvidence, never an InvestigationEvidence -- the
    two are structurally independent types, not sub/superclass."""
    entry = next(e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_revenue_recognition")
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"

    evidence = assemble_investigation_evidence_for_benchmark(entry)

    assert isinstance(evidence, PartialInvestigationEvidence)
    assert not isinstance(evidence, InvestigationEvidence)

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("booking_date", "delivery_date"),
        "excluded_statuses": ("(none)", "pending_delivery"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)
    assert evidence.reconciliation == []
    assert evidence.unexplained_residual is None


def test_technical_scenario_3plus_causes_still_raises_through_wrapper():
    """The single most important behavior: a technical (is_ambiguous=False)
    scenario whose genuine 3+-cause failure fires must re-raise unchanged
    through the wrapper, not get silently swallowed. None of the 11
    existing Case 1-11 fixtures naturally reaches 3 remaining causes
    (checked directly, not assumed), so this reuses
    AMBIGUOUS_REVENUE_RECOGNITION's own real 3-cause scenario under a
    synthetic is_ambiguous=False entry -- the same underlying failure,
    the opposite ambiguity flag."""
    synthetic_technical_entry = BenchmarkEntry(
        scenario=AMBIGUOUS_REVENUE_RECOGNITION,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
    )

    with pytest.raises(ValueError, match="remaining cross-source causes for scenario"):
        assemble_investigation_evidence_for_benchmark(synthetic_technical_entry)

    # Confirm it's the exact same failure assemble_investigation_evidence itself raises.
    with pytest.raises(ValueError, match="remaining cross-source causes for scenario"):
        assemble_investigation_evidence(AMBIGUOUS_REVENUE_RECOGNITION)


def test_normal_path_returns_real_investigation_evidence():
    """The non-escalated path must return a real InvestigationEvidence
    (not a PartialInvestigationEvidence) -- confirming the union return
    type is honest in both directions, not just on the escalated path."""
    entry = next(e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "case_01_join_type")
    evidence = assemble_investigation_evidence_for_benchmark(entry)

    assert isinstance(evidence, InvestigationEvidence)
    assert not isinstance(evidence, PartialInvestigationEvidence)
    assert evidence.unexplained_residual == 0.0


def test_assemble_investigation_evidence_unaffected_for_all_11_cases():
    """assemble_investigation_evidence itself is unmodified by this task --
    confirm by rerunning every Case 1-11 fixture directly (not through the
    wrapper), not by inspecting the diff alone. Every case must still
    either resolve to a real float residual or (Case 5) a fully-unexplained
    known_gap, with the sum-check invariant holding exactly, same as
    before this task."""
    for scenario in SCENARIOS:
        evidence = assemble_investigation_evidence(scenario)
        assert isinstance(evidence.unexplained_residual, float)
        total = sum(item.dollar_impact for item in evidence.reconciliation)
        assert total + evidence.unexplained_residual == scenario.known_gap


# --- Build 3, Day 2, Part 8: committed coverage for AMBIGUOUS_CUSTOMER_COUNTING
# and AMBIGUOUS_ATTRIBUTION (verification of PR #87's chat-reported figures
# against real execution, not new scenario design). Both fixtures and their
# BenchmarkEntrys already existed; this only adds a regression guard, mirroring
# AMBIGUOUS_REVENUE_RECOGNITION's own coverage above.


def test_ambiguous_customer_counting_reported_values_match_seed_execution():
    """Confirm the fixture's committed reported_value_a/b/known_gap
    (450.0/300.0/150.0, as reported in chat during Build 3 Day 2 Part 7)
    are exactly what's on the Scenario object -- these are hand-set fields
    calibrated to real seed execution per decision 13, so this test reads
    them directly rather than re-deriving them from a live query."""
    assert AMBIGUOUS_CUSTOMER_COUNTING.reported_value_a == 450.0
    assert AMBIGUOUS_CUSTOMER_COUNTING.reported_value_b == 300.0
    assert AMBIGUOUS_CUSTOMER_COUNTING.known_gap == 150.0


def test_ambiguous_customer_counting_findings_match_reported():
    """Confirm diff_sql and diff_definitions, run directly against the
    fixture's real sources, produce exactly the findings reported in chat:
    two SQLStructuralDifference categories (filter, date_field) and two
    DefinitionDifferences (date_field, excluded_statuses), both declared/
    high-confidence -- real output, not restated from the prior report."""
    source_a = AMBIGUOUS_CUSTOMER_COUNTING.source_a
    source_b = AMBIGUOUS_CUSTOMER_COUNTING.source_b

    sql_differences = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    assert {d.category for d in sql_differences} == {"filter", "date_field"}

    definition_differences = diff_definitions(source_a, source_b)
    fields = {
        d.field: (d.source_a_value, d.source_b_value, d.source, d.confidence)
        for d in definition_differences
    }
    assert fields == {
        "date_field": ("signup_date", "last_active_date", "declared", "high"),
        "excluded_statuses": ("(none)", "churned", "declared", "high"),
    }


def test_ambiguous_customer_counting_returns_partial_evidence_via_wrapper():
    """Mirrors test_ambiguous_revenue_recognition_returns_partial_evidence_via_wrapper
    above: confirm assemble_investigation_evidence_for_benchmark raises the
    same 3+-cause condition internally (decision 18's confidence="medium"/
    "high" filter/excluded_statuses gap, not suppressed at high confidence)
    and returns a PartialInvestigationEvidence, not an InvestigationEvidence,
    with reconciliation=[], unexplained_residual=None, and the same
    post-suppression finding shape reported in chat: definition_differences
    keeps both fields, sql_differences keeps only 'filter' (its 'date_field'
    finding is suppressed in favor of the definitional one, decision 12)."""
    entry = next(
        e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_customer_counting"
    )
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"

    evidence = assemble_investigation_evidence_for_benchmark(entry)

    assert isinstance(evidence, PartialInvestigationEvidence)
    assert not isinstance(evidence, InvestigationEvidence)

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("signup_date", "last_active_date"),
        "excluded_statuses": ("(none)", "churned"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)
    assert [d.category for d in evidence.sql_differences] == ["filter"]
    assert evidence.reconciliation == []
    assert evidence.unexplained_residual is None


def test_ambiguous_attribution_reported_values_match_seed_execution():
    """Confirm the fixture's committed reported_value_a/b/known_gap
    (24000.0/24500.0/-500.0, as reported in chat during Build 3 Day 2
    Part 7) are exactly what's on the Scenario object."""
    assert AMBIGUOUS_ATTRIBUTION.reported_value_a == 24000.0
    assert AMBIGUOUS_ATTRIBUTION.reported_value_b == 24500.0
    assert AMBIGUOUS_ATTRIBUTION.known_gap == -500.0


def test_ambiguous_attribution_findings_match_reported():
    """Confirm diff_sql and diff_definitions, run directly against the
    fixture's real sources, produce exactly the findings reported in chat:
    two SQLStructuralDifference categories (filter, date_field) and two
    DefinitionDifferences (date_field, excluded_statuses), both declared/
    high-confidence -- real output, not restated from the prior report."""
    source_a = AMBIGUOUS_ATTRIBUTION.source_a
    source_b = AMBIGUOUS_ATTRIBUTION.source_b

    sql_differences = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    assert {d.category for d in sql_differences} == {"filter", "date_field"}

    definition_differences = diff_definitions(source_a, source_b)
    fields = {
        d.field: (d.source_a_value, d.source_b_value, d.source, d.confidence)
        for d in definition_differences
    }
    assert fields == {
        "date_field": ("first_touch_date", "close_date", "declared", "high"),
        "excluded_statuses": ("(none)", "lost, open", "declared", "high"),
    }


def test_ambiguous_attribution_returns_partial_evidence_via_wrapper():
    """Mirrors test_ambiguous_revenue_recognition_returns_partial_evidence_via_wrapper
    above: confirm assemble_investigation_evidence_for_benchmark raises the
    same 3+-cause condition internally and returns a
    PartialInvestigationEvidence, not an InvestigationEvidence, with
    reconciliation=[], unexplained_residual=None, and the same
    post-suppression finding shape reported in chat."""
    entry = next(
        e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_attribution"
    )
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"

    evidence = assemble_investigation_evidence_for_benchmark(entry)

    assert isinstance(evidence, PartialInvestigationEvidence)
    assert not isinstance(evidence, InvestigationEvidence)

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("first_touch_date", "close_date"),
        "excluded_statuses": ("(none)", "lost, open"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)
    assert [d.category for d in evidence.sql_differences] == ["filter"]
    assert evidence.reconciliation == []
    assert evidence.unexplained_residual is None


# --- Build 3, Day 2, Part 9: committed coverage for AMBIGUOUS_CURRENCY_TIMING,
# a deliberately single-cause ambiguous scenario -- unlike the Pattern-2
# scenarios above, this one is Known-Safe Pattern 1 (same shape as
# AMBIGUOUS_REFUND_TIMING): it must complete through
# assemble_investigation_evidence DIRECTLY, with no wrapper involved,
# returning a real InvestigationEvidence with exactly one reconciliation
# line item. Live-verified before writing these tests, same discipline as
# Part 8.


def test_ambiguous_currency_timing_reported_values_match_seed_execution():
    """Confirm the fixture's committed reported_value_a/b/known_gap
    (4100.0/6300.0/-2200.0, computed via real DuckDB execution during
    Build 3 Day 2 Part 9's authoring) are exactly what's on the Scenario
    object."""
    assert AMBIGUOUS_CURRENCY_TIMING.reported_value_a == 4100.0
    assert AMBIGUOUS_CURRENCY_TIMING.reported_value_b == 6300.0
    assert AMBIGUOUS_CURRENCY_TIMING.known_gap == -2200.0


def test_ambiguous_currency_timing_findings_match_reported():
    """Confirm diff_sql and diff_definitions, run directly against the
    fixture's real sources, produce exactly the findings this scenario was
    designed around: one SQLStructuralDifference (date_field, since the
    two sides' SQL literally filters on different date columns) and
    exactly one DefinitionDifference (date_field, declared/high-confidence)
    -- no excluded_statuses or aggregation difference on either side, by
    deliberate single-cause design."""
    source_a = AMBIGUOUS_CURRENCY_TIMING.source_a
    source_b = AMBIGUOUS_CURRENCY_TIMING.source_b

    sql_differences = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    assert [d.category for d in sql_differences] == ["date_field"]

    definition_differences = diff_definitions(source_a, source_b)
    fields = {
        d.field: (d.source_a_value, d.source_b_value, d.source, d.confidence)
        for d in definition_differences
    }
    assert fields == {
        "date_field": ("transaction_date", "period_close_date", "declared", "high"),
    }


def test_ambiguous_currency_timing_completes_through_normal_pipeline_single_line_item():
    """The whole point of authoring this as single-cause: unlike
    AMBIGUOUS_REVENUE_RECOGNITION/CUSTOMER_COUNTING/ATTRIBUTION, this
    scenario must complete through assemble_investigation_evidence
    DIRECTLY -- no ValueError, no wrapper needed -- with sql_diff's
    colliding date_field finding suppressed (decision 12, same fact as the
    definitional date_field difference) and exactly one
    ReconciliationLineItem whose dollar_impact matches known_gap exactly,
    leaving unexplained_residual at 0.0."""
    evidence = assemble_investigation_evidence(AMBIGUOUS_CURRENCY_TIMING)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.sql_differences == []
    assert len(evidence.definition_differences) == 1
    assert evidence.definition_differences[0].field == "date_field"
    assert evidence.self_consistency_issues == []

    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].dollar_impact == AMBIGUOUS_CURRENCY_TIMING.known_gap == -2200.0
    assert evidence.unexplained_residual == 0.0


def test_ambiguous_currency_timing_benchmark_entry_is_escalate_not_answer():
    """Even with a single, clean, fully-reconciled cause, the correct
    system behavior is still to escalate, not silently pick a side --
    finding one clean cause does not mean the ambiguity is resolved. The
    BenchmarkEntry must reflect that, distinctly from every non-ambiguous
    Case 1-11 entry that also reconciles cleanly."""
    entry = next(
        e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_currency_timing"
    )
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"
    assert entry.ground_truth_check_field == "reconciliation"
