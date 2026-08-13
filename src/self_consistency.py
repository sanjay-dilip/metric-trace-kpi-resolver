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

import sqlglot
from sqlglot import exp

from src.definition_diff import _values_equal, diff_definitions, infer_definition_from_sql
from src.query_mutation import construct_corrected_query
from src.reconciliation import single_cause_attribution
from src.schema import DefinitionDifference, SelfConsistencyIssue, SQLStructuralDifference
from src.scenario import DashboardSource
from src.sql_diff import _bare_column
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
    Case 4's known_gap (+200.0, recalibrated to real seed execution by
    Decision 13's resolution -- Build 1, Day 7, Task 3; it was a
    mismatched-scale +11500.0 when this paragraph was first written), so
    summing it alone fully closes the known-gap-vs-explained-sum distance
    (200.0 -> 0.0), matching the naive intuition that "this cause explains
    the gap" exactly, not just partially. Case 7's
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


def _bare_date_column_from_snippet(snippet: str) -> str | None:
    """Extract the single bare column name referenced in a date_field
    SQLStructuralDifference snippet (e.g. "order_date >= '2024-01-01'"),
    parsed via sqlglot rather than string matching, for the same reliability
    reasons every other tool in this codebase parses instead of pattern-
    matches raw SQL text.

    Returns None -- deliberately, not a best guess -- when the snippet
    doesn't parse (e.g. the "(no date filter)" placeholder), or when it
    references anything other than EXACTLY one distinct bare column.
    _diff_date_fields (src/sql_diff.py) joins multiple date columns per side
    into one snippet with "; " when more than one exists on that side (a
    genuinely ambiguous structural finding); sqlglot.condition() parses that
    joined text without raising, and find_all(exp.Column) walks across both
    halves, so a naive "take the first column found" would silently match
    only part of an ambiguous finding. Requiring exactly one distinct column
    is what makes the difference between the real Case 3 collision (one
    column each side, matches) and the over-fire case Task 1b constructs
    (two columns on one side, refuses to match) correct rather than
    accidental."""
    try:
        condition = sqlglot.condition(snippet)
    except Exception:
        return None
    columns = {_bare_column(column.sql()) for column in condition.find_all(exp.Column)}
    if len(columns) != 1:
        return None
    return next(iter(columns))


def _same_date_field_fact(
    sql_difference: SQLStructuralDifference, definition_difference: DefinitionDifference
) -> bool:
    """"Same underlying fact" for the date_field pairing (decision 12,
    docs/decisions.md), defined with the same precision standard as
    _same_count_distinct_fact: not merely "both categories present," but a
    side-matched exact equality between the two findings' own referenced
    columns -- sql_difference's query_a_snippet/query_b_snippet (parsed via
    _bare_date_column_from_snippet) must equal definition_difference's
    source_a_value/source_b_value (bare-compared) on the SAME side, not
    just overlap as an unordered set. Side-matching, not set-equality,
    is what correctly refuses a hypothetical swapped-pairing coincidence
    (source_a's structural column equalling source_b's declared value, or
    vice versa) that would technically overlap as a set without actually
    describing the same fact."""
    structural_a = _bare_date_column_from_snippet(sql_difference.query_a_snippet)
    structural_b = _bare_date_column_from_snippet(sql_difference.query_b_snippet)
    if structural_a is None or structural_b is None:
        return False
    definitional_a = _bare_column(definition_difference.source_a_value)
    definitional_b = _bare_column(definition_difference.source_b_value)
    return structural_a == definitional_a and structural_b == definitional_b


def assemble_structural_and_definitional_evidence(
    sql_differences: list[SQLStructuralDifference],
    definition_differences: list[DefinitionDifference],
) -> tuple[list[SQLStructuralDifference], list[DefinitionDifference]]:
    """Implements decision 10 AND decision 12 (docs/decisions.md) -- the same
    class of gap (an sql_diff structural finding and a definition_diff
    definitional finding both tracing to one underlying fact) discovered
    twice now, resolved by the same shape of rule each time: when a
    `distinct`-category SQLStructuralDifference and an `aggregation`-category
    DefinitionDifference trace to the same COUNT/COUNT DISTINCT fact (decision
    10, per _same_count_distinct_fact), OR a `date_field`-category
    SQLStructuralDifference and a `date_field`-category DefinitionDifference
    trace to the same date-column swap (decision 12, per
    _same_date_field_fact), the SQLStructuralDifference is removed from
    sql_differences -- the mechanical/structural finding is a downstream
    restatement of the business-meaning definitional finding, not an
    independent cause. definition_differences is always returned unmodified;
    only sql_differences is ever pruned here.

    Decision 12's own dollar-value note (see docs/decisions.md for the full
    reasoning, and the Case 7 comparison this was checked against): unlike
    Day 7 Part A's suppressed-cross-source folding (a genuinely SEPARATE,
    SEQUENTIAL correction whose dollar value would otherwise vanish),
    date_field's two colliding findings describe the SAME single SQL
    mutation (swap the date column) when they match -- there is no second,
    additional dollar-bearing correction to fold in. The surviving
    DefinitionDifference's own downstream dollar computation (construct_corrected_query
    + single_cause_attribution/shapley_pair_attribution, in
    src/reconciliation_assembly.py) already fully captures the entire
    effect once the redundant structural finding is out of the way -- this
    function suppresses that redundant finding, no arithmetic beyond that
    suppression is required or performed here.

    When neither condition holds -- either finding of a pair absent, or a
    structural finding present without a same-fact definitional counterpart
    -- both lists pass through unchanged for that pairing. No new inference
    or comparison logic beyond the two "same fact" helpers: this is
    orchestration/filtering only, over already-computed sql_diff and
    definition_diff output, same as assemble_definitional_evidence.
    """
    has_distinct_finding = any(diff.category == "distinct" for diff in sql_differences)
    if has_distinct_finding and _same_count_distinct_fact(definition_differences):
        sql_differences = [diff for diff in sql_differences if diff.category != "distinct"]

    date_field_structural = next((diff for diff in sql_differences if diff.category == "date_field"), None)
    date_field_definitional = next((diff for diff in definition_differences if diff.field == "date_field"), None)
    if (
        date_field_structural is not None
        and date_field_definitional is not None
        and _same_date_field_fact(date_field_structural, date_field_definitional)
    ):
        sql_differences = [diff for diff in sql_differences if diff.category != "date_field"]

    return sql_differences, definition_differences
