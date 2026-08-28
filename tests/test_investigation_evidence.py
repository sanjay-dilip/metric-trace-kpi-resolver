"""Tests for src.reconciliation_assembly.assemble_investigation_evidence
(Build 1, Day 7, Task 2): the completion gate for the deterministic core.
Runs the full pipeline for a scenario and computes unexplained_residual =
known_gap - sum(line_item.dollar_impact for all line items).

Three things are proven separately here, on purpose, because they are NOT
the same claim:

1. sum(line_items) + unexplained_residual == known_gap, EXACTLY, for every
   fixture in SCENARIOS. This holds by algebraic construction
   (unexplained_residual is DEFINED as known_gap minus the total), the
   same caveat already documented on shapley_pair_attribution's own
   sum-check (src/reconciliation.py): it proves there's no arithmetic bug
   in how the residual is computed, not that the residual is small or
   that every real cause was found.

2. Whether unexplained_residual is actually SMALL is a separate, genuine
   claim, checked case by case below. Originally (Day 7 Task 2) it was NOT
   small for Cases 1-4 -- a real, traced discrepancy, not a bug: back then
   Cases 1-5's reported_value_a/reported_value_b were hand-authored at a
   realistic dashboard scale (hundreds of thousands) while their seed data
   was built at a much smaller, independently-chosen scale for tractable
   hand-verification, and the two had never been calibrated to match
   (Decision 13, docs/decisions.md). Build 1, Day 7, Task 3 resolved that
   gap: Cases 1-6's reported_value_a/reported_value_b/known_gap were
   recalibrated to real execution of each scenario's as-written SQL
   against its own seed data (Case 5 and Case 6 turned out to already be
   correctly calibrated -- verified, not assumed -- so their numbers did
   not change). Case 7 was already calibrated (Day 4 close-out) and was
   left untouched. Cases 1-4 and 7 show a residual of exactly 0.0; Case 5's
   100% residual is the deliberate design intent (no findable cause
   exists) -- confirmed case by case below, not just asserted.

3. Cases 8-11 (Build 2, Day 5, added to SCENARIOS once
   data_quality_issues was wired into assemble_investigation_evidence)
   each show a 100%-of-known_gap residual too -- but UNLIKE Case 5, this
   is NOT "no cause exists." Each of these four has a real, found,
   fully-quantified data-quality cause (visible in
   evidence.data_quality_issues), deliberately NOT folded into
   unexplained_residual (additive-evidence-only, see
   assemble_investigation_evidence's own docstring for the full
   reasoning). Tested explicitly below, per case, so this distinction is
   proven rather than left to be confused with Case 5's shape."""

import math

import pytest

from src.reconciliation_assembly import assemble_investigation_evidence
from src.schema import InvestigationEvidence
from src.scenario import DashboardSource, DeclaredDefinition, Scenario
from src.self_consistency import check_self_consistency
from tests.fixtures.scenarios import (
    CASE_1_JOIN_TYPE,
    CASE_2_MULTI_CAUSE,
    CASE_3_HYBRID_FALLBACK,
    CASE_4_GOVERNANCE_DRIFT,
    CASE_5_UNEXPLAINED_RESIDUAL,
    CASE_6_NEGATIVE_CONTROL,
    CASE_7_PRECEDENCE_CONFLICT,
    CASE_8_STALE_EXTRACT,
    CASE_9_MISSING_PARTITION,
    CASE_10_REFERENTIAL_INTEGRITY,
    CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B,
    CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION,
    SCENARIOS,
)

_EXACT_TOLERANCE = 0.0  # see module docstring: the sum-check holds by exact float equality on every fixture


