"""Pydantic schema for investigation evidence. Every tool and agent boundary
produces or consumes these types; no free-form dict or string output crosses
a tool/agent boundary in this pipeline."""

from typing import Literal

from pydantic import BaseModel


class DefinitionDifference(BaseModel):
    """A metric-definition mismatch between source A and source B, declared
    or inferred from SQL when no declared config exists on one or both sides."""

    field: str
    source_a_value: str
    source_b_value: str
    source: Literal["declared", "inferred"]
    confidence: Literal["high", "medium", "low"]


class SelfConsistencyIssue(BaseModel):
    """A source's own SQL implementation diverges from that same source's
    declared metric definition. Takes precedence over any DefinitionDifference
    on the same field: if a source's SQL doesn't even match its own declared
    definition, the cross-source comparison for that field is not meaningful
    until this is resolved.

    dollar_impact sign convention (Build 1, Day 6 close-out), matching
    Scenario.known_gap's existing convention (known_gap = reported_value_a
    minus reported_value_b, positive means A reports higher than B): a
    positive dollar_impact means this cause is currently INFLATING
    known_gap -- fixing it moves the observed A-vs-B gap toward zero (or
    negative). A negative dollar_impact means this cause is currently
    SUPPRESSING/offsetting known_gap -- fixing it moves the gap further
    from zero in the positive direction. This is the sign, not just the
    magnitude, that Day 7's reconciliation sums every cause's dollar_impact
    against known_gap to check.

    Concretely: let `original` be the source's as-written SQL executed
    against its own seed data, and `corrected` be the same source's SQL if
    it were fixed to match its own declared definition (construct_corrected_query,
    src/query_mutation.py; execution via single_cause_attribution,
    src/reconciliation.py). For a source="a" issue: dollar_impact = original
    - corrected (reducing A's own inflated value reduces known_gap by
    exactly that amount, since known_gap subtracts B, which A-side issues
    don't touch). For a source="b" issue the sign flips: dollar_impact =
    corrected - original (known_gap subtracts B, so an increase in B's
    value reduces known_gap -- the opposite direction of the same
    correction applied to A).

    The same convention applies to ReconciliationLineItem.dollar_impact
    once Day 7 populates it from cross-source causes too, so every cause's
    dollar_impact can be summed directly against known_gap without a
    separate sign-flip step per cause type.

    Suppressed-cause folding (Build 1, Day 7 Task 1, Part A):
    assemble_definitional_evidence's precedence rule removes a cross-source
    DefinitionDifference from definition_differences when a
    SelfConsistencyIssue exists on the same field, since reporting both
    would double-report the same underlying fact. That removal is a
    REPORTING decision, not a dollar-value decision -- the suppressed
    difference's dollar contribution is still real and must not vanish.
    When a SelfConsistencyIssue's field had a cross-source difference
    suppressed on its account, dollar_impact (as returned by
    assemble_definitional_evidence_with_dollar_impacts,
    src/self_consistency.py) is the SUM of two signed components, both
    computed under the exact convention above: the self-consistency
    correction itself, plus the further correction from "this source's own
    declared value" to "the other source's declared value" on that same
    field. A reader must not assume dollar_impact here reflects only "this
    source's own SQL bug" -- it may also carry a real cross-source
    definitional difference that was structurally suppressed from
    definition_differences to avoid double-reporting it, not to discard its
    dollar value."""

    source: Literal["a", "b"]
    declared_field: str
    declared_value: str
    implemented_value: str
    confidence: Literal["high", "medium", "low"]
    dollar_impact: float


class SQLStructuralDifference(BaseModel):
    """A structural difference between the two queries' SQL (joins, filters,
    date handling, etc.) that is independent of metric-definition semantics."""

    category: Literal[
        "join_type", "filter", "date_field", "aggregation", "distinct", "grouping", "other"
    ]
    description: str
    query_a_snippet: str
    query_b_snippet: str


class DataQualityIssue(BaseModel):
    """A data-freshness or completeness defect on one source (stale extract,
    missing partition, late-arriving data, or a referential-integrity gap)
    that accounts for some or all of the observed KPI discrepancy,
    independent of any metric-definition or SQL-structural difference.
    Build 2, Day 1: schema only -- no detection tool populates this yet.

    dollar_impact will follow the same sign convention as
    SelfConsistencyIssue.dollar_impact once Build 2's freshness_attribution
    function exists to compute it (Day 2+, not built yet): for a
    source="a" issue, a positive value means the stale/incomplete data is
    currently INFLATING known_gap (running the same query against the
    complete snapshot would move the gap toward zero); the sign flips for
    a source="b" issue, mirroring SelfConsistencyIssue's own convention
    exactly (src/schema.py, above) so every cause type sums against
    known_gap the same way. Cross-category interaction with
    DefinitionDifference/SQLStructuralDifference on the same fixture's
    overlapping rows is untested and explicitly out of scope for Build 2
    -- deferred to Build 3, mirroring decision 11's own stated scope
    limit (docs/decisions.md)."""

    category: Literal["stale_extract", "missing_partition", "late_arriving_data", "referential_integrity"]
    source: Literal["a", "b"]
    description: str
    confidence: Literal["high", "medium", "low"]
    dollar_impact: float


class ReconciliationLineItem(BaseModel):
    """One confirmed cause's contribution to the total dollar gap, with the
    computation that produced it named for traceability back to a specific
    deterministic tool rather than an LLM claim."""

    cause: str
    dollar_impact: float
    computed_by: str


class InvestigationEvidence(BaseModel):
    """The complete evidence bundle for one discrepancy investigation, passed
    to the LLM explainer as its only source of truth. Precedence rule: when a
    SelfConsistencyIssue exists for a field, the corresponding cross-source
    DefinitionDifference for that same field is suppressed from
    definition_differences, not double-counted alongside it in reconciliation.
    data_quality_issues (Build 2, Day 1) is not yet populated by any
    assembly function -- reconciliation_assembly.py's assemble_investigation_evidence
    still only fills the other four fields; wiring a freshness pre-check
    into that function is Build 2, Day 2+ work, not this one.

    Decision 17 (docs/decisions.md): a data_quality_issues entry's
    dollar_impact may restate a cause already counted in reconciliation
    (confirmed concretely on Case 12 -- a join_type correction and a
    referential-integrity orphan finding both quantify the same $300
    correction independently) -- never sum a data_quality_issues figure
    against reconciliation's total, in a report, a reader's own math, or a
    future explainer prompt."""

    definition_differences: list[DefinitionDifference]
    self_consistency_issues: list[SelfConsistencyIssue]
    sql_differences: list[SQLStructuralDifference]
    data_quality_issues: list[DataQualityIssue]
    reconciliation: list[ReconciliationLineItem]
    unexplained_residual: float
