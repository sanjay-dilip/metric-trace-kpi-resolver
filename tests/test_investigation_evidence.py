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
    CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES,
    CASE_15_DATE_FIELD_INFERRED_ONLY,
    CASE_16_EXCLUDED_STATUSES_DECLARED,
    CASE_17_JOIN_TYPE_EXCLUDED_STATUSES_INTERACTING,
    CASE_18_JOIN_TYPE_ONLY,
    CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED,
    CASE_20_STALE_EXTRACT_JOIN_COLLISION,
    CASE_21_FILTER_ADD_DIRECTION,
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


# NOTE (Build 3, Day 2, Part 15): the synthetic test that used to live here
# (test_excluded_statuses_and_filter_same_fact_at_high_confidence_crashes_the_shapley_pair,
# Part 14) documented a real crash for the "active-user convention"
# standalone-excluded_statuses scenario -- excluded_statuses and filter
# colliding at confidence="high" reached the Shapley-pair branch and
# crashed constructing the joint counterfactual. Decision 22 (Part 15,
# docs/decisions.md) fixed this directly by extending the
# filter/excluded_statuses suppression rule to fire at confidence="high"
# too (suppressing filter, keeping excluded_statuses -- the reverse
# direction from the low-confidence case). That synthetic test's premise
# (an unresolvable crash) is no longer true, and it cannot be converted to
# a positive test in place -- once the correction actually runs to
# completion it needs real seed data to execute against, not a
# "nonexistent_seed_table" placeholder. The real proof now lives with the
# fully-authored AMBIGUOUS_ACTIVE_USER_CONVENTION scenario and its own
# committed tests (tests/test_benchmark_pipeline.py), the same template
# every other ambiguous scenario in this project uses.


def test_case_13_full_pipeline_now_reconciles_the_filter_removal():
    """Build 3, Day 1, Part 6: proves the filter/excluded_statuses
    suppression rule (assemble_structural_and_definitional_evidence,
    src/self_consistency.py) actually reaches assemble_investigation_evidence,
    not just the isolated unit-level proof in
    tests/test_structural_definitional_precedence.py. Before Part 6,
    Case 13 reached this function's Shapley-pair branch (2 remaining
    causes: excluded_statuses + filter) and raised inside the
    excluded_statuses correction itself (Part 4/5's finding). After Part
    6's suppression, only `filter` survives -- exactly 1 remaining cause,
    routed through the single-cause branch instead.

    Build 3, Day 2, Part 13 built apply_filter_correction's ADD direction
    only -- CASE_13's source_a is the side WITH the filter
    (`status NOT IN ('churned')`), source_b has none, so correcting
    source_a toward source_b's value means REMOVING an existing predicate,
    which crashed through Build 3, Day 2, Part 15. Build 3, Day 2 cleanup,
    Part 1 built the REMOVE direction (apply_filter_removal,
    src/query_mutation.py) -- CASE_13 now reconciles CLEANLY end to end,
    reaching an exact 0.0 unexplained_residual (both sides share
    identical underlying orders data per the fixture's own docstring, so
    removing source_a's extraneous exclusion reproduces source_b's real
    value exactly) instead of raising. Still deliberately NOT added to
    SCENARIOS -- this fixture's job is proving the collision/suppression
    machinery on freshly built data, not benchmark participation."""
    evidence = assemble_investigation_evidence(CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION)

    assert evidence.definition_differences == []
    assert [d.category for d in evidence.sql_differences] == ["filter"]
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].dollar_impact == -150.0
    assert evidence.unexplained_residual == 0.0


