"""Ambiguity-aware benchmark wrapper (Build 3, Day 2, Part 4, A3-ii; return
type corrected in Part 5) around
src.reconciliation_assembly.assemble_investigation_evidence.

Placement, a real design call worth stating rather than leaving silent:
this module lives under tests/fixtures/, not src/reconciliation_assembly.py
(one of the two locations this task's brief offered), because its
signature takes a BenchmarkEntry (tests/fixtures/benchmark_entries.py)
directly. BenchmarkEntry's own docstring already states it is "not
consumed by any src/ code" -- putting this wrapper in src/ would require
src/reconciliation_assembly.py to import from tests/fixtures/, a
backwards dependency (production code depending on test-only fixtures)
that would silently break that stated invariant. Importing
assemble_investigation_evidence (and the module-private
_resolve_data_quality_issues helper, reused rather than duplicated) from
src.reconciliation_assembly here, the ordinary tests-depend-on-src
direction, has no such problem.

assemble_investigation_evidence and Scenario are NOT modified by this
task and are not touched by this module -- they keep raising on 3+
remaining cross-source causes exactly as before. This wrapper only adds
an ambiguity-aware catch on top, for benchmark scoring purposes.

Part 5 correction: Part 4 widened InvestigationEvidence.unexplained_residual
to float | None in src/schema.py to carry this wrapper's escalated-path
output. That was reverted -- InvestigationEvidence is production schema
consumed by src/explainer.py and every other assembly caller, and widening
it to accommodate a benchmark-only outcome leaked a benchmark concern into
production schema. PartialInvestigationEvidence below is the correct fix:
a standalone benchmark-only type, structurally independent from
InvestigationEvidence (not a subclass -- the same wrap-don't-inherit choice
BenchmarkEntry already made for Scenario), so InvestigationEvidence itself
never needs to accommodate an outcome only this wrapper produces."""

from pydantic import BaseModel

from config import DATA_SAMPLE_DIR
from src.reconciliation_assembly import (
    _resolve_data_quality_issues,
    assemble_investigation_evidence,
)
from src.schema import (
    CorrectionEscalation,
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
from tests.fixtures.benchmark_entries import BenchmarkEntry

_THREE_PLUS_CAUSES_MARKER = "remaining cross-source causes for scenario"
"""A substring unique to assemble_investigation_evidence's own 3+-causes
ValueError message (verified unique against every other raise site in
src/, including query_mutation.py's several unrelated ValueErrors) --
the only way to identify that specific failure from outside the function,
since assemble_investigation_evidence is not modified to raise a distinct
exception type for it. This string match is a real, accepted fragility:
if that message's wording ever changes, this constant must change with
it. Documented here rather than left as a silent trap."""


class PartialInvestigationEvidence(BaseModel):
    """Benchmark-only evidence bundle for a genuinely ambiguous scenario
    whose interacting causes exceed the 2-cause Shapley pairing this
    project's reconciliation engine supports -- returned only by
    assemble_investigation_evidence_for_benchmark's escalated path, below.
    Deliberately NOT a subclass of InvestigationEvidence (src/schema.py):
    structural independence, not inheritance, matching BenchmarkEntry's own
    choice to wrap Scenario rather than extend it. Every field name and
    type matches InvestigationEvidence exactly except reconciliation
    (defaults to [], since it is never computed on this path) and
    unexplained_residual (float | None, defaults to None -- None states
    honestly that no residual was computed, never a fabricated 0.0).

    escalations (Build 3, Day 2 cleanup, Part 2): added for the same
    exact-field-match reason as every other field here -- InvestigationEvidence
    gained this field in decision 40's own verification pass, and this
    type's own docstring commits to mirroring it. Defaults to [] so
    existing PartialInvestigationEvidence(...) constructions that predate
    this field are unaffected."""

    definition_differences: list[DefinitionDifference]
    self_consistency_issues: list[SelfConsistencyIssue]
    sql_differences: list[SQLStructuralDifference]
    data_quality_issues: list[DataQualityIssue]
    escalations: list[CorrectionEscalation] = []
    reconciliation: list[ReconciliationLineItem] = []
    unexplained_residual: float | None = None


def assemble_investigation_evidence_for_benchmark(
    entry: BenchmarkEntry,
) -> InvestigationEvidence | PartialInvestigationEvidence:
    """Benchmark-only wrapper: calls assemble_investigation_evidence(entry.scenario)
    normally, returning its real InvestigationEvidence unchanged on
    success. If that call raises the specific 3+-remaining-causes
    ValueError AND entry.is_ambiguous is True, catches it and returns a
    PartialInvestigationEvidence instead, built from the individual
    findings (definition_differences, sql_differences,
    self_consistency_issues, data_quality_issues) -- recomputed here via
    the same public/module-level functions assemble_investigation_evidence
    itself calls before its guard fires, since that function is not
    modified to expose them directly. reconciliation is [] and
    unexplained_residual is None on this path -- honestly reflecting that
    neither was actually computed, not a fabricated 0.0 or empty-looking
    InvestigationEvidence pretending to be a complete result.

    The union return type is deliberate and matches reality: this
    function genuinely returns one of two different types depending on
    outcome, and hiding that behind a single type (as Part 4's now-reverted
    InvestigationEvidence.unexplained_residual widening did) would let a
    caller mistake a partial, benchmark-only result for a real, complete
    InvestigationEvidence.

    For any other case -- entry.is_ambiguous is False, or a different
    ValueError fires (e.g. query_mutation.py's unsupported-category
    errors) -- the original exception is re-raised completely unchanged.
    This is the single most important behavior this wrapper has to get
    right: it must never widen tolerance for a genuine technical-scenario
    failure just because it happens to be caught here.

    Known, accepted call-boundary limitation, stated explicitly rather
    than left implicit: even with the union return type making a partial
    result visible via isinstance()/type() at the call site, this function
    still does not stamp its output with anything referencing the
    BenchmarkEntry it was built from. A caller receiving a
    PartialInvestigationEvidence knows only that reconciliation could not
    be computed -- not, from the object alone, which scenario, why, or
    what expected_behavior applies. Scoring code consuming this function's
    output must keep the BenchmarkEntry (or at minimum its
    is_ambiguous/expected_behavior fields) alongside the returned evidence,
    not discard it."""
    try:
        return assemble_investigation_evidence(entry.scenario)
    except ValueError as exc:
        if not entry.is_ambiguous or _THREE_PLUS_CAUSES_MARKER not in str(exc):
            raise

    scenario = entry.scenario
    seed_db_path_a = str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_a.duckdb")
    seed_db_path_b = str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_b.duckdb")

    data_quality_issues = _resolve_data_quality_issues(scenario, seed_db_path_a, seed_db_path_b)
    sql_differences = diff_sql(parse_sql(scenario.source_a.sql), parse_sql(scenario.source_b.sql))
    definition_differences, self_consistency_issues, escalations = (
        assemble_definitional_evidence_with_dollar_impacts(
            scenario.source_a, scenario.source_b, seed_db_path_a, seed_db_path_b
        )
    )
    sql_differences, definition_differences = assemble_structural_and_definitional_evidence(
        sql_differences, definition_differences
    )

    return PartialInvestigationEvidence(
        definition_differences=definition_differences,
        self_consistency_issues=self_consistency_issues,
        sql_differences=sql_differences,
        data_quality_issues=data_quality_issues,
        escalations=escalations,
    )
