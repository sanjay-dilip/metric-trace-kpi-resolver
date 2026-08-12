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

Day 4 Part 2 adds assemble_definitional_evidence, which orchestrates this
module's check_self_consistency together with src.definition_diff's
diff_definitions and applies the precedence rule documented on
InvestigationEvidence (src/schema.py): a SelfConsistencyIssue on a field
suppresses the matching cross-source DefinitionDifference for that field.
It lives here rather than in src/definition_diff.py to avoid a circular
import -- this module already imports from definition_diff, so the
orchestration layer that depends on both sits above both, in whichever
of the two doesn't need to import the other. No new inference or
comparison logic is added: this is pure orchestration and filtering over
already-verified Day 3 / Day 4 Part 1 functions.

Day 5, Task 1b adds assemble_structural_and_definitional_evidence, which
implements decision 10 (docs/decisions.md): src.sql_diff's `distinct`
category finding is suppressed in favor of src.definition_diff's
`aggregation` field finding when both trace to the same underlying
COUNT/COUNT DISTINCT fact -- a Case 2 audit found decision 10 had been
recorded in the decision log with no enforcing code anywhere, which this
closes. Placed here for the same reason as assemble_definitional_evidence:
"structurally the same kind of precedence rule ... applied to a second
category pair" (decision 10's own wording), and this module only needs
the SQLStructuralDifference *type* from src.schema, not anything from
src.sql_diff itself, so co-locating it here introduces no import cycle.
"""

from typing import Literal

from src.definition_diff import _values_equal, diff_definitions, infer_definition_from_sql
from src.schema import DefinitionDifference, SelfConsistencyIssue, SQLStructuralDifference
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


def assemble_definitional_evidence(
    source_a: DashboardSource, source_b: DashboardSource
) -> tuple[list[DefinitionDifference], list[SelfConsistencyIssue]]:
    """Run check_self_consistency on both sides and diff_definitions across
    them, then apply the precedence rule: a field with a SelfConsistencyIssue
    on either side has its matching cross-source DefinitionDifference removed
    from the returned list, since that cross-source comparison is not
    meaningful once one side's own SQL is known to contradict its own
    declared definition for that field.

    None-handling choice: check_self_consistency raises TypeError on a source
    with no declared_definition (Day 4 Part 1's fail-loud contract), because
    at that function's level a missing declaration is the caller's mistake --
    there is nothing to check. At THIS level, a missing declared_definition
    is not a mistake; it is an expected, valid scenario state (the Day 3
    hybrid-fallback case), so this function checks for None itself and treats
    it as "self-consistency does not apply to this side," never calling
    check_self_consistency on that side rather than catching the exception it
    would raise.

    Returns (definition_differences, self_consistency_issues) -- the pruned
    cross-source list and the self-consistency list, unmodified. This is the
    definitional/self-consistency slice of InvestigationEvidence only:
    sql_differences (Day 2), reconciliation, and unexplained_residual
    (Day 5) are not assembled here and are left to a later top-level
    assembly step.
    """
    self_consistency_issues: list[SelfConsistencyIssue] = []
    if source_a.declared_definition is not None:
        self_consistency_issues += check_self_consistency(source_a, "a")
    if source_b.declared_definition is not None:
        self_consistency_issues += check_self_consistency(source_b, "b")

    cross_source_differences = diff_definitions(source_a, source_b)

    suppressed_fields = {issue.declared_field for issue in self_consistency_issues}
    definition_differences = [
        diff for diff in cross_source_differences if diff.field not in suppressed_fields
    ]

    return definition_differences, self_consistency_issues


_DISTINCT_SUFFIX = "_distinct"


def _same_count_distinct_fact(definition_differences: list[DefinitionDifference]) -> bool:
    """"Same underlying fact" is defined precisely, not just "both categories
    present": an `aggregation` DefinitionDifference qualifies only when its
    two values are the same base aggregation function, differing solely by
    the "_distinct" suffix (e.g. "count_distinct" vs "count") -- exactly the
    shape infer_definition_from_sql (src/definition_diff.py) produces when
    one side is COUNT(DISTINCT col) and the other is COUNT(col), the same
    fact sql_diff's `distinct` category is built to flag. A same-function
    match is required in both directions (either side may be the DISTINCT
    one) so this doesn't accidentally match an unrelated pairing that merely
    happens to contain the substring "distinct"."""
    for diff in definition_differences:
        if diff.field != "aggregation":
            continue
        a, b = diff.source_a_value, diff.source_b_value
        if a.endswith(_DISTINCT_SUFFIX) and a[: -len(_DISTINCT_SUFFIX)] == b:
            return True
        if b.endswith(_DISTINCT_SUFFIX) and b[: -len(_DISTINCT_SUFFIX)] == a:
            return True
    return False


def assemble_structural_and_definitional_evidence(
    sql_differences: list[SQLStructuralDifference],
    definition_differences: list[DefinitionDifference],
) -> tuple[list[SQLStructuralDifference], list[DefinitionDifference]]:
    """Implements decision 10: when a `distinct`-category SQLStructuralDifference
    and an `aggregation`-category DefinitionDifference both exist and trace to
    the same underlying COUNT/COUNT DISTINCT fact (per _same_count_distinct_fact),
    the `distinct` finding is removed from sql_differences -- the DISTINCT
    toggle is a downstream symptom of the aggregation-definition disagreement,
    not an independent cause. definition_differences (including `aggregation`)
    is always returned unmodified; only sql_differences is ever pruned here.

    When the condition does not hold -- either finding absent, or a `distinct`
    finding present without a same-fact `aggregation` counterpart -- both
    lists pass through unchanged. No new inference or comparison logic: this
    is orchestration/filtering only, over already-computed sql_diff and
    definition_diff output, same as assemble_definitional_evidence.
    """
    has_distinct_finding = any(diff.category == "distinct" for diff in sql_differences)
    if has_distinct_finding and _same_count_distinct_fact(definition_differences):
        sql_differences = [diff for diff in sql_differences if diff.category != "distinct"]

    return sql_differences, definition_differences
