"""Tests for tests.fixtures.benchmark_pipeline.assemble_investigation_evidence_for_benchmark
(Build 3, Day 2, Part 4, A3-ii; return type corrected in Part 5)."""

import pytest

from src.definition_diff import diff_definitions
from src.reconciliation_assembly import assemble_investigation_evidence
from src.schema import InvestigationEvidence
from src.scenario import DashboardSource, DeclaredDefinition, Scenario
from src.self_consistency import check_self_consistency
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.ambiguous_scenarios import (
    AMBIGUOUS_ACTIVE_USER_CONVENTION,
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_REFUND_TIMING,
    AMBIGUOUS_REVENUE_RECOGNITION,
)
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES, BenchmarkEntry
from tests.fixtures.benchmark_pipeline import (
    PartialInvestigationEvidence,
    assemble_investigation_evidence_for_benchmark,
)
from tests.fixtures.scenarios import SCENARIOS


def test_ambiguous_revenue_recognition_now_completes_directly_no_wrapper_needed():
    """Build 3, Day 2, Part 15 (decision 22) superseded this test's own
    original premise: AMBIGUOUS_REVENUE_RECOGNITION used to need the
    benchmark wrapper because its filter/excluded_statuses collision
    (confidence='high') was NOT suppressed, reaching 3 remaining causes.
    Decision 22 extends suppression to fire at confidence='high' too
    (removing filter, keeping excluded_statuses, the reverse direction
    from the low-confidence case) -- this scenario now reduces to 2
    remaining causes (date_field, excluded_statuses) and completes
    through assemble_investigation_evidence DIRECTLY, returning a real
    InvestigationEvidence, not a PartialInvestigationEvidence. Real,
    execution-verified figures, live-checked before this test was
    written: date_field=-1200.0, excluded_statuses=+1150.0,
    unexplained_residual=0.0."""
    evidence = assemble_investigation_evidence(AMBIGUOUS_REVENUE_RECOGNITION)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.sql_differences == []

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("booking_date", "delivery_date"),
        "excluded_statuses": ("(none)", "pending_delivery"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)

    impacts = {item.cause.split(":")[0]: item.dollar_impact for item in evidence.reconciliation}
    assert impacts == {"date_field": -1200.0, "excluded_statuses": 1150.0}
    assert evidence.unexplained_residual == 0.0

    # The wrapper's non-escalated path returns the same real InvestigationEvidence.
    entry = next(e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_revenue_recognition")
    via_wrapper = assemble_investigation_evidence_for_benchmark(entry)
    assert isinstance(via_wrapper, InvestigationEvidence)
    assert not isinstance(via_wrapper, PartialInvestigationEvidence)


def test_technical_scenario_3plus_causes_still_raises_through_wrapper():
    """The single most important behavior: a technical (is_ambiguous=False)
    scenario whose genuine 3+-cause failure fires must re-raise unchanged
    through the wrapper, not get silently swallowed. Build 3, Day 2, Part
    15 update: this can no longer reuse AMBIGUOUS_REVENUE_RECOGNITION's
    scenario, since decision 22's suppression extension reduces it to 2
    causes now (see the test above) -- a fresh synthetic 3-cause scenario
    is constructed directly instead (join_type + date_field +
    excluded_statuses, no filter/excluded_statuses collision involved at
    all, so decision 22 does not affect it), mirroring
    tests/test_investigation_evidence.py's own
    test_raises_on_more_than_two_remaining_causes construction."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(o.amount) AS revenue FROM orders o "
            "LEFT JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.order_date >= '2024-01-01' AND o.status != 'cancelled'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date", excluded_statuses=["cancelled"], aggregation="sum"
        ),
    )
    source_b = DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(o.amount) AS revenue FROM orders o "
            "INNER JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.created_at >= '2024-01-01' AND o.status NOT IN ('cancelled', 'refunded')"
        ),
        declared_definition=DeclaredDefinition(
            date_field="created_at", excluded_statuses=["cancelled", "refunded"], aggregation="sum"
        ),
    )
    assert check_self_consistency(source_a, "a") == []
    assert check_self_consistency(source_b, "b") == []

    synthetic_scenario = Scenario(
        scenario_id="synthetic_three_cause_for_wrapper_test",
        description="Synthetic: 3 simultaneously surviving causes, unaffected by decision 22 (no filter/excluded_statuses collision).",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=1000.0,
        reported_value_b=500.0,
        known_gap=500.0,
        seed_table="nonexistent_seed_table",  # never reached: the raise fires before any SQL execution
    )
    synthetic_technical_entry = BenchmarkEntry(
        scenario=synthetic_scenario,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
    )

    with pytest.raises(ValueError, match="remaining cross-source causes for scenario"):
        assemble_investigation_evidence_for_benchmark(synthetic_technical_entry)

    # Confirm it's the exact same failure assemble_investigation_evidence itself raises.
    with pytest.raises(ValueError, match="remaining cross-source causes for scenario"):
        assemble_investigation_evidence(synthetic_scenario)


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


def test_ambiguous_customer_counting_now_completes_directly_no_wrapper_needed():
    """Build 3, Day 2, Part 15 (decision 22) superseded this test's own
    original premise, same as AMBIGUOUS_REVENUE_RECOGNITION above: the
    filter/excluded_statuses collision here is confidence='high', now
    suppressed (filter removed, excluded_statuses survives), reducing 3
    remaining causes to 2 -- this scenario completes through
    assemble_investigation_evidence DIRECTLY, returning a real
    InvestigationEvidence.

    Build 3, Day 3, Part 2 update: the figures below changed again, this
    time for a genuine reason, not a re-suppression ripple. Root-caused in
    Part 1 (docs/decisions.md is not yet updated with this fix as of this
    entry -- pending a future session per this task's own locked scope):
    apply_date_field_correction was value-blind, silently keeping source_a's
    OWN date threshold ('2000-01-01') instead of adopting source_b's real
    one ('2024-01-01') when correcting the date_field column, which
    understated date_field's true attribution. Fixed in Part 2
    (construct_corrected_query's new other_side_sql parameter,
    src/query_mutation.py's new _extract_date_predicate_snippet). Real,
    execution-verified figures, post-fix: date_field=100.0,
    excluded_statuses=50.0, unexplained_residual=0.0 -- this scenario NOW
    fully reconciles, exactly like AMBIGUOUS_REVENUE_RECOGNITION/ATTRIBUTION,
    closing the gap this fixture's own prior docstring reported as a
    genuine, unresolved artifact."""
    evidence = assemble_investigation_evidence(AMBIGUOUS_CUSTOMER_COUNTING)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.sql_differences == []

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("signup_date", "last_active_date"),
        "excluded_statuses": ("(none)", "churned"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)

    impacts = {item.cause.split(":")[0]: item.dollar_impact for item in evidence.reconciliation}
    assert impacts == {"date_field": 100.0, "excluded_statuses": 50.0}
    assert evidence.unexplained_residual == 0.0

    entry = next(e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_customer_counting")
    via_wrapper = assemble_investigation_evidence_for_benchmark(entry)
    assert isinstance(via_wrapper, InvestigationEvidence)
    assert not isinstance(via_wrapper, PartialInvestigationEvidence)


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


def test_ambiguous_attribution_now_completes_directly_no_wrapper_needed():
    """Build 3, Day 2, Part 15 (decision 22) superseded this test's own
    original premise, same as AMBIGUOUS_REVENUE_RECOGNITION/CUSTOMER_COUNTING
    above: the filter/excluded_statuses collision here is confidence='high',
    now suppressed, reducing 3 remaining causes to 2 -- this scenario
    completes through assemble_investigation_evidence DIRECTLY, returning
    a real InvestigationEvidence. Real, execution-verified figures,
    live-checked before this test was written: date_field=-11500.0,
    excluded_statuses=+11000.0, unexplained_residual=0.0."""
    evidence = assemble_investigation_evidence(AMBIGUOUS_ATTRIBUTION)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.sql_differences == []

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("first_touch_date", "close_date"),
        "excluded_statuses": ("(none)", "lost, open"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)

    impacts = {item.cause.split(":")[0]: item.dollar_impact for item in evidence.reconciliation}
    assert impacts == {"date_field": -11500.0, "excluded_statuses": 11000.0}
    assert evidence.unexplained_residual == 0.0

    entry = next(e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_attribution")
    via_wrapper = assemble_investigation_evidence_for_benchmark(entry)
    assert isinstance(via_wrapper, InvestigationEvidence)
    assert not isinstance(via_wrapper, PartialInvestigationEvidence)


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


# --- Build 3, Day 2, Part 10: committed coverage for AMBIGUOUS_REFUND_TIMING
# (Build 3, Day 2, Part 3) -- the original Known-Safe Pattern 1 reference
# case, relied upon across three subsequent sessions (Parts 4-9) purely on
# the strength of its original chat report, never independently
# re-confirmed in committed code until now. Live-verified before writing
# these tests, same discipline as Parts 8 and 9 -- confirmed to match
# Part 3's original figures exactly (880.0/530.0/350.0, one declared/high
# date_field DefinitionDifference, clean completion with no wrapper, one
# reconciliation line item, unexplained_residual=0.0). Mirrors the
# AMBIGUOUS_CURRENCY_TIMING test structure above, not the Pattern-2
# wrapper-test structure.


def test_ambiguous_refund_timing_reported_values_match_seed_execution():
    """Confirm the fixture's committed reported_value_a/b/known_gap
    (880.0/530.0/350.0, as originally reported in chat during Build 3 Day 2
    Part 3) are exactly what's on the Scenario object."""
    assert AMBIGUOUS_REFUND_TIMING.reported_value_a == 880.0
    assert AMBIGUOUS_REFUND_TIMING.reported_value_b == 530.0
    assert AMBIGUOUS_REFUND_TIMING.known_gap == 350.0


def test_ambiguous_refund_timing_findings_match_reported():
    """Confirm diff_sql and diff_definitions, run directly against the
    fixture's real sources, produce exactly the findings Part 3 originally
    reported: one SQLStructuralDifference (date_field, since the two
    sides' SQL literally filters on different date columns) and exactly
    one DefinitionDifference (date_field, declared/high-confidence) -- no
    excluded_statuses or aggregation difference on either side."""
    source_a = AMBIGUOUS_REFUND_TIMING.source_a
    source_b = AMBIGUOUS_REFUND_TIMING.source_b

    sql_differences = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    assert [d.category for d in sql_differences] == ["date_field"]

    definition_differences = diff_definitions(source_a, source_b)
    fields = {
        d.field: (d.source_a_value, d.source_b_value, d.source, d.confidence)
        for d in definition_differences
    }
    assert fields == {
        "date_field": ("refund_date", "purchase_date", "declared", "high"),
    }


def test_ambiguous_refund_timing_completes_through_normal_pipeline_single_line_item():
    """Confirm this scenario completes through assemble_investigation_evidence
    DIRECTLY, as Part 3 originally reported -- no ValueError, no wrapper
    needed -- with sql_diff's colliding date_field finding suppressed
    (decision 12, same fact as the definitional date_field difference) and
    exactly one ReconciliationLineItem whose dollar_impact matches
    known_gap exactly, leaving unexplained_residual at 0.0."""
    evidence = assemble_investigation_evidence(AMBIGUOUS_REFUND_TIMING)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.sql_differences == []
    assert len(evidence.definition_differences) == 1
    assert evidence.definition_differences[0].field == "date_field"
    assert evidence.self_consistency_issues == []

    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].dollar_impact == AMBIGUOUS_REFUND_TIMING.known_gap == 350.0
    assert evidence.unexplained_residual == 0.0


def test_ambiguous_refund_timing_benchmark_entry_is_escalate_not_answer():
    """Even with a single, clean, fully-reconciled cause, the correct
    system behavior is still to escalate, not silently pick a side. The
    BenchmarkEntry must still reflect that, matching the live evidence
    shape confirmed above."""
    entry = next(
        e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_refund_timing"
    )
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"
    assert entry.ground_truth_check_field == "reconciliation"


# --- Build 3, Day 2, Part 15: committed coverage for
# AMBIGUOUS_ACTIVE_USER_CONVENTION (decision 22) -- drafted Part 12,
# blocked there and in Part 14, authored here once decision 22's
# confidence-gate extension unblocked it. Live-verified before writing
# these tests, same discipline as every prior scenario.


def test_ambiguous_active_user_convention_reported_values_match_seed_execution():
    """Confirm the fixture's committed reported_value_a/b/known_gap
    (550.0/400.0/150.0, computed via real DuckDB execution during Build 3
    Day 2 Part 15's authoring) are exactly what's on the Scenario object."""
    assert AMBIGUOUS_ACTIVE_USER_CONVENTION.reported_value_a == 550.0
    assert AMBIGUOUS_ACTIVE_USER_CONVENTION.reported_value_b == 400.0
    assert AMBIGUOUS_ACTIVE_USER_CONVENTION.known_gap == 150.0


def test_ambiguous_active_user_convention_findings_match_reported():
    """Confirm diff_sql and diff_definitions, run directly against the
    fixture's real sources, produce exactly the findings this scenario
    was designed around: one SQLStructuralDifference (filter, since
    source_b filters on 'status' and source_a does not) and exactly one
    DefinitionDifference (excluded_statuses, declared/high-confidence) --
    no date_field or aggregation difference on either side, by deliberate
    standalone-excluded_statuses design."""
    source_a = AMBIGUOUS_ACTIVE_USER_CONVENTION.source_a
    source_b = AMBIGUOUS_ACTIVE_USER_CONVENTION.source_b

    sql_differences = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    assert [d.category for d in sql_differences] == ["filter"]

    definition_differences = diff_definitions(source_a, source_b)
    fields = {
        d.field: (d.source_a_value, d.source_b_value, d.source, d.confidence)
        for d in definition_differences
    }
    assert fields == {
        "excluded_statuses": ("(none)", "suspended", "declared", "high"),
    }


def test_ambiguous_active_user_convention_completes_through_normal_pipeline_single_line_item():
    """Decision 22's real effect: the raw filter/excluded_statuses
    collision (confidence='high') is suppressed down to a single
    surviving cause (excluded_statuses), routed through the SINGLE-CAUSE
    branch, not Shapley pairing -- confirms assemble_investigation_evidence
    completes normally (no wrapper needed) with exactly one
    ReconciliationLineItem whose dollar_impact matches known_gap exactly,
    leaving unexplained_residual at 0.0."""
    evidence = assemble_investigation_evidence(AMBIGUOUS_ACTIVE_USER_CONVENTION)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.sql_differences == []
    assert len(evidence.definition_differences) == 1
    assert evidence.definition_differences[0].field == "excluded_statuses"
    assert evidence.self_consistency_issues == []

    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].dollar_impact == AMBIGUOUS_ACTIVE_USER_CONVENTION.known_gap == 150.0
    assert evidence.reconciliation[0].computed_by == "single_cause_attribution"
    assert evidence.unexplained_residual == 0.0


def test_ambiguous_active_user_convention_benchmark_entry_is_escalate_not_answer():
    """Even with a single, clean, fully-reconciled cause, the correct
    system behavior is still to escalate, not silently pick a side. The
    BenchmarkEntry must still reflect that, matching the live evidence
    shape confirmed above."""
    entry = next(
        e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_active_user_convention"
    )
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"
    assert entry.ground_truth_check_field == "reconciliation"
