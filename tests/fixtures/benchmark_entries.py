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

    notes: str | None = None
    """One-sentence scorer-relevant caveat that doesn't fit the two fields
    above -- e.g. a correct answer split across multiple reconciliation line
    items, or a correct answer that only appears once suppression has moved
    it into a different evidence field. None when no caveat applies."""
