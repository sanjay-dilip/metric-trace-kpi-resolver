"""Ambiguity-aware benchmark wrapper (Build 3, Day 2, Part 4, A3-ii) around
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
an ambiguity-aware catch on top, for benchmark scoring purposes."""

from config import DATA_SAMPLE_DIR
from src.reconciliation_assembly import (
    _resolve_data_quality_issues,
    assemble_investigation_evidence,
)
from src.schema import InvestigationEvidence
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


def assemble_investigation_evidence_for_benchmark(entry: BenchmarkEntry) -> InvestigationEvidence:
    """Benchmark-only wrapper: calls assemble_investigation_evidence(entry.scenario)
    normally. If that raises the specific 3+-remaining-causes ValueError
    AND entry.is_ambiguous is True, catches it and returns a partial
    InvestigationEvidence built from the individual findings (definition_differences,
    sql_differences, self_consistency_issues, data_quality_issues) --
    recomputed here via the same public/module-level functions
    assemble_investigation_evidence itself calls before its guard fires,
    since that function is not modified to expose them directly.
    reconciliation is [] and unexplained_residual is None in this case --
    None, not 0.0 or any other placeholder, because no residual was
    actually computed; InvestigationEvidence.unexplained_residual was
    widened to float | None (src/schema.py) specifically for this path.

    For any other case -- entry.is_ambiguous is False, or a different
    ValueError fires (e.g. query_mutation.py's unsupported-category
    errors) -- the original exception is re-raised completely unchanged.
    This is the single most important behavior this wrapper has to get
    right: it must never widen tolerance for a genuine technical-scenario
    failure just because it happens to be caught here.

    Known, accepted call-boundary limitation, stated explicitly rather
    than left implicit: this function's partial-evidence output (an
    InvestigationEvidence with reconciliation=[] and
    unexplained_residual=None) is only meaningful when reached through a
    BenchmarkEntry whose expected_behavior is already known to the
    caller. Calling this function does not itself stamp the returned
    InvestigationEvidence object with any marker of how it was produced --
    a caller holding only the returned InvestigationEvidence, without its
    originating BenchmarkEntry, cannot distinguish "this is a genuinely
    ambiguous scenario's partial evidence" from any other empty-
    reconciliation outcome by inspecting the object alone. Scoring code
    consuming this function's output must keep the BenchmarkEntry (or at
    minimum its is_ambiguous/expected_behavior fields) alongside the
    returned evidence, not discard it."""
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
    definition_differences, self_consistency_issues = assemble_definitional_evidence_with_dollar_impacts(
        scenario.source_a, scenario.source_b, seed_db_path_a, seed_db_path_b
    )
    sql_differences, definition_differences = assemble_structural_and_definitional_evidence(
        sql_differences, definition_differences
    )

    return InvestigationEvidence(
        definition_differences=definition_differences,
        self_consistency_issues=self_consistency_issues,
        sql_differences=sql_differences,
        data_quality_issues=data_quality_issues,
        reconciliation=[],
        unexplained_residual=None,
    )
