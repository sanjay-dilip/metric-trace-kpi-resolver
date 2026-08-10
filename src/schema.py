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
    until this is resolved."""

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
    definition_differences, not double-counted alongside it in reconciliation."""

    definition_differences: list[DefinitionDifference]
    self_consistency_issues: list[SelfConsistencyIssue]
    sql_differences: list[SQLStructuralDifference]
    reconciliation: list[ReconciliationLineItem]
    unexplained_residual: float