def test_case_14_full_pipeline_completes_with_a_real_but_large_residual():
    """Build 3, Day 3, Part 6, decision 27 (docs/decisions.md): Case 14
    is the first fixture in this project where one member of a genuinely
    interacting Shapley-pair is low-confidence/inferred rather than
    declared. This proves TWO things at once, both load-bearing for why
    the fixture stays out of SCENARIOS rather than being treated as
    broken:

    1. The pipeline does NOT crash -- assemble_investigation_evidence
       returns a real InvestigationEvidence, with both date_field and
       excluded_statuses attributed via shapley_pair_attribution. This
       was the fixture's actual design goal, and it holds.
    2. unexplained_residual is 650.0, NOT 0.0, even though this is a
       clean two-cause fixture with no data-quality cause and no third
       interacting cause -- the same standard every other clean
       technical fixture (Cases 1-4, 7) reconciles to exactly. Root
       cause (decision 27): correcting excluded_statuses toward source_b's
       inferred value ("(none)", the fixed placeholder returned for
       EVERY low-confidence case) means "remove source_a's exclusion
       filter entirely," which is not what source_b's SQL actually does
       (an inclusion filter, "only active") -- a real, structural gap in
       what this schema's excluded_statuses vocabulary can represent as
       a correction target, not a bug in the Shapley/reconciliation
       arithmetic itself (the individual line items are exactly what a
       hand-reconstruction of the four counterfactual queries confirms)."""
    evidence = assemble_investigation_evidence(CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES)

    assert [d.field for d in evidence.definition_differences] == ["date_field", "excluded_statuses"]
    assert evidence.sql_differences == []
    assert [item.computed_by for item in evidence.reconciliation] == [
        "shapley_pair_attribution",
        "shapley_pair_attribution",
    ]

    dollar_impacts = {item.cause.split(":")[0]: item.dollar_impact for item in evidence.reconciliation}
    assert dollar_impacts["date_field"] == 100.0
    assert dollar_impacts["excluded_statuses"] == -300.0
    assert evidence.unexplained_residual == 650.0
    assert CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES not in SCENARIOS


def test_case_15_fully_reconciles_single_cause_inferred_date_field():
    """Case 15 (Build 3, Day 3, Part 7 -- finalized 8-scenario list, item
    1): the first fixture with an inferred-vs-inferred (not declared-vs-
    inferred) date_field cause, added directly to SCENARIOS since it
    reconciles exactly, matching Cases 1-4/7's own standard -- unlike
    Case 14, there is no schema-representation gap here (excluded_statuses
    never differs at all, so apply_excluded_statuses_correction's lossy
    "(none)" placeholder is never invoked)."""
    evidence = assemble_investigation_evidence(CASE_15_DATE_FIELD_INFERRED_ONLY)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert [d.field for d in evidence.definition_differences] == ["date_field"]
    assert evidence.sql_differences == []
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].computed_by == "single_cause_attribution"
    assert total == CASE_15_DATE_FIELD_INFERRED_ONLY.known_gap == 100.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_15_DATE_FIELD_INFERRED_ONLY in SCENARIOS


def test_case_16_fully_reconciles_single_cause_declared_excluded_statuses():
    """Case 16 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    2): a clean cross-source declared-vs-declared excluded_statuses diff,
    a second data point alongside Case 4 at a different scale, with no
    self-consistency issue on either side."""
    evidence = assemble_investigation_evidence(CASE_16_EXCLUDED_STATUSES_DECLARED)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert [d.field for d in evidence.definition_differences] == ["excluded_statuses"]
    assert evidence.sql_differences == []
    assert evidence.self_consistency_issues == []
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].computed_by == "single_cause_attribution"
    assert total == CASE_16_EXCLUDED_STATUSES_DECLARED.known_gap == 300.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_16_EXCLUDED_STATUSES_DECLARED in SCENARIOS


