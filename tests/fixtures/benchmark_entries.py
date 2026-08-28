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
from tests.fixtures.ambiguous_scenarios import AMBIGUOUS_REFUND_TIMING


class BenchmarkEntry(BaseModel):
    """One scenario plus the benchmark-only metadata an eval-harness scorer
    needs to grade it correctly -- not part of the evidence-assembly pipeline
    itself, and not consumed by any src/ code."""

    scenario: Scenario
    """The underlying fixture, unmodified."""

    ground_truth_check_field: Literal["reconciliation", "data_quality_issues", "none"]
    """Which InvestigationEvidence field a scorer must inspect to determine
    whether the correct cause was found. "none" means no cause exists and
    none should be reported -- distinct from "reconciliation" pointing to an
    empty list for a different reason (a real gap with no findable cause),
    and distinct from "data_quality_issues" cases where reconciliation is
    empty but a real cause exists elsewhere in the evidence object."""

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
]
"""Build 3, Day 2, Part 2a: all eleven Case 1-11 fixtures, populated
directly from the Build 3, Day 2, Part 1 benchmark-fitness audit (issue
#74) -- not re-derived. Case 2 was originally deliberately excluded pending
its own recalibration task (the audit flagged its dollar magnitudes as
toy-scale relative to every other fixture); Build 3, Day 2, Part 2b
recalibrated its seed data to a realistic magnitude and added its entry
here. Build 3, Day 2, Part 3 (proof-of-concept, issue #79) added the first
ambiguous-scenario entry, AMBIGUOUS_REFUND_TIMING (tests/fixtures/
ambiguous_scenarios.py) -- the second proof-of-concept scenario,
AMBIGUOUS_REVENUE_RECOGNITION, is deliberately NOT added here yet; see
that module's own docstring for why. The remaining 8 ambiguous scenarios
this benchmark's decision-6 split calls for have not been authored."""
