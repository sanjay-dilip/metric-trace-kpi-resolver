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

check_self_consistency itself still never computes dollar_impact -- every
issue it builds uses dollar_impact=0.0 as an explicit placeholder, since it
is a purely structural/symbolic comparison (declared value vs.
inferred-from-SQL value) with no SQL execution. Real dollar-impact
computation is compute_self_consistency_dollar_impacts's job (Day 6, Task
2, added below) -- a separate, execution-based function, not a change to
check_self_consistency's own contract.

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
from src.query_mutation import construct_corrected_query
from src.reconciliation import single_cause_attribution
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


def compute_self_consistency_dollar_impacts(
    source: DashboardSource, side: Literal["a", "b"], seed_db_path: str
) -> list[SelfConsistencyIssue]:
    """Run check_self_consistency, then replace each issue's placeholder
    dollar_impact with a real, execution-based number (Build 1, Day 6, Task
    2). For each issue found: construct the corrected SQL that would result
    if source.sql were fixed to match source's own declared definition
    (construct_corrected_query, src/query_mutation.py -- Day 6 Task 1),
    execute both source.sql and the corrected SQL against seed_db_path, and
    take the magnitude of the difference (single_cause_attribution,
    src/reconciliation.py -- Day 5 Task 2; no new arithmetic or SQL-variant
    construction is added here, both are reused as-is).

    dollar_impact is stored as an UNSIGNED magnitude
    (abs(single_cause_attribution(...))), not a signed reconciliation
    contribution. Reasoning: this project's own prior real-execution
    verification (Day 4 close-out, recorded in CONTEXT.md) describes both
    Case 4 and Case 7's self-consistency effects as a plain dollar "gap"
    (200.0 and 100.0 respectively) with no asserted sign convention, and a
    literal signed corrected-minus-original computation produces a NEGATIVE
    200.0 for Case 4 while producing a positive 100.0 for Case 7 -- i.e. the
    two cases' signs would not agree under either fixed signed convention,
    only the magnitude does. Deciding how a self-consistency issue's dollar
    effect should be signed within a ReconciliationLineItem relative to
    known_gap's sign convention (reported_value_a - reported_value_b) is
    explicitly Day 7's job, not resolved here -- this function only proves
    the magnitude is real and mechanically reproducible, matching the
    already-verified 200.0/100.0 figures.

    seed_db_path is the caller's responsibility to resolve (e.g. from
    Scenario.seed_table + side, per scripts/build_seed_data.py's "_a"/"_b"
    per-side file convention) -- this function only executes SQL against
    whatever path it is given, same as single_cause_attribution itself.
    """
    issues = check_self_consistency(source, side)
    resolved_issues = []
    for issue in issues:
        corrected_sql = construct_corrected_query(source.sql, issue)
        impact = abs(single_cause_attribution(seed_db_path, source.sql, corrected_sql))
        resolved_issues.append(issue.model_copy(update={"dollar_impact": impact}))
    return resolved_issues


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