def test_case_17_fully_reconciles_interacting_join_type_and_excluded_statuses():
    """Case 17 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    3): the first structural x definitional interacting pair anywhere in
    this project, closing decision 11's longest-standing untested scope
    gap. order_id=3 is the genuinely overlapping row both causes act on
    (an orphan FK row that is ALSO excluded by source_b's declared
    excluded_statuses). Real Shapley split: 150.0/150.0, summing to
    known_gap exactly."""
    evidence = assemble_investigation_evidence(CASE_17_JOIN_TYPE_EXCLUDED_STATUSES_INTERACTING)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert [d.field for d in evidence.definition_differences] == ["excluded_statuses"]
    assert [d.category for d in evidence.sql_differences] == ["join_type"]
    assert [item.computed_by for item in evidence.reconciliation] == [
        "shapley_pair_attribution",
        "shapley_pair_attribution",
    ]
    dollar_impacts = {item.cause.split(":")[0].split(" ")[0]: item.dollar_impact for item in evidence.reconciliation}
    assert dollar_impacts["excluded_statuses"] == 150.0
    assert dollar_impacts["source_a"] == 150.0  # join_type's cause text starts with "source_a uses ..."
    assert total == CASE_17_JOIN_TYPE_EXCLUDED_STATUSES_INTERACTING.known_gap == 300.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_17_JOIN_TYPE_EXCLUDED_STATUSES_INTERACTING in SCENARIOS


def test_case_18_fully_reconciles_single_cause_join_type_new_domain():
    """Case 18 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    4): a second real join_type data point on a wholly different table
    pairing (tickets/agents, not orders/customers) from Case 1 -- same
    orphan-fact-row mechanism, different domain."""
    evidence = assemble_investigation_evidence(CASE_18_JOIN_TYPE_ONLY)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert evidence.definition_differences == []
    assert [d.category for d in evidence.sql_differences] == ["join_type"]
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].computed_by == "single_cause_attribution"
    assert total == CASE_18_JOIN_TYPE_ONLY.known_gap == 200.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_18_JOIN_TYPE_ONLY in SCENARIOS


def test_case_19_fully_reconciles_declared_date_field_excluded_statuses_high_confidence():
    """Case 19 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    5): a technical mirror of AMBIGUOUS_REVENUE_RECOGNITION's own shape --
    both declared, high confidence, no business-rule ambiguity. Also the
    first purely technical fixture to exercise decision 12's date_field
    suppression at confidence="high" (Case 3/14/15 were all "medium")."""
    evidence = assemble_investigation_evidence(CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert [d.field for d in evidence.definition_differences] == ["date_field", "excluded_statuses"]
    assert evidence.sql_differences == []
    assert [item.computed_by for item in evidence.reconciliation] == [
        "shapley_pair_attribution",
        "shapley_pair_attribution",
    ]
    dollar_impacts = {item.cause.split(":")[0]: item.dollar_impact for item in evidence.reconciliation}
    assert dollar_impacts["date_field"] == -300.0
    assert dollar_impacts["excluded_statuses"] == 200.0
    assert total == CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED.known_gap == -100.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED in SCENARIOS


def test_case_20_join_type_reconciles_while_data_quality_stays_additive_only():
    """Case 20 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    7): the first fixture in SCENARIOS with BOTH a real reconciliation
    entry (join_type, from an orphan row present in both sides' actual
    data) AND a real data_quality_issues entry (stale_extract, from a
    DIFFERENT row missing from source_a's as-delivered snapshot) --
    deliberately dispatched, unlike Case 1/12. The two facts are
    independent (different rows), so join_type alone does not explain
    known_gap -- unexplained_residual (-200.0) is a real, honest number
    that happens to equal the unfolded data-quality dollar_impact exactly
    for this fixture's numbers (decision 17's coexistence risk, not a
    double-count: naively summing reconciliation's +300.0 and data-
    quality's -200.0 would actually land on known_gap correctly here,
    which is NOT true in general and must not be assumed)."""
    evidence = assemble_investigation_evidence(CASE_20_STALE_EXTRACT_JOIN_COLLISION)

    assert evidence.definition_differences == []
    assert [d.category for d in evidence.sql_differences] == ["join_type"]
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].computed_by == "single_cause_attribution"
    assert evidence.reconciliation[0].dollar_impact == 300.0

    assert len(evidence.data_quality_issues) == 1
    issue = evidence.data_quality_issues[0]
    assert issue.category == "stale_extract"
    assert issue.source == "a"
    assert issue.dollar_impact == -200.0

    assert CASE_20_STALE_EXTRACT_JOIN_COLLISION.known_gap == 100.0
    assert evidence.unexplained_residual == -200.0
    assert CASE_20_STALE_EXTRACT_JOIN_COLLISION in SCENARIOS


