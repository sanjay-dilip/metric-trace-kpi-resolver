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

from typing import Callable

from config import DATA_SAMPLE_DIR
from src.data_quality import check_missing_partition, check_referential_integrity, check_stale_extract
from src.definition_diff import diff_definitions
from src.query_mutation import construct_corrected_query
from src.reconciliation import shapley_pair_attribution, single_cause_attribution
from src.scenario import DashboardSource, Scenario
from src.schema import (
    DataQualityIssue,
    DefinitionDifference,
    InvestigationEvidence,
    ReconciliationLineItem,
    SelfConsistencyIssue,
    SQLStructuralDifference,
)
from src.self_consistency import (
    assemble_definitional_evidence_with_dollar_impacts,
    assemble_structural_and_definitional_evidence,
)
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql

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
    source_a: DashboardSource, source_b: DashboardSource, seed_db_path_a: str, difference: _Difference
) -> ReconciliationLineItem:
    """One non-interacting cause, corrected on source A toward source B's
    value (construct_corrected_query's default target), attributed via
    single_cause_attribution (src/reconciliation.py, Day 5 Task 2, reused
    as-is) and sign-oriented to known_gap's convention.

    `source_b` (Build 3, Day 3, Part 2, new parameter): passed through to
    construct_corrected_query as `other_side_sql` so a `date_field`
    DefinitionDifference correction can adopt source_b's real threshold, not
    just its column name (see construct_corrected_query's own docstring) --
    a no-op for every other difference type/field, which ignore
    other_side_sql entirely."""
    corrected_sql = construct_corrected_query(source_a.sql, difference, other_side_sql=source_b.sql)
    raw_delta = single_cause_attribution(seed_db_path_a, source_a.sql, corrected_sql)
    return ReconciliationLineItem(
        cause=_describe(difference),
        dollar_impact=-raw_delta,
        computed_by="single_cause_attribution",
    )


def _shapley_pair_line_items(
    source_a: DashboardSource,
    source_b: DashboardSource,
    seed_db_path_a: str,
    difference_x: _Difference,
    difference_y: _Difference,
) -> tuple[ReconciliationLineItem, ReconciliationLineItem]:
    """Two interacting causes on source A, both corrected toward source B's
    value, jointly attributed via shapley_pair_attribution (src/reconciliation.py,
    Day 5 Task 2, reused as-is). x_only/y_only/both are constructed by
    chaining construct_corrected_query rather than hand-authored SQL --
    verified during this task to reproduce Case 2's hand-written "both" SQL
    exactly (4.0, matching tests/test_reconciliation.py's committed figure).

    `source_b` (Build 3, Day 3, Part 2, new parameter): same reasoning as
    _single_cause_line_item's own -- passed through as `other_side_sql` to
    every construct_corrected_query call below, a no-op unless the
    difference being corrected is a `date_field` DefinitionDifference."""
    baseline_sql = source_a.sql
    x_only_sql = construct_corrected_query(baseline_sql, difference_x, other_side_sql=source_b.sql)
    y_only_sql = construct_corrected_query(baseline_sql, difference_y, other_side_sql=source_b.sql)
    both_sql = construct_corrected_query(x_only_sql, difference_y, other_side_sql=source_b.sql)

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
        line_items.extend(_shapley_pair_line_items(source_a, source_b, seed_db_path_a, difference_x, difference_y))

    all_cross_source_differences: list[_Difference] = [*definition_differences, *sql_differences]
    for difference in all_cross_source_differences:
        if any(difference == paired for paired in paired_differences):
            continue
        line_items.append(_single_cause_line_item(source_a, source_b, seed_db_path_a, difference))

    for issue in self_consistency_issues:
        line_items.append(_self_consistency_line_item(source_a, source_b, issue))

    return line_items


def _case_08_stale_extract(scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str) -> DataQualityIssue | None:
    complete_db_path_a = str(DATA_SAMPLE_DIR / f"{scenario.freshness_complete_seed_table}_a.duckdb")
    return check_stale_extract(complete_db_path_a, seed_db_path_a, "orders", scenario.source_a.sql, "a")