def test_sum_check_holds_exactly_across_all_fixtures():
    """sum(line_items) + unexplained_residual == known_gap, exact float
    equality, no tolerance needed -- verified explicitly rather than
    assumed, since it's an algebraic identity of how unexplained_residual
    is computed, not proof any given fixture's causes are complete. Holds
    trivially for Cases 8-11 too (reconciliation == [], so residual ==
    known_gap by definition) -- this test proves no arithmetic bug, it
    does NOT prove those four are "fully reconciled"; see
    test_case_8_through_11_data_quality_additive_only below for that
    distinction."""
    for scenario in SCENARIOS:
        evidence = assemble_investigation_evidence(scenario)
        total = sum(item.dollar_impact for item in evidence.reconciliation)
        reconstructed_gap = total + evidence.unexplained_residual
        assert math.isclose(reconstructed_gap, scenario.known_gap, abs_tol=_EXACT_TOLERANCE), (
            f"{scenario.scenario_id}: total ({total}) + residual "
            f"({evidence.unexplained_residual}) = {reconstructed_gap}, expected known_gap "
            f"({scenario.known_gap})"
        )


def test_returns_a_fully_populated_investigation_evidence_instance():
    """The first point in this project where InvestigationEvidence
    (src/schema.py) is populated with real, computed data end to end,
    rather than a hand-constructed throwaway instance (Day 1's
    verification). Confirmed against Case 2: every field present, typed
    correctly, non-trivially populated where a real cause exists."""
    evidence = assemble_investigation_evidence(CASE_2_MULTI_CAUSE)

    assert isinstance(evidence, InvestigationEvidence)
    assert evidence.definition_differences != []
    assert evidence.self_consistency_issues == []
    assert evidence.sql_differences == []  # decision 10 suppressed the redundant distinct finding
    assert evidence.reconciliation != []
    assert isinstance(evidence.unexplained_residual, float)


def test_case_1_fully_reconciles_after_recalibration():
    """Case 1's real cause (join_type) is +300.0, and known_gap is now
    +300.0 too (recalibrated to real seed execution -- Decision 13's
    resolution, Build 1 Day 7 Task 3; it was a mismatched-scale +3400.0
    hand-typed figure before). The single found cause now fully accounts
    for the gap: residual is exactly 0.0, not merely "near" zero."""
    evidence = assemble_investigation_evidence(CASE_1_JOIN_TYPE)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert len(evidence.reconciliation) == 1
    assert total == CASE_1_JOIN_TYPE.known_gap == 300.0
    assert evidence.unexplained_residual == 0.0


def test_case_2_fully_reconciles_after_recalibration():
    """Same recalibration result as Case 1, for the Shapley-pair case: the
    two interacting causes sum to +20.0, matching known_gap exactly.
    known_gap was originally a mismatched-scale +1250.0 (pre-Decision 13),
    then +1.0 (Decision 13's Day 7 Task 3 resolution, correct but
    single-digit), now +20.0 after Build 3, Day 2, Part 2b's seed-data
    magnitude recalibration (excluded_statuses=+120.0, aggregation=-100.0
    -- both causes still nonzero and still interacting via the Shapley-pair
    engine at the new scale, confirmed directly, not assumed to transfer)."""
    evidence = assemble_investigation_evidence(CASE_2_MULTI_CAUSE)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert len(evidence.reconciliation) == 2
    assert total == CASE_2_MULTI_CAUSE.known_gap == 20.0
    assert evidence.unexplained_residual == 0.0


def test_case_3_fully_reconciles_after_recalibration():
    """Same recalibration result as Case 1/2, for the decision-12-fixed
    Shapley-pair case: sum is +100.0, matching the recalibrated known_gap
    (+100.0, was a mismatched-scale +5500.0) exactly."""
    evidence = assemble_investigation_evidence(CASE_3_HYBRID_FALLBACK)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert len(evidence.reconciliation) == 2
    assert total == CASE_3_HYBRID_FALLBACK.known_gap == 100.0
    assert evidence.unexplained_residual == 0.0


def test_case_4_fully_reconciles_after_recalibration():
    """Same recalibration result as Case 1/2/3, for the self-consistency-
    only case: +200.0 matches the recalibrated known_gap (+200.0, was a
    mismatched-scale +11500.0) exactly."""
    evidence = assemble_investigation_evidence(CASE_4_GOVERNANCE_DRIFT)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert len(evidence.reconciliation) == 1
    assert total == CASE_4_GOVERNANCE_DRIFT.known_gap == 200.0
    assert evidence.unexplained_residual == 0.0