def test_case_21_fully_reconciles_filter_add_direction():
    """Case 21 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    8): apply_filter_correction's ADD direction (decision 20), proven
    outside the ambiguous set for the first time -- source_a is missing a
    real, non-status filter (`region`) that source_b has; correcting A
    toward B adds the predicate."""
    evidence = assemble_investigation_evidence(CASE_21_FILTER_ADD_DIRECTION)
    total = sum(item.dollar_impact for item in evidence.reconciliation)

    assert evidence.definition_differences == []
    assert [d.category for d in evidence.sql_differences] == ["filter"]
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].computed_by == "single_cause_attribution"
    assert total == CASE_21_FILTER_ADD_DIRECTION.known_gap == 200.0
    assert evidence.unexplained_residual == 0.0
    assert CASE_21_FILTER_ADD_DIRECTION in SCENARIOS


def test_ambiguous_date_columns_reaches_full_pipeline_as_a_completed_escalation():
    """Build 3, Day 2 cleanup, Part 1's own verification pass (decision 40
    update): proves, via the REAL full assemble_investigation_evidence
    pipeline (not an isolated call to assemble_reconciliation_line_items
    alone), that a genuinely ambiguous date_field correction (source_a
    filters on TWO real date-like columns, order_date AND created_at,
    both present on case_03_hybrid_fallback's own orders table) no longer
    aborts the whole investigation.

    Traced by hand before this fix (via a throwaway script, not committed):
    assemble_investigation_evidence(scenario) raised
    src.query_mutation.DateFieldCorrectionAmbiguous uncaught -- no
    InvestigationEvidence was produced at all for this scenario, directly
    contradicting the original brief's own requirement that a genuinely
    ambiguous correction "should still produce a clearly-typed 'escalate'
    result." Confirmed fixed here: the call now completes normally and
    the ambiguity is visible as a real CorrectionEscalation, not silently
    dropped and not guessed at.

    Not registered in SCENARIOS -- like Case 12/13, this fixture's job is
    proving one specific mechanism on freshly constructed data, not
    benchmark participation. source_a's own SelfConsistencyIssue
    (declared 'created_at' vs its SQL's own inferred pick 'order_date',
    since both sides declare the SAME definition) is a real, expected
    byproduct of this construction, not a second bug -- asserted on
    explicitly below so it isn't mistaken for interference with the
    escalation being tested."""
    declared = DeclaredDefinition(date_field="created_at", excluded_statuses=[], aggregation="sum")
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND created_at >= '2024-01-01'"
        ),
        declared_definition=declared,
    )
    source_b = DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01'",
        declared_definition=declared,
    )
    scenario = Scenario(
        scenario_id="scratch_ambiguous_date_columns_full_pipeline",
        description="Proof fixture: source_a filters on two real date-like columns at once.",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=550.0,
        reported_value_b=1050.0,
        known_gap=550.0 - 1050.0,
        seed_table="case_03_hybrid_fallback",
    )

    evidence = assemble_investigation_evidence(scenario)

    assert [d.category for d in evidence.sql_differences] == ["date_field"]
    assert len(evidence.escalations) == 1
    escalation = evidence.escalations[0]
    assert "ambiguous" in escalation.reason
    assert "created_at" in escalation.reason and "order_date" in escalation.reason
    assert evidence.data_quality_issues == []
    # source_b's own SQL contradicts source_b's declared 'created_at' (its SQL
    # only ever filters on order_date) -- a real, expected self-consistency
    # finding this construction produces, not evidence the escalation failed.
    assert len(evidence.reconciliation) == 1
    assert evidence.reconciliation[0].computed_by == "self_consistency_correction"


