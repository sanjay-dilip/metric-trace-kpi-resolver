"""Benchmark-only metadata wrapping the fixtures in scenarios.py, so a future
eval-harness scorer knows which InvestigationEvidence field carries the
correct root-cause answer for a given scenario, without re-deriving that
per-scenario knowledge from scratch. Wraps Scenario rather than modifying it
-- src/scenario.py stays evidence-representation-only, per its own module
docstring; a scorer's "where do I look" metadata does not belong there.
Populated directly from the Build 3, Day 2, Part 1 benchmark-fitness audit
(GitHub issue #74), not re-derived."""

from typing import Literal

from pydantic import BaseModel

from src.scenario import Scenario
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
)
from tests.fixtures.ambiguous_scenarios import (
    AMBIGUOUS_ACTIVE_USER_CONVENTION,
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_REFUND_TIMING,
    AMBIGUOUS_REVENUE_RECOGNITION,
)


class BenchmarkEntry(BaseModel):
    """One scenario plus the benchmark-only metadata an eval-harness scorer
    needs to grade it correctly -- not part of the evidence-assembly pipeline
    itself, and not consumed by any src/ code."""

    scenario: Scenario
    """The underlying fixture, unmodified."""

    ground_truth_check_field: Literal[
        "reconciliation", "data_quality_issues", "definition_differences", "none"
    ]
    """Which InvestigationEvidence field a scorer must inspect to determine
    whether the correct cause was found. "none" means no cause exists and
    none should be reported -- distinct from "reconciliation" pointing to an
    empty list for a different reason (a real gap with no findable cause),
    and distinct from "data_quality_issues" cases where reconciliation is
    empty but a real cause exists elsewhere in the evidence object.
    "definition_differences" (Build 3, Day 2, Part 4) is for scenarios run
    through assemble_investigation_evidence_for_benchmark's ambiguous-
    scenario partial-evidence path (tests/fixtures/benchmark_pipeline.py):
    reconciliation is always [] there by construction (interaction beyond
    a single pair is never Shapley-attributed), so the real, correctly-
    found cause only appears in definition_differences (and/or
    sql_differences/self_consistency_issues) -- pointing a scorer at
    "reconciliation" for such a scenario would be actively wrong, not
    just incomplete."""

    is_ambiguous: bool
    """Per decision 6's two-metric split (escalation recall vs.
    false-escalation rate): True for a genuinely ambiguous business-rule
    scenario that should be escalated to a human, False for a technical/
    deterministic scenario that should be answered directly."""

    expected_behavior: Literal["answer", "escalate"]
    """What a correctly-behaving system should do with this scenario.
    "escalate" does not mean the mechanical cause goes unfound -- it means
    the system must not unilaterally declare either side wrong once it has
    found it; see is_ambiguous's own docstring for the metric this drives."""

    notes: str | None = None
    """One-sentence scorer-relevant caveat that doesn't fit the two fields
    above -- e.g. a correct answer split across multiple reconciliation line
    items, or a correct answer that only appears once suppression has moved
    it into a different evidence field. None when no caveat applies."""