def test_case_5_residual_equals_known_gap_by_design():
    """Case 5 (CASE_5_UNEXPLAINED_RESIDUAL) is deliberately built so that
    declared definitions and SQL structure match on both sides -- no
    definitional, structural, or self-consistency cause exists within this
    tool's scope, per its own fixture docstring. Confirmed here: zero line
    items, and unexplained_residual equals known_gap exactly (3800.0) --
    the entire gap is, correctly, unexplained. Unlike Cases 1-4, Case 5's
    reported_value_a/b were already correctly calibrated when checked
    during Day 7 Task 3's recalibration (executed seed values matched the
    hand-typed figures exactly), so this fixture's numbers are unchanged
    -- this is the design intent, not a bug, and it holds both before and
    after the recalibration."""
    evidence = assemble_investigation_evidence(CASE_5_UNEXPLAINED_RESIDUAL)

    assert evidence.reconciliation == []
    assert evidence.definition_differences == []
    assert evidence.sql_differences == []
    assert evidence.self_consistency_issues == []
    assert evidence.unexplained_residual == CASE_5_UNEXPLAINED_RESIDUAL.known_gap == 3800.0


def test_case_6_negative_control_zero_impact_zero_residual():
    """Case 6: no declared definitions, identical SQL on both sides,
    known_gap == 0.0. No fabricated causes, no phantom gap -- total impact
    and residual are both exactly 0.0. Like Case 5, Case 6's numbers were
    already correctly calibrated before Day 7 Task 3 (trivially, since
    both sides are 0 either way) and are unchanged by it."""
    evidence = assemble_investigation_evidence(CASE_6_NEGATIVE_CONTROL)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert evidence.reconciliation == []
    assert total == 0.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_6_NEGATIVE_CONTROL.known_gap == 0.0


def test_case_7_residual_is_zero_fully_explained():
    """Case 7 was already calibrated before Day 7 Task 3 (Day 4
    close-out): its reported_value_a/b were deliberately set equal to real
    seed-execution figures (300.0/100.0, see CASE_7_PRECEDENCE_CONFLICT's
    own comment in tests/fixtures/scenarios.py) when the fixture was
    built, so it was untouched by this recalibration. Its self-consistency
    (+ folded suppressed cross-source) cause fully explains known_gap with
    an exact zero residual, the same result Cases 1-4 now also show post-
    recalibration -- confirming the reconciliation math was correct all
    along; only the other 4 fixtures' calibration was off."""
    evidence = assemble_investigation_evidence(CASE_7_PRECEDENCE_CONFLICT)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert len(evidence.reconciliation) == 1
    assert total == CASE_7_PRECEDENCE_CONFLICT.known_gap == 200.0
    assert evidence.unexplained_residual == 0.0


@pytest.mark.parametrize(
    "scenario, expected_category, expected_source, expected_dollar_impact",
    [
        (CASE_8_STALE_EXTRACT, "stale_extract", "a", -150.0),
        (CASE_9_MISSING_PARTITION, "missing_partition", "a", -250.0),
        (CASE_10_REFERENTIAL_INTEGRITY, "referential_integrity", "a", 300.0),
        (CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B, "referential_integrity", "b", -300.0),
    ],
)
def test_case_8_through_11_data_quality_additive_only(
    scenario, expected_category, expected_source, expected_dollar_impact
):
    """Build 2, Day 5: proves the dispatch point (_resolve_data_quality_issues,
    src/reconciliation_assembly.py) chooses correctly per fixture AND that
    wiring data_quality_issues in is genuinely additive-only -- it does
    NOT change reconciliation or unexplained_residual at all, for any of
    the four cases. Each case's dollar_impact/category/source here matches
    the exact figures already proven independently in
    tests/test_data_quality.py's own dedicated firing tests -- re-asserted
    here specifically through the full assemble_investigation_evidence
    pipeline, not just the bare check_* function, to prove the dispatch
    wiring itself (not just the underlying check) is correct."""
    evidence = assemble_investigation_evidence(scenario)

    assert len(evidence.data_quality_issues) == 1
    issue = evidence.data_quality_issues[0]
    assert issue.category == expected_category
    assert issue.source == expected_source
    assert issue.dollar_impact == expected_dollar_impact

    # Additive-only: reconciliation/unexplained_residual are exactly what
    # they would be with data_quality_issues absent entirely -- the same
    # shape as Case 5's true "no cause found" (reconciliation == [],
    # residual == known_gap), even though a real cause WAS found here.
    assert evidence.reconciliation == []
    assert evidence.unexplained_residual == scenario.known_gap