def test_grouping_mismatch_reaches_full_pipeline_as_a_completed_escalation():
    """Follow-up to decision 40's verification pass: applies the same
    catch-and-escalate fix now built for UnsupportedCorrectionCategory
    (src/reconciliation_assembly.py's _single_cause_line_item) and proves
    it via the REAL full assemble_investigation_evidence pipeline, the
    same standard used for the date_field-ambiguous fix.

    Reuses the exact real fixture decision 40's verification pass
    constructed to prove `grouping` is a reachable, plausible finding (a
    per-customer breakdown dashboard vs. a company-total dashboard) --
    before this fix, this crashed with an uncaught
    UnsupportedCorrectionCategory, identical in shape to the
    DateFieldCorrectionAmbiguous crash decision 40's verification pass
    already fixed. Confirmed fixed here: the call completes normally and
    the grouping mismatch is visible as a real CorrectionEscalation."""
    declared = DeclaredDefinition(date_field="order_date", excluded_statuses=[], aggregation="sum")
    source_a = DashboardSource(
        label="dashboard_a",
        sql="SELECT customer_id, SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01' GROUP BY customer_id",
        declared_definition=declared,
    )
    source_b = DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01'",
        declared_definition=declared,
    )
    scenario = Scenario(
        scenario_id="scratch_grouping_full_pipeline",
        description="Proof fixture: source_a groups by customer_id, source_b reports a flat total.",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=0.0,
        reported_value_b=800.0,
        known_gap=0.0 - 800.0,
        seed_table="case_01_join_type",
    )

    evidence = assemble_investigation_evidence(scenario)

    assert [d.category for d in evidence.sql_differences] == ["grouping"]
    assert len(evidence.escalations) == 1
    escalation = evidence.escalations[0]
    assert "no automatic correction rule exists" in escalation.reason
    assert evidence.data_quality_issues == []
    assert evidence.reconciliation == []
    assert evidence.unexplained_residual == scenario.known_gap


def test_self_consistency_ambiguous_date_columns_reaches_full_pipeline_as_escalation():
    """Build 3, Day 2 cleanup, Part 2: proves, via the REAL full
    assemble_investigation_evidence pipeline, that self_consistency.py's
    own DateFieldCorrectionAmbiguous crash path (decision 40's verification
    pass flagged this as live and confirmed-reachable, distinct from and
    unfixed by _single_cause_line_item's own fix) no longer aborts the
    whole investigation.

    Traced by hand before this fix (via a throwaway script, not committed):
    assemble_investigation_evidence(scenario) raised
    src.query_mutation.DateFieldCorrectionAmbiguous uncaught, from inside
    compute_self_consistency_dollar_impacts (src/self_consistency.py) --
    entirely before assemble_reconciliation_line_items is ever reached, a
    different call path from _single_cause_line_item's own crash. Confirmed
    fixed here: the call completes normally and the ambiguity is visible
    as a real CorrectionEscalation."""
    declared = DeclaredDefinition(date_field="order_date", excluded_statuses=[], aggregation="sum")
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND created_at >= '2024-01-01'"
        ),
        declared_definition=declared,
    )
    source_b = DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01'",
        declared_definition=declared,
    )
    scenario = Scenario(
        scenario_id="scratch_self_consistency_ambiguous_full_pipeline",
        description="Proof fixture: source_a's own SQL filters on two real date-like columns at once.",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=550.0,
        reported_value_b=1050.0,
        known_gap=550.0 - 1050.0,
        seed_table="case_03_hybrid_fallback",
    )

    evidence = assemble_investigation_evidence(scenario)

    assert evidence.self_consistency_issues == []
    # Two escalations fire independently on the same real ambiguity: one
    # from _single_cause_line_item's own fix (the competing
    # SQLStructuralDifference date_field finding this construction also
    # produces, since source_a/source_b use different real date columns),
    # one from this Part's own self-consistency fix. Both are real,
    # neither is a duplicate -- confirmed by their distinct `cause` text.
    assert len(evidence.escalations) == 2
    self_consistency_escalation = next(e for e in evidence.escalations if "own SQL implements" in e.cause)
    assert "date_field='created_at'" in self_consistency_escalation.cause
    assert "declared 'order_date'" in self_consistency_escalation.cause
    assert evidence.data_quality_issues == []


