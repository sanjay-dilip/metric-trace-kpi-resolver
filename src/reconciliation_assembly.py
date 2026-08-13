"""Build 1, Day 7 Task 1, Part B: assembles every dollar-attributed cause
into a list[ReconciliationLineItem], the final evidence type
InvestigationEvidence.reconciliation (src/schema.py) expects.

Kept as its own module rather than folded into src/reconciliation.py or
src/self_consistency.py: src/reconciliation.py's own docstring states it
has "no dependency in either direction on self_consistency.py,
definition_diff.py, or sql_diff.py's internals" -- this module needs
construct_corrected_query (src/query_mutation.py, which imports
src.sql_diff) and diff_definitions (src/definition_diff.py), so adding it
to reconciliation.py would break that stated invariant. src/self_consistency.py's
charter is specifically self-consistency orchestration, not general
cross-source Shapley/single-cause assembly. This module sits above both,
consuming reconciliation.py's arithmetic primitives (shapley_pair_attribution,
single_cause_attribution) and query_mutation.py's SQL construction, the
same layering self_consistency.py already established for its own
execution-based functions.

Sign convention: every ReconciliationLineItem.dollar_impact is signed per
SelfConsistencyIssue.dollar_impact's documented convention (src/schema.py,
Build 1 Day 6 close-out + Day 7 Part A) -- positive means the cause
currently INFLATES known_gap (reported_value_a - reported_value_b),
negative means it currently SUPPRESSES it. reconciliation.py's own
primitives (shapley_pair_attribution, single_cause_attribution) return the
opposite-oriented raw delta (corrected - baseline, i.e. "the effect of
adopting the other side's value") -- this module negates that raw delta
for every cause expressed as "source A corrected toward source B", the
same negation compute_self_consistency_dollar_impacts already applies for
source="a" self-consistency issues. Neither reconciliation.py's primitives
nor construct_corrected_query are modified to do this themselves; the sign
orientation is this module's job alone, applied consistently across every
cause type it assembles.

Explicit scope boundary, matching decision 11 (docs/decisions.md):
interaction detection between causes (which pairs of differences actually
interact on overlapping rows, vs. which are independent) is NOT built
here. That was deliberately deferred to Build 3's benchmark-authoring pass
when this project had only 6-7 hand-designed scenarios; it still is.
assemble_reconciliation_line_items takes the caller's already-known
interacting pairs as an explicit argument, the same way Day 5 Task 2's
shapley_pair_attribution already required the caller to hand-select which
two SQL variants mattered -- this module mechanizes the SQL construction
for that known pairing (via construct_corrected_query) rather than
requiring hand-authored SQL per pair, but it does not decide which pairs
interact in the first place.

Deterministic only -- no LLM calls anywhere.
"""

from src.definition_diff import diff_definitions
from src.query_mutation import construct_corrected_query
from src.reconciliation import shapley_pair_attribution, single_cause_attribution
from src.scenario import DashboardSource
from src.schema import (
    DefinitionDifference,
    ReconciliationLineItem,
    SelfConsistencyIssue,
    SQLStructuralDifference,
)

_Difference = DefinitionDifference | SQLStructuralDifference


def _describe(difference: _Difference) -> str:
    """A human-readable cause description for ReconciliationLineItem.cause.
    SQLStructuralDifference already carries one (its `description` field,
    written for exactly this purpose in src/sql_diff.py); DefinitionDifference
    has no equivalent field, so one is composed from its parts here."""
    if isinstance(difference, SQLStructuralDifference):
        return difference.description
    return (
        f"{difference.field}: source_a {'declares' if difference.source == 'declared' else 'implies'} "
        f"'{difference.source_a_value}', source_b {'declares' if difference.source == 'declared' else 'implies'} "
        f"'{difference.source_b_value}'"
    )


def _single_cause_line_item(
    source_a: DashboardSource, seed_db_path_a: str, difference: _Difference
) -> ReconciliationLineItem:
    """One non-interacting cause, corrected on source A toward source B's
    value (construct_corrected_query's default target), attributed via
    single_cause_attribution (src/reconciliation.py, Day 5 Task 2, reused
    as-is) and sign-oriented to known_gap's convention."""
    corrected_sql = construct_corrected_query(source_a.sql, difference)
    raw_delta = single_cause_attribution(seed_db_path_a, source_a.sql, corrected_sql)
    return ReconciliationLineItem(
        cause=_describe(difference),
        dollar_impact=-raw_delta,
        computed_by="single_cause_attribution",
    )


