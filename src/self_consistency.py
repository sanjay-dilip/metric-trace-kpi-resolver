"""Deterministic self-consistency check: does a single source's own SQL
implement what that same source declares its metric definition to be?
This is a one-source comparison (Day 4 Part 1), distinct from every prior
Build 1 tool, which all compared two sources against each other.

Kept as its own module rather than folded into src/definition_diff.py: that
module's docstring already states self-consistency is explicitly out of its
scope ("No self-consistency checks (that is Day 4's job)"), and the intended
consumer of this check (Day 4 Part 2's precedence-suppression / evidence
assembly) sits above both definition_diff and this module, not inside either.
Reuses infer_definition_from_sql and _values_equal from src.definition_diff
directly rather than duplicating inference or comparison logic.

No LLM calls anywhere: this is rule-based comparison against already-inferred
SQL structure, same as Day 3 Part 2.

dollar_impact is NOT computed here. SelfConsistencyIssue requires it as a
non-optional field, so every issue built here uses dollar_impact=0.0 as an
explicit placeholder -- actual dollar-impact computation is Day 5's
reconciliation math, not this module's job.
"""

from typing import Literal

from src.definition_diff import _values_equal, infer_definition_from_sql
from src.schema import SelfConsistencyIssue
from src.scenario import DashboardSource
from src.sql_parser import parse_sql

_DOLLAR_IMPACT_PLACEHOLDER = 0.0


def check_self_consistency(
    source: DashboardSource, side: Literal["a", "b"]
) -> list[SelfConsistencyIssue]:
    """Compare `source`'s declared definition against what `source.sql`
    actually implements (via infer_definition_from_sql), field by field, and
    return one SelfConsistencyIssue per field where they disagree.

    `side` records which side of the dispute `source` is ("a" or "b") so the
    resulting SelfConsistencyIssue.source is correct -- it cannot be derived
    from `source` alone, since DashboardSource has no notion of which side of
    a Scenario it occupies.

    source.declared_definition must not be None: with no declared definition
    there is nothing to check the SQL against, so this function raises a
    clear TypeError rather than silently returning [], matching the fail-loud
    discipline of diff_declared_definitions (src/definition_diff.py).
    """
    if source.declared_definition is None:
        raise TypeError(
            "check_self_consistency requires source.declared_definition to be "
            "present; a source with no declared definition has nothing to "
            "check its SQL against and must not be passed here."
        )

    declared = source.declared_definition
    implemented = infer_definition_from_sql(parse_sql(source.sql))

    issues = []
    for field in ("date_field", "excluded_statuses", "aggregation"):
        declared_value = getattr(declared, field)
        if field == "excluded_statuses":
            declared_value = ", ".join(sorted(set(declared_value))) or "(none)"

        implemented_field = getattr(implemented, field)

        if _values_equal(field, declared_value, implemented_field.value):
            continue

        issues.append(
            SelfConsistencyIssue(
                source=side,
                declared_field=field,
                declared_value=declared_value,
                implemented_value=implemented_field.value,
                confidence=implemented_field.confidence,
                dollar_impact=_DOLLAR_IMPACT_PLACEHOLDER,
            )
        )
    return issues