def test_self_consistency_missing_date_columns_reaches_full_pipeline_as_escalation():
    """Same fix, the zero-columns shape, proven via the real full pipeline.
    Both sides declare identically and run the IDENTICAL SQL text
    (deliberately -- keeps sql_differences/definition_differences both
    empty, isolating this test to the self-consistency path alone rather
    than also exercising the unrelated, out-of-scope Shapley-pair branch a
    genuinely differing pair of sides would land in). Each side
    independently has the same real, unrelated excluded_statuses
    self-consistency issue (dollar-quantified correctly) alongside its own
    date_field escalation -- symmetric, cancels to an exact 0.0 residual,
    confirming partial success and escalation coexist correctly at the
    full-pipeline level too."""
    declared = DeclaredDefinition(date_field="order_date", excluded_statuses=[], aggregation="sum")
    sql = "SELECT SUM(amount) AS revenue FROM orders WHERE status != 'cancelled'"
    source_a = DashboardSource(label="dashboard_a", sql=sql, declared_definition=declared)
    source_b = DashboardSource(label="finance_query", sql=sql, declared_definition=declared)
    scenario = Scenario(
        scenario_id="scratch_self_consistency_missing_full_pipeline",
        description="Proof fixture: both sides declare a date_field their identical SQL never filters by.",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=100.0,
        reported_value_b=100.0,
        known_gap=0.0,
        seed_table="case_03_hybrid_fallback",
    )

    evidence = assemble_investigation_evidence(scenario)

    assert evidence.sql_differences == []
    assert evidence.definition_differences == []
    assert len(evidence.escalations) == 2
    assert all("does not filter by date at all" in e.reason for e in evidence.escalations)
    assert {i.source for i in evidence.self_consistency_issues} == {"a", "b"}
    assert all(i.declared_field == "excluded_statuses" for i in evidence.self_consistency_issues)
    assert evidence.unexplained_residual == 0.0


def test_self_consistency_ambiguous_with_suppressed_cross_source_still_completes():
    """Build 3, Day 2 cleanup, Part 2's own transitivity proof: an
    ambiguous date_field self-consistency issue that ALSO has a genuine
    suppressed cross-source counterpart (source_a and source_b declare
    genuinely different date_field values) does not crash the second,
    chained construct_corrected_query call inside
    assemble_definitional_evidence_with_dollar_impacts's own suppressed-
    cause folding (the "transitively safe" claim documented on that
    function) -- because the ambiguous issue is filtered out by
    compute_self_consistency_dollar_impacts before that chained call is
    ever attempted. Also exercises _single_cause_line_item's own
    DateFieldCorrectionAmbiguous fix (decision 40) side by side, on the
    SQLStructuralDifference this same construction produces -- both fixes
    fire independently on the same real ambiguity, both as
    CorrectionEscalations, neither as a crash."""
    declared_a = DeclaredDefinition(date_field="order_date", excluded_statuses=[], aggregation="sum")
    declared_b = DeclaredDefinition(date_field="created_at", excluded_statuses=[], aggregation="sum")
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND created_at >= '2024-01-01'"
        ),
        declared_definition=declared_a,
    )
    source_b = DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE created_at >= '2024-01-01'",
        declared_definition=declared_b,
    )
    scenario = Scenario(
        scenario_id="scratch_self_consistency_transitivity",
        description="Proof fixture: ambiguous self-consistency issue with a real suppressed cross-source counterpart.",
        source_a=source_a,
        source_b=source_b,
        reported_value_a=550.0,
        reported_value_b=1150.0,
        known_gap=550.0 - 1150.0,
        seed_table="case_03_hybrid_fallback",
    )

    evidence = assemble_investigation_evidence(scenario)

    assert evidence.definition_differences == []  # suppressed, per the precedence rule
    assert evidence.self_consistency_issues == []  # excluded -- dollar_impact could not be computed
    assert len(evidence.escalations) == 2  # one from _single_cause_line_item, one from self-consistency
    assert evidence.unexplained_residual == scenario.known_gap