def test_case_1_has_no_data_quality_cause_dispatch_correctly_returns_empty():
    """Negative-control side of the dispatch point: Case 1 has no entry in
    _DATA_QUALITY_DISPATCH (src/reconciliation_assembly.py) -- confirms
    the dispatch silently returns [] for a scenario_id it doesn't
    recognize, rather than guessing or misfiring, even though Case 1's own
    seed data has a real orphan FK row (order_id=3, customer_id=99,
    documented in CONTEXT.md as a known, unresolved latent-collision risk
    if check_referential_integrity were ever run against it directly --
    this test only confirms today's dispatch table doesn't do that, not
    that the underlying risk is resolved)."""
    evidence = assemble_investigation_evidence(CASE_1_JOIN_TYPE)

    assert evidence.data_quality_issues == []


def test_raises_on_more_than_two_remaining_causes():
    """Interaction beyond a single pair is untested (decision 11) --
    constructed here (not one of the 7 committed fixtures, none of which
    reach this branch, verified separately by test_sum_check_holds_exactly_across_all_7_fixtures
    running all 7 without error) to prove the guard actually fires rather
    than trusting it by inspection: three simultaneously surviving causes
    (date_field, excluded_statuses -- both definitional -- plus join_type,
    structural, no definitional counterpart to collide with), both sides
    self-consistent (confirmed, so Day 4's precedence does not reduce the
    count), no decision 10/12 collision in play (aggregation matches on
    both sides; join_type has no definitional analog at all)."""
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

    scenario = Scenario(
        scenario_id="synthetic_three_cause",
        description="Synthetic: 3 simultaneously surviving causes, proving the n>2 raise guard actually fires.",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=1000.0,
        reported_value_b=500.0,
        known_gap=500.0,
        seed_table="nonexistent_seed_table",  # never reached: the raise fires before any SQL execution
    )

    with pytest.raises(ValueError, match="3 remaining cross-source causes"):
        assemble_investigation_evidence(scenario)


def test_case_13_full_pipeline_now_raises_on_filter_alone_after_suppression():
    """Build 3, Day 1, Part 6: proves the filter/excluded_statuses
    suppression rule (assemble_structural_and_definitional_evidence,
    src/self_consistency.py) actually reaches assemble_investigation_evidence,
    not just the isolated unit-level proof in
    tests/test_structural_definitional_precedence.py. Before Part 6,
    Case 13 reached this function's Shapley-pair branch (2 remaining
    causes: excluded_statuses + filter) and raised inside the
    excluded_statuses correction itself (Part 4/5's finding). After Part
    6's suppression, only `filter` survives -- exactly 1 remaining cause,
    routed through the single-cause branch instead -- and THAT raises on
    filter's own uncovered-category gap in construct_corrected_query,
    the gap Part 1's inventory originally predicted. This is the correct,
    expected stopping point (apply_filter_correction is a separate,
    undecided design question), not a failure -- CASE_13 is still
    deliberately NOT added to SCENARIOS."""
    with pytest.raises(ValueError, match="No corrected-query mutation rule exists for SQLStructuralDifference category 'filter'"):
        assemble_investigation_evidence(CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION)