def _shapley_pair_line_items(
    source_a: DashboardSource,
    seed_db_path_a: str,
    difference_x: _Difference,
    difference_y: _Difference,
) -> tuple[ReconciliationLineItem, ReconciliationLineItem]:
    """Two interacting causes on source A, both corrected toward source B's
    value, jointly attributed via shapley_pair_attribution (src/reconciliation.py,
    Day 5 Task 2, reused as-is). x_only/y_only/both are constructed by
    chaining construct_corrected_query rather than hand-authored SQL --
    verified during this task to reproduce Case 2's hand-written "both" SQL
    exactly (4.0, matching tests/test_reconciliation.py's committed figure)."""
    baseline_sql = source_a.sql
    x_only_sql = construct_corrected_query(baseline_sql, difference_x)
    y_only_sql = construct_corrected_query(baseline_sql, difference_y)
    both_sql = construct_corrected_query(x_only_sql, difference_y)

    x_attr, y_attr = shapley_pair_attribution(seed_db_path_a, baseline_sql, x_only_sql, y_only_sql, both_sql)

    return (
        ReconciliationLineItem(
            cause=_describe(difference_x), dollar_impact=-x_attr, computed_by="shapley_pair_attribution"
        ),
        ReconciliationLineItem(
            cause=_describe(difference_y), dollar_impact=-y_attr, computed_by="shapley_pair_attribution"
        ),
    )


def _self_consistency_line_item(
    source_a: DashboardSource, source_b: DashboardSource, issue: SelfConsistencyIssue
) -> ReconciliationLineItem:
    """issue.dollar_impact is already correctly signed and, where applicable,
    already folded with its suppressed cross-source counterpart's dollar
    value (assemble_definitional_evidence_with_dollar_impacts,
    src/self_consistency.py, Day 7 Part A) -- no further arithmetic here.
    This function only decides the human-readable cause text and which
    computed_by label applies, by independently re-checking whether
    issue.declared_field had a cross-source difference suppressed on its
    account (diff_definitions's raw output minus the fields that actually
    survived -- the same check Part A performs internally, re-derived here
    rather than threaded through as extra state, since SelfConsistencyIssue
    itself carries no "was this folded" field)."""
    raw_cross_source_fields = {diff.field for diff in diff_definitions(source_a, source_b)}
    was_folded = issue.declared_field in raw_cross_source_fields
    computed_by = "self_consistency_correction+suppressed_cross_source" if was_folded else "self_consistency_correction"

    cause = (
        f"source_{issue.source}'s own SQL implements {issue.declared_field}='{issue.implemented_value}', "
        f"contradicting its own declared '{issue.declared_value}'"
    )
    return ReconciliationLineItem(cause=cause, dollar_impact=issue.dollar_impact, computed_by=computed_by)


def assemble_reconciliation_line_items(
    source_a: DashboardSource,
    source_b: DashboardSource,
    seed_db_path_a: str,
    sql_differences: list[SQLStructuralDifference],
    definition_differences: list[DefinitionDifference],
    self_consistency_issues: list[SelfConsistencyIssue],
    interacting_pairs: list[tuple[_Difference, _Difference]] | None = None,
) -> list[ReconciliationLineItem]:
    """Assemble every dollar-attributed cause -- Shapley-averaged
    interacting pairs, self-consistency issues (with Part A's folded
    dollar impacts), and clean single-cause differences with no known
    interaction -- into a list[ReconciliationLineItem].

    `definition_differences` and `sql_differences` must already be the
    pruned/suppressed evidence (assemble_definitional_evidence's and
    assemble_structural_and_definitional_evidence's output, respectively)
    -- this function does not re-run precedence suppression or decision
    10's distinct-vs-aggregation suppression itself. `self_consistency_issues`
    must already carry correctly-signed, folded dollar_impact values
    (assemble_definitional_evidence_with_dollar_impacts's output) -- this
    function does no self-consistency arithmetic of its own.

    `interacting_pairs` names which differences (drawn from
    definition_differences and/or sql_differences) are known to interact
    and must be jointly Shapley-attributed rather than independently
    single-cause-attributed -- see this module's docstring for why that
    detection is the caller's job, not this function's (decision 11,
    interaction-detection deferred to Build 3). Every difference named in
    a pair is excluded from the single-cause pass below; every difference
    NOT named in any pair is attributed independently.

    Only source A's SQL is corrected toward source B's declared/inferred
    values for cross-source causes here (construct_corrected_query's
    default direction, matching every counterfactual this project has
    built so far -- Day 5 Task 2, Day 6 Task 1's validation, Day 7 Part A).
    seed_db_path_a is the only seed path this function needs as a result.
    """
    interacting_pairs = interacting_pairs or []
    paired_differences: list[_Difference] = [d for pair in interacting_pairs for d in pair]

    line_items: list[ReconciliationLineItem] = []

    for difference_x, difference_y in interacting_pairs:
        line_items.extend(_shapley_pair_line_items(source_a, seed_db_path_a, difference_x, difference_y))

    all_cross_source_differences: list[_Difference] = [*definition_differences, *sql_differences]
    for difference in all_cross_source_differences:
        if any(difference == paired for paired in paired_differences):
            continue
        line_items.append(_single_cause_line_item(source_a, seed_db_path_a, difference))

    for issue in self_consistency_issues:
        line_items.append(_self_consistency_line_item(source_a, source_b, issue))

    return line_items
