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
    dollar_impact with a real, execution-based, SIGNED number (Build 1, Day
    6 close-out -- supersedes the Day 6 Task 2 unsigned-magnitude version).
    For each issue found: construct the corrected SQL that would result if
    source.sql were fixed to match source's own declared definition
    (construct_corrected_query, src/query_mutation.py -- Day 6 Task 1),
    execute both source.sql and the corrected SQL against seed_db_path, and
    sign the delta per the convention documented on
    SelfConsistencyIssue.dollar_impact (src/schema.py) -- no new arithmetic:
    single_cause_attribution (src/reconciliation.py, Day 5 Task 2) still
    supplies the raw (corrected - original) delta; this function only
    orients its sign relative to known_gap = reported_value_a -
    reported_value_b.

    Sign derivation, restated concretely (full reasoning lives on
    SelfConsistencyIssue.dollar_impact): single_cause_attribution returns
    (corrected - original). For a source-"a" issue, dollar_impact is the
    NEGATION of that raw delta -- (original - corrected) -- because
    reducing A's own value (correcting an inflation) reduces known_gap by
    exactly that amount. For a source-"b" issue, dollar_impact is the raw
    delta AS-IS -- (corrected - original) -- because known_gap subtracts B,
    so increasing B's value (correcting an inflation on B's side) reduces
    known_gap the same way, with the opposite sign relative to raw.

    seed_db_path is the caller's responsibility to resolve (e.g. from
    Scenario.seed_table + side, per scripts/build_seed_data.py's "_a"/"_b"
    per-side file convention) -- this function only executes SQL against
    whatever path it is given, same as single_cause_attribution itself.

    Directional sanity-check finding, worth recording honestly rather than
    smoothing over: Case 4's dollar_impact (+200.0) has the same sign as
    Case 4's known_gap (+11500.0), so summing it alone shrinks the
    known-gap-vs-explained-sum distance (11500.0 -> 11300.0), matching the
    naive intuition that "this cause explains part of the gap." Case 7's
    dollar_impact (-100.0) does NOT share known_gap's sign (+200.0), so
    summing it alone GROWS that distance (200.0 -> 300.0) rather than
    shrinking it. This is not a convention error: Case 7's self-consistency
    bug genuinely suppresses the observed gap below what it would be if
    fixed in isolation (A's as-written 300.0 vs A's own-declaration-corrected
    400.0 -- correcting only this cause would widen the A-vs-B gap from
    200.0 to 300.0, not narrow it). Nothing else in Case 7's evidence set
    currently explains the remaining difference (the cross-source
    excluded_statuses DefinitionDifference is precedence-suppressed in
    favor of this very issue), so a correct Day 7 accounting would need
    unexplained_residual = 300.0 here (200.0 = -100.0 + 300.0), which is
    algebraically valid even though it exceeds known_gap's own magnitude --
    a signed decomposition permits that whenever some cause's contribution
    opposes the net direction of the others.
    """
    issues = check_self_consistency(source, side)
    resolved_issues = []
    for issue in issues:
        corrected_sql = construct_corrected_query(source.sql, issue)
        raw_delta = single_cause_attribution(seed_db_path, source.sql, corrected_sql)
        impact = -raw_delta if issue.source == "a" else raw_delta
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


def assemble_definitional_evidence_with_dollar_impacts(
    source_a: DashboardSource,
    source_b: DashboardSource,
    seed_db_path_a: str,
    seed_db_path_b: str,
) -> tuple[list[DefinitionDifference], list[SelfConsistencyIssue]]:
    """Build 1, Day 7 Task 1, Part A. Layers execution-based dollar-impact
    computation on top of assemble_definitional_evidence, WITHOUT changing
    that function's own signature or behavior (kept structural/pure so every
    existing caller and test of assemble_definitional_evidence is
    unaffected) -- "the surrounding orchestration" this task's brief refers
    to, rather than a modification to assemble_definitional_evidence itself.

    The gap this closes: assemble_definitional_evidence's precedence rule
    correctly suppresses a cross-source DefinitionDifference when a
    SelfConsistencyIssue exists on the same field, but the suppressed
    finding's dollar value was previously discarded entirely -- confirmed
    concretely on Case 7 (Build 1, Day 6 close-out investigation): the
    suppressed excluded_statuses cross-source difference is worth a real,
    execution-verified 300.0, not "unexplained."

    For each SelfConsistencyIssue whose field was suppressed here (i.e. the
    field appears in diff_definitions's raw output but NOT in this
    function's returned definition_differences), this folds the suppressed
    difference's dollar contribution into that SAME SelfConsistencyIssue's
    dollar_impact, rather than discarding it:

      1. Compute the self-consistency dollar_impact as before
         (compute_self_consistency_dollar_impacts, Day 6 close-out): fixes
         the source's own SQL to match its own declared definition.
      2. From THAT already-corrected SQL, apply the suppressed
         DefinitionDifference itself (construct_corrected_query again,
         chained) to reach the OTHER side's declared value for the same
         field -- target_value is source_b_value when the issue is on side
         "a", source_a_value when the issue is on side "b" (the "other
         side" from this issue's own side, not construct_corrected_query's
         default assumption).
      3. Sign the resulting delta with the SAME per-side convention as step
         1 (SelfConsistencyIssue.dollar_impact docstring, src/schema.py):
         negate the raw (corrected - original) delta for a source="a"
         issue, use it as-is for source="b". This is NOT a different rule
         invented for the suppressed piece -- it is the identical
         known_gap-relative convention applied to a second, chained
         correction step, which is exactly why simply ADDING the two signed
         pieces together is valid: both terms already point in known_gap's
         direction before they are summed.
      4. Add that signed suppressed-cause delta onto the self-consistency
         dollar_impact from step 1.

    Concretely, on Case 7: self-consistency alone gives -100.0 (A's own SQL
    vs. A's own declaration). The suppressed cross-source piece (A's own
    declaration vs. B's declaration, applied to the already-self-consistency-
    corrected SQL) gives +300.0. Combined: -100.0 + 300.0 = +200.0 -- which
    equals Case 7's known_gap exactly, because excluded_statuses is the
    ONLY differing field anywhere in Case 7's evidence (confirmed: no other
    DefinitionDifference, SQLStructuralDifference, or SelfConsistencyIssue
    exists for this scenario), so this one field alone must and does fully
    explain the entire gap.

    Consequence worth stating plainly: a SelfConsistencyIssue.dollar_impact
    returned by THIS function may represent the sum of a self-consistency
    correction and whatever cross-source difference it suppressed on the
    same field -- not the self-consistency correction alone (that narrower
    meaning is what compute_self_consistency_dollar_impacts, called without
    this wrapper, still returns). A reader consuming this function's output
    should not assume dollar_impact reflects only "this source's own SQL
    bug"; it may also carry a real cross-source definitional difference
    that got structurally suppressed from definition_differences to avoid
    reporting it twice.

    seed_db_path_a/seed_db_path_b are the caller's responsibility to
    resolve, same as compute_self_consistency_dollar_impacts.
    """
    definition_differences, _ = assemble_definitional_evidence(source_a, source_b)
    raw_cross_source_differences = diff_definitions(source_a, source_b)
    surviving_fields = {diff.field for diff in definition_differences}
    suppressed_by_field = {
        diff.field: diff for diff in raw_cross_source_differences if diff.field not in surviving_fields
    }

    resolved_issues: list[SelfConsistencyIssue] = []
    for side, source, seed_db_path in (("a", source_a, seed_db_path_a), ("b", source_b, seed_db_path_b)):
        if source.declared_definition is None:
            continue
        for issue in compute_self_consistency_dollar_impacts(source, side, seed_db_path):
            suppressed = suppressed_by_field.get(issue.declared_field)
            if suppressed is None:
                resolved_issues.append(issue)
                continue

            self_consistency_corrected_sql = construct_corrected_query(source.sql, issue)
            other_side_target = suppressed.source_b_value if side == "a" else suppressed.source_a_value
            cross_source_corrected_sql = construct_corrected_query(
                self_consistency_corrected_sql, suppressed, target_value=other_side_target
            )
            raw_delta = single_cause_attribution(
                seed_db_path, self_consistency_corrected_sql, cross_source_corrected_sql
            )
            suppressed_impact = -raw_delta if side == "a" else raw_delta

            combined_impact = issue.dollar_impact + suppressed_impact
            resolved_issues.append(issue.model_copy(update={"dollar_impact": combined_impact}))

    return definition_differences, resolved_issues


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