def _case_09_missing_partition(scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str) -> DataQualityIssue | None:
    complete_db_path_a = str(DATA_SAMPLE_DIR / f"{scenario.freshness_complete_seed_table}_a.duckdb")
    return check_missing_partition(complete_db_path_a, seed_db_path_a, "orders", scenario.source_a.sql, "a")


def _case_10_referential_integrity(
    scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str
) -> DataQualityIssue | None:
    return check_referential_integrity(
        seed_db_path_a, seed_db_path_a, "orders", "customers", "customer_id", "customer_id",
        scenario.source_a.sql, "a",
    )


def _case_11_referential_integrity_source_b(
    scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str
) -> DataQualityIssue | None:
    return check_referential_integrity(
        seed_db_path_b, seed_db_path_b, "orders", "customers", "customer_id", "customer_id",
        scenario.source_b.sql, "b",
    )


def _case_12_join_orphan_collision(
    scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str
) -> DataQualityIssue | None:
    """Build 3, Day 1, Part 2: a new dispatch entry for a new, standalone
    collision-proof fixture (Case 12) -- not a change to any existing
    entry. Case 12 is deliberately excluded from SCENARIOS (see its own
    Scenario docstring, tests/fixtures/scenarios.py) precisely because no
    suppression rule exists yet to reconcile this dispatch's finding
    against sql_diff's join_type finding on the same data; this entry
    exists so the collision can be demonstrated via direct function calls,
    not so this fixture participates in any pipeline run."""
    return check_referential_integrity(
        seed_db_path_a, seed_db_path_a, "orders", "customers", "customer_id", "customer_id",
        scenario.source_a.sql, "a",
    )


def _case_20_stale_extract_join_collision(
    scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str
) -> DataQualityIssue | None:
    """Build 3, Day 3, Part 9 (finalized 8-scenario list, item 7): unlike
    Case 12, this fixture IS added to SCENARIOS and IS dispatched --
    deliberately, per item 7's own stated purpose (the first live
    demonstration of decision 17's legibility risk inside the actual
    benchmark set). Mirrors _case_08_stale_extract's own shape exactly:
    only source_a carries the freshness cause (source_b's data is always
    complete)."""
    complete_db_path_a = str(DATA_SAMPLE_DIR / f"{scenario.freshness_complete_seed_table}_a.duckdb")
    return check_stale_extract(complete_db_path_a, seed_db_path_a, "orders", scenario.source_a.sql, "a")


_DATA_QUALITY_DISPATCH: dict[str, Callable[[Scenario, str, str], DataQualityIssue | None]] = {
    "case_08_stale_extract": _case_08_stale_extract,
    "case_09_missing_partition": _case_09_missing_partition,
    "case_10_referential_integrity": _case_10_referential_integrity,
    "case_11_referential_integrity_source_b": _case_11_referential_integrity_source_b,
    "case_12_join_orphan_collision": _case_12_join_orphan_collision,
    "case_20_stale_extract_join_collision": _case_20_stale_extract_join_collision,
}
"""Build 2, Day 5 dispatch point (decision locked in chat, Option B): only
ONE data-quality check is ever invoked per scenario, chosen by
`scenario.scenario_id` against this fixed, hand-authored lookup table --
not by any runtime inspection of the scenario's data, and not by trying
every check and reconciling collisions between them. `check_stale_extract`
and `check_missing_partition` are mechanism-identical (Day 3,
`_check_completeness`) and provably fire identically on the same
row-count-diff input (the cross-category discrimination test,
test_data_quality.py); there is no way to tell, from a Scenario object
alone, which category label is the "honest" one for a given fixture --
that knowledge exists only in how the fixture's seed data was
constructed, by whoever authored it. Building a general "same underlying
fact" collision detector between these two functions (the way decisions
10/12 built one between sql_diff and definition_diff findings) was
explicitly rejected as disproportionate: those two SQL-diff tools each
detect something structurally different that can *coincidentally* trace
to the same fact; check_stale_extract/check_missing_partition are the
literal same function under two names, so any input that makes one fire
makes the other fire too, always, by construction -- there's no partial
overlap to detect, only a binary choice.

**Named, open limitation, not a solved problem:** this table only covers
the four scenarios this project has authored a data-quality cause for. A
future, unlabeled scenario -- one nobody has hand-classified into this
table -- gets zero data-quality issues from this dispatch (see
_resolve_data_quality_issues below), not an automatic guess at which
check might apply. Build 3's benchmark-authoring pass, which will
introduce new freshness/quality scenarios at 20-30x this project's
current fixture count, MUST decide how new scenarios get correctly
classified before assuming this table scales -- hand-maintaining one
dict entry per scenario does not obviously survive that jump, and no
solution to that is proposed or implied here.
"""