BENCHMARK_ENTRIES: list[BenchmarkEntry] = [
    BenchmarkEntry(
        scenario=CASE_1_JOIN_TYPE,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=CASE_2_MULTI_CAUSE,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
        notes=(
            "Correct answer is split across two reconciliation line items "
            "(excluded_statuses +120.0, aggregation -100.0) that only sum "
            "to known_gap (+20.0) together -- same shape as Case 3's note, "
            "at Build 3 Day 2 Part 2b's recalibrated magnitude."
        ),
    ),
    BenchmarkEntry(
        scenario=CASE_3_HYBRID_FALLBACK,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
        notes=(
            "Correct answer is split across two reconciliation line items "
            "(date_field -400.0, excluded_statuses +500.0) that only sum to "
            "known_gap together, and both are medium-confidence inferred "
            "findings, not declared ones -- full credit must not require "
            "declared/high-confidence provenance."
        ),
    ),
    BenchmarkEntry(
        scenario=CASE_4_GOVERNANCE_DRIFT,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=CASE_5_UNEXPLAINED_RESIDUAL,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
        notes=(
            "Empty reconciliation is the correct answer here, unlike Case "
            "6: this scenario has a nonzero known_gap with no findable "
            "cause, not a zero known_gap with nothing to investigate."
        ),
    ),
    BenchmarkEntry(
        scenario=CASE_6_NEGATIVE_CONTROL,
        ground_truth_check_field="none",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=CASE_7_PRECEDENCE_CONFLICT,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
        notes=(
            "The real cross-source excluded_statuses conflict is "
            "suppressed from definition_differences and only appears "
            "folded into self_consistency_issues[0].dollar_impact -- "
            "checking definition_differences alone gives a false negative."
        ),
    ),
    BenchmarkEntry(
        scenario=CASE_8_STALE_EXTRACT,
        ground_truth_check_field="data_quality_issues",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=CASE_9_MISSING_PARTITION,
        ground_truth_check_field="data_quality_issues",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=CASE_10_REFERENTIAL_INTEGRITY,
        ground_truth_check_field="data_quality_issues",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B,
        ground_truth_check_field="data_quality_issues",
        is_ambiguous=False,
        expected_behavior="answer",
    ),
    BenchmarkEntry(
        scenario=AMBIGUOUS_REFUND_TIMING,
        ground_truth_check_field="reconciliation",
        is_ambiguous=True,
        expected_behavior="escalate",
        notes=(
            "A correct response finds and states the mechanical cause "
            "(source_a declares date_field='refund_date', source_b "
            "declares date_field='purchase_date', dollar_impact=350.0, "
            "matching known_gap exactly) AND declines to declare either "
            "side wrong -- both are legitimate accounting conventions. "
            "Escalation does not mean the cause goes unfound."
        ),
    ),
    BenchmarkEntry(
        scenario=AMBIGUOUS_REVENUE_RECOGNITION,
        ground_truth_check_field="reconciliation",
        is_ambiguous=True,
        expected_behavior="escalate",
        notes=(
            "Build 3, Day 2, Part 15 (decision 22) update: this scenario now "
            "completes through assemble_investigation_evidence DIRECTLY, no "
            "wrapper needed -- decision 22 extends the filter/excluded_statuses "
            "suppression rule to confidence='high' (suppressing the redundant "
            "filter finding rather than excluded_statuses, the reverse of the "
            "low-confidence direction), which reduces this scenario from 3 "
            "remaining causes to 2, routed to the ordinary Shapley-pair branch. "
            "Real, execution-verified figures: date_field -1200.0, "
            "excluded_statuses +1150.0, unexplained_residual=0.0. Superseded "
            "the prior 'run through assemble_investigation_evidence_for_benchmark' "
            "note -- Known-Safe Pattern 1 now, not Pattern 2. A correct "
            "response still declines to declare either convention wrong, "
            "even with a complete reconciliation available."
        ),
    ),
    BenchmarkEntry(
        scenario=AMBIGUOUS_CUSTOMER_COUNTING,
        ground_truth_check_field="reconciliation",
        is_ambiguous=True,
        expected_behavior="escalate",
        notes=(
            "Build 3, Day 2, Part 15 (decision 22) update: same shape as "
            "AMBIGUOUS_REVENUE_RECOGNITION -- completes through "
            "assemble_investigation_evidence DIRECTLY (2 remaining causes "
            "post-suppression, not 3), no wrapper needed. "
            "Build 3, Day 3, Part 1 root-caused a then-real 50.0 "
            "unexplained_residual (date_field -0.0, excluded_statuses "
            "+100.0) to a genuine bug: apply_date_field_correction "
            "(src/query_mutation.py) swapped only the date COLUMN name "
            "(signup_date -> last_active_date) and never adjusted the "
            "comparison THRESHOLD value, silently keeping source_a's own "
            "'>= 2000-01-01' cutoff instead of adopting source_b's real "
            "'>= 2024-01-01' one -- a no-op filter matching every row, "
            "which is why date_field's attributed dollar_impact read as "
            "-0.0. Confirmed at the time (by direct re-execution) that no "
            "third cause was silently missed and the seed data had not "
            "drifted -- the residual was purely a correction-mechanism "
            "artifact, not new information about the scenario itself. "
            "Build 3, Day 3, Part 2 FIXED this: construct_corrected_query "
            "gained an other_side_sql parameter (src/query_mutation.py's "
            "new _extract_date_predicate_snippet), so a date_field "
            "DefinitionDifference correction now adopts the target side's "
            "real threshold, not just its column name. Real, "
            "execution-verified figures, post-fix: date_field=100.0, "
            "excluded_statuses=50.0, summing to known_gap=150.0 exactly -- "
            "unexplained_residual is now 0.0. This scenario now fully "
            "reconciles, the same as revenue_recognition/attribution; the "
            "'partially-explained, not fully reconciled' framing from Day 3 "
            "Part 1 no longer applies. A correct response still declines to "
            "declare either convention wrong, even with a complete "
            "reconciliation available."
        ),
    ),
    BenchmarkEntry(
        scenario=AMBIGUOUS_ATTRIBUTION,
        ground_truth_check_field="reconciliation",
        is_ambiguous=True,
        expected_behavior="escalate",
        notes=(
            "Build 3, Day 2, Part 15 (decision 22) update: same shape as "
            "AMBIGUOUS_REVENUE_RECOGNITION/CUSTOMER_COUNTING -- now completes "
            "through assemble_investigation_evidence DIRECTLY (2 remaining "
            "causes post-suppression, not 3), no wrapper needed. Real, "
            "execution-verified figures: date_field -11500.0, "
            "excluded_statuses +11000.0, unexplained_residual=0.0. A correct "
            "response still declines to declare either convention wrong, "
            "even with a complete reconciliation available."
        ),
    ),
    BenchmarkEntry(
        scenario=AMBIGUOUS_CURRENCY_TIMING,
        ground_truth_check_field="reconciliation",
        is_ambiguous=True,
        expected_behavior="escalate",
        notes=(
            "Deliberately single-cause (Build 3, Day 2, Part 9), unlike "
            "AMBIGUOUS_REVENUE_RECOGNITION/CUSTOMER_COUNTING/ATTRIBUTION -- "
            "routes through assemble_investigation_evidence directly (no "
            "wrapper needed), returning a real InvestigationEvidence with "
            "exactly one reconciliation line item (date_field, "
            "dollar_impact=-2200.0, matching known_gap exactly) and "
            "unexplained_residual=0.0. Included specifically to test "
            "escalation recall against a case with a clean, statable "
            "finding: a correct response finds and states the cause AND "
            "still declines to declare either convention (transaction-date "
            "spot rate vs. period-close rate) wrong -- finding one clean "
            "cause does not mean the system should silently pick a side."
        ),
    ),
    BenchmarkEntry(
        scenario=AMBIGUOUS_ACTIVE_USER_CONVENTION,
        ground_truth_check_field="reconciliation",
        is_ambiguous=True,
        expected_behavior="escalate",
        notes=(
            "Build 3, Day 2, Part 15 (decision 22): the first ambiguous "
            "scenario testing excluded_statuses as a STANDALONE dimension "
            "(no co-occurring date_field difference) -- drafted Part 12, "
            "blocked there and again in Part 14 by two separate code "
            "gaps, unblocked here by decision 22's confidence-gate "
            "extension. Raw sql_diff/definition_diff produce a real "
            "filter/excluded_statuses collision on the same status column "
            "at confidence='high'; decision 22 suppresses the redundant "
            "`filter` finding, leaving exactly ONE remaining cause -- "
            "routes through assemble_investigation_evidence directly (no "
            "wrapper needed), returning a real InvestigationEvidence with "
            "exactly one reconciliation line item (excluded_statuses, "
            "dollar_impact=150.0, matching known_gap exactly) and "
            "unexplained_residual=0.0. A correct response still declines "
            "to declare either convention (still-provisioned/capacity vs. "
            "currently-engaged/health) wrong, even with a clean, single, "
            "fully-reconciled cause."
        ),
    ),
]
"""Build 3, Day 2, Part 2a: all eleven Case 1-11 fixtures, populated
directly from the Build 3, Day 2, Part 1 benchmark-fitness audit (issue
#74) -- not re-derived. Case 2 was originally deliberately excluded pending
its own recalibration task (the audit flagged its dollar magnitudes as
toy-scale relative to every other fixture); Build 3, Day 2, Part 2b
recalibrated its seed data to a realistic magnitude and added its entry
here. Build 3, Day 2, Part 3 (proof-of-concept, issue #79) added the first
ambiguous-scenario entry, AMBIGUOUS_REFUND_TIMING (tests/fixtures/
ambiguous_scenarios.py). Build 3, Day 2, Part 4 (issue #81) unblocked and
added the second, AMBIGUOUS_REVENUE_RECOGNITION -- ground_truth_check_field
="definition_differences", since it must be run through
assemble_investigation_evidence_for_benchmark (tests/fixtures/
benchmark_pipeline.py), not assemble_investigation_evidence directly.
Build 3, Day 2, Part 7 (issue #86) added two more, AMBIGUOUS_CUSTOMER_COUNTING
and AMBIGUOUS_ATTRIBUTION, both hitting the exact same 3+-cause shape as
AMBIGUOUS_REVENUE_RECOGNITION and both confirmed to route cleanly through
the wrapper at authoring time (Known-Safe Pattern 2 as it existed then --
see the Part 15 update below, this no longer holds for any of the three).
Build 3, Day 2, Part 9 (issue #90) added AMBIGUOUS_CURRENCY_TIMING,
deliberately single-cause (Known-Safe Pattern 1, same shape as
AMBIGUOUS_REFUND_TIMING). Two further topics were attempted and found
BLOCKED, not authored, in the same stretch of sessions, each for a
distinct, execution-proven reason: cost-allocation basis and
aggregation-basis/gross-vs-net (Parts 9/11) -- this project's
DeclaredDefinition schema has only three fields (date_field,
excluded_statuses, aggregation, function-only), and neither convention is
representable as a single, honest DefinitionDifference in any of them
(see tests/fixtures/ambiguous_scenarios.py's own comment, just above
AMBIGUOUS_CURRENCY_TIMING, for the full proof).

Build 3, Day 2, Part 15 (issue TBD, decision 22) added a sixth real
scenario, AMBIGUOUS_ACTIVE_USER_CONVENTION -- the standalone-excluded_statuses
scenario drafted in Part 12, blocked there and again in Part 14 by two
separate code gaps (a zero-predicate correction gap, then a real
confidence='high' filter/excluded_statuses same-fact collision that
crashed the Shapley-pair engine), unblocked by decision 22's extension of
the filter/excluded_statuses suppression rule to fire at ANY confidence
level. That same rule change had a consequence this benchmark's earlier
entries did not anticipate: AMBIGUOUS_REVENUE_RECOGNITION,
AMBIGUOUS_CUSTOMER_COUNTING, and AMBIGUOUS_ATTRIBUTION ALL share the exact
same confidence='high' filter/excluded_statuses collision shape, so all
three now ALSO complete directly through assemble_investigation_evidence
(2 remaining causes, Shapley-paired) instead of needing the benchmark
wrapper -- their ground_truth_check_field entries above were updated from
"definition_differences" to "reconciliation" accordingly, with real,
re-verified dollar figures. AMBIGUOUS_CUSTOMER_COUNTING's reconciliation
initially showed a genuine, non-zero unexplained_residual (50.0) -- the
first time this scenario's full dollar math had ever actually been
computed, reported honestly rather than force-fit. Build 3, Day 3 (Parts
1-2) root-caused and then FIXED that residual: it was apply_date_field_correction
silently keeping the wrong side's date threshold, not a property of the
scenario itself -- construct_corrected_query's new other_side_sql
parameter (src/query_mutation.py) fixes it, and this entry's figures were
recalibrated (date_field=100.0, excluded_statuses=50.0,
unexplained_residual=0.0), same as this docstring entry's own note above.
6 real ambiguous scenarios now exist; 2 further topics are
confirmed-blocked, not abandoned speculatively. The eventual total this
benchmark's decision-6 split should target is an open question -- see
CONTEXT.md, not settled by this docstring."""