def _resolve_data_quality_issues(scenario: Scenario, seed_db_path_a: str, seed_db_path_b: str) -> list[DataQualityIssue]:
    """Look up scenario.scenario_id in _DATA_QUALITY_DISPATCH and run the
    one check it names, if any. A scenario_id with no entry returns []
    silently -- not an error, and not a guess. This is deliberate: guessing
    which of three mechanism-distinct checks might apply to an
    unclassified scenario would be exactly the kind of silent gap-patching
    this project has repeatedly caught itself doing and stopped doing
    (assemble_investigation_evidence's own 3+-remaining-causes guard,
    below, refuses to guess for the same reason)."""
    check = _DATA_QUALITY_DISPATCH.get(scenario.scenario_id)
    if check is None:
        return []
    issue = check(scenario, seed_db_path_a, seed_db_path_b)
    return [issue] if issue is not None else []


def assemble_investigation_evidence(scenario: Scenario) -> InvestigationEvidence:
    """Build 1, Day 7, Task 2 -- the completion gate for the deterministic
    core. Runs the FULL pipeline for one scenario end to end: sql_diff,
    diff_definitions/check_self_consistency (via
    assemble_definitional_evidence_with_dollar_impacts, Day 7 Part A),
    decision 10 + decision 12's suppression (assemble_structural_and_definitional_evidence),
    the Shapley-pair engine where a genuine interacting pair remains, and
    assemble_reconciliation_line_items -- then computes unexplained_residual
    and returns the complete InvestigationEvidence (src/schema.py), the
    first point in this project where that full schema is populated with
    real, computed data rather than a hand-constructed instance.

    Interacting-pair selection, spelled out precisely rather than left
    implicit: after suppression, whatever DefinitionDifferences and
    SQLStructuralDifferences remain are pooled into one list.
      - 0 remaining: no cross-source line items (pure self-consistency-only
        scenarios like Case 4/7, or pure-residual scenarios like Case 5,
        or the negative control, Case 6).
      - 1 remaining: attributed as a single, non-interacting cause
        (Case 1's lone join_type difference).
      - exactly 2 remaining: treated as one interacting pair and Shapley-
        averaged. This is not a new detection heuristic invented here --
        it is decision 11's own stated rule, applied literally: "For any
        pair of remaining causes ... this applies uniformly to every
        remaining pair, without first attempting to detect whether the
        pair actually overlaps." Every one of the 7 fixtures that reaches
        this branch (Case 2, Case 3) is a definitional-vs-definitional
        pair, the only shape decision 11 confirmed empirically; a
        definitional-vs-structural pair would take this same branch today
        but decision 11's own scope caveat already flags that shape as
        untested, not this function's problem to resolve.
      - 3+ remaining: raises. Averaging across more than two orderings is
        explicitly untested and deferred to Build 3 (decision 11) -- none
        of the 7 current fixtures reach this branch (verified explicitly,
        not assumed), and guessing which sub-pairs interact would be
        exactly the kind of silent gap-patching this project has
        repeatedly caught itself doing and stopped doing.

    seed_db_path_a/seed_db_path_b are resolved from scenario.seed_table
    here (the "_a"/"_b" per-side convention scripts/build_seed_data.py
    established) -- every other function in this module takes seed paths
    as caller-supplied arguments; this is the one place in the pipeline
    that owns resolving them, since it is the one function that owns a
    whole Scenario rather than two bare DashboardSources.

    data_quality_issues (src/schema.py, Build 2, Day 1) is populated as of
    Build 2, Day 5 -- via _resolve_data_quality_issues's fixture-authored
    dispatch table, above -- for the four scenarios this project has a
    known data-quality cause for (Cases 8-11). Every other scenario still
    gets [] (no data-quality check applies, or none is known to).

    **data_quality_issues is ADDITIVE EVIDENCE ONLY (Build 2, Day 5,
    locked decision, Option B -- not full integration). Read this before
    trusting unexplained_residual for any scenario where
    data_quality_issues is non-empty:** data_quality_issues does NOT
    participate in assemble_reconciliation_line_items, the
    Shapley-pair/single-cause attribution machinery, or the
    unexplained_residual calculation below. A DataQualityIssue's own
    dollar_impact field is real and execution-derived (src/data_quality.py),
    but it is never summed into total_dollar_impact and never subtracted
    out of unexplained_residual. Concretely, for Case 8 (a real, found,
    fully-quantified stale_extract cause with dollar_impact == known_gap
    exactly): evidence.reconciliation is [] and evidence.unexplained_residual
    equals known_gap in full, THE SAME AS Case 5's true "no cause exists"
    scenario -- even though Case 8's cause is fully known and fully
    quantified, just not folded into this arithmetic. **For any scenario
    with a non-empty data_quality_issues list, unexplained_residual is NOT
    a meaningful "how much is genuinely unexplained" figure -- it is
    "how much the definitional/structural/self-consistency machinery
    alone did not explain," which is a different, narrower claim.**

    This is deliberate, not an oversight: decision 11 already deferred
    definitional-vs-structural cause interaction as unproven (untested by
    any of the first 7 fixtures); freshness-vs-definitional/structural
    interaction is equally unproven -- no fixture in this project pairs a
    data-quality cause with a definitional or structural one on
    overlapping rows, so there is no evidence folding a DataQualityIssue's
    dollar_impact into the same Shapley/residual math would even be
    correct if attempted. Folding it in anyway would silently assume
    interaction-safety nobody has tested, exactly the mistake Day 7's
    date_field discovery (decision 12) caught and fixed before it shipped
    -- here it is being named and left unresolved instead, since Cases
    8-11 are all single-cause-only fixtures where "fold it in" and "don't"
    happen to be indistinguishable by any test this session could write
    (there is no interacting second cause to get wrong). Build 3's
    benchmark-authoring pass, once it introduces a scenario pairing a
    data-quality cause with a definitional/structural one, is where this
    gets resolved for real -- not assumed away here.
    """
    seed_db_path_a = str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_a.duckdb")
    seed_db_path_b = str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_b.duckdb")

    data_quality_issues = _resolve_data_quality_issues(scenario, seed_db_path_a, seed_db_path_b)

    sql_differences = diff_sql(parse_sql(scenario.source_a.sql), parse_sql(scenario.source_b.sql))
    definition_differences, self_consistency_issues = assemble_definitional_evidence_with_dollar_impacts(
        scenario.source_a, scenario.source_b, seed_db_path_a, seed_db_path_b
    )
    sql_differences, definition_differences = assemble_structural_and_definitional_evidence(
        sql_differences, definition_differences
    )

    remaining_causes: list[_Difference] = [*definition_differences, *sql_differences]
    if len(remaining_causes) > 2:
        raise ValueError(
            f"assemble_investigation_evidence found {len(remaining_causes)} remaining "
            "cross-source causes for scenario "
            f"'{scenario.scenario_id}' after suppression; interaction beyond a single "
            "pair is untested (decision 11, docs/decisions.md) and this function "
            "refuses to guess which sub-pairs interact rather than silently pick one."
        )
    interacting_pairs = [(remaining_causes[0], remaining_causes[1])] if len(remaining_causes) == 2 else None

    line_items = assemble_reconciliation_line_items(
        scenario.source_a,
        scenario.source_b,
        seed_db_path_a,
        sql_differences,
        definition_differences,
        self_consistency_issues,
        interacting_pairs,
    )

    total_dollar_impact = sum(item.dollar_impact for item in line_items)
    unexplained_residual = scenario.known_gap - total_dollar_impact

    return InvestigationEvidence(
        definition_differences=definition_differences,
        self_consistency_issues=self_consistency_issues,
        sql_differences=sql_differences,
        data_quality_issues=data_quality_issues,
        reconciliation=line_items,
        unexplained_residual=unexplained_residual,
    )
