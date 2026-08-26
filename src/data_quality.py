"""Deterministic data-quality/freshness pre-check (Build 2, Day 2-3). Per the
locked architecture decision (Build 2, Day 1): this is a plain, directly
callable module, exactly like src/sql_diff.py or src/self_consistency.py --
not an LLM-directed agent, no orchestration, nothing LangGraph-related.
That packaging question does not arise again until Build 4.

check_stale_extract and check_missing_partition (Day 3) are deliberately
scoped as a single COMPLETENESS-diff mechanism, framed as two different
detection categories, not a timestamp-based staleness model. Both know only
two DuckDB snapshots' row counts for one table and ask whether they match --
neither has any notion of extraction time, SLA, or freshness deadline. A
real production detector would likely also reason about *when* data
arrived; these deliberately do not, per this session's own scope: "no
timestamp, no SLA field, no new schema." A reader should not mistake either
for more than a binary structural completeness check.

check_stale_extract and check_missing_partition share ONE detection
mechanism -- a row-count diff between a snapshot and its complete
counterfactual (_check_completeness, below). This is not two detection
capabilities; it is one mechanism exposed under two names. The distinction
between "stale extract" (Case 8: scattered individual rows missing, no
structural grouping) and "missing partition" (Case 9: an entire contiguous,
identifiable chunk -- e.g. every row for one date -- missing) lives
ENTIRELY in how each fixture's seed data is constructed, not in this code.
Both public functions will fire identically on either fixture's row-count
delta; nothing here inspects *which* rows are missing or whether they form
a contiguous group. See test_data_quality.py's cross-category
discrimination test, which proves this deliberately rather than leaving it
to be discovered as a surprise.

No LLM calls anywhere: same rule-based, execution-backed discipline as
every other Build 1/2 deterministic tool.
"""

from typing import Literal

import duckdb

from src.reconciliation import freshness_attribution
from src.schema import DataQualityIssue

_CONFIDENCE = "high"
"""check_stale_extract's row-count comparison is an exact, mechanically
verified fact (a COUNT(*) query against each snapshot), the same kind of
certainty SQLStructuralDifference's findings have (which is why that type
carries no confidence field at all) -- unlike DefinitionDifference/
SelfConsistencyIssue's confidence, which varies because SQL inference can
genuinely be ambiguous. DataQualityIssue's schema (Build 2, Day 1) requires
a confidence field on every instance regardless, so "high" is used as a
fixed constant here rather than a per-call judgment, and is not expected to
vary once missing_partition/late_arriving_data/referential_integrity are
built later -- each of those may have a genuine reason to vary confidence,
which this constant deliberately does not decide now."""


def _row_count(db_path: str, table_name: str) -> int:
    con = duckdb.connect(db_path, read_only=True)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    finally:
        con.close()


def _check_completeness(
    complete_db_path: str,
    stale_db_path: str,
    table_name: str,
    query_sql: str,
    source: Literal["a", "b"],
    category: Literal["stale_extract", "missing_partition"],
) -> DataQualityIssue | None:
    """Shared row-count-completeness mechanism behind both
    check_stale_extract and check_missing_partition (Build 2, Day 3). Both
    public functions delegate here directly rather than one wrapping the
    other, since neither is conceptually "more basic" than the other --
    they are the same check under two names, distinguished only by which
    `category` string the caller passes. A wrapping relationship (one
    calling the other) would misleadingly imply an asymmetry that does not
    exist.

    Compares `table_name`'s row count between `stale_db_path` (the
    scenario's seed_table -- per decision 15, the stale/as-delivered
    snapshot that actually produced the reported value) and
    `complete_db_path` (the scenario's freshness_complete_seed_table
    counterfactual). Row count, not partition count, is the comparison
    unit: this project's seed data (scripts/build_seed_data.py) has no
    partitioning scheme at all -- every table is a single flat DuckDB
    table with no partition column to compare -- so row count is the only
    completeness signal actually available, and the more direct one for
    what this check is trying to detect (are rows missing) regardless.
    This holds identically for missing_partition: whether the missing rows
    are scattered (Case 8) or form one contiguous, identifiable chunk
    (Case 9) is invisible to a row-count diff -- that distinction lives
    entirely in how each fixture's seed data is constructed, never in this
    function's logic.

    Returns None when the counts match (no issue -- the extract is
    complete relative to the counterfactual). When they differ, returns a
    populated DataQualityIssue with the given `category`, confidence="high"
    (see _CONFIDENCE), a description stating the actual counts, and a real,
    execution-derived dollar_impact: `query_sql` (the scenario's actual
    query for `source`'s side) is run against both snapshots via
    freshness_attribution (src/reconciliation.py, same "same query, two
    databases" shape this cause type needs), and the raw (complete - stale)
    delta is signed with the EXACT convention already established for
    SelfConsistencyIssue.dollar_impact (src/self_consistency.py's
    compute_self_consistency_dollar_impacts): negated for source="a", used
    as-is for source="b" -- no new sign rule invented for this cause type.

    Signature extended beyond the three bare parameters (complete_db_path,
    stale_db_path, table_name) originally sketched for check_stale_extract
    -- `query_sql` and `source` were added because a caller needs both to
    compute a real dollar_impact and set DataQualityIssue.source correctly;
    a row-count-only check could report that an extract is incomplete, but
    not by how much it is worth, which this session's own brief explicitly
    requires. This is flagged here, but deliberately NOT logged as a
    docs/decisions.md entry the way decision 15 was, and that is itself a
    judgment call worth stating rather than leaving to infer: decision 15
    was a genuine reinterpretation of an existing field's semantics
    (seed_table silently means something additional for a subset of
    scenarios) -- a real architectural choice with a lasting tradeoff a
    future reader could be misled by. This signature extension is neither:
    query_sql and source aren't reinterpreted, they're new parameters added
    because the function's own stated job (a real, correctly-signed
    dollar_impact) is mechanically impossible to satisfy without them --
    the same shape as Day 6's dollar_impact placeholder naturally growing a
    real signature once execution was required, not a semantic pivot.
    Nothing about this changes what any EXISTING field or function means,
    so there is nothing for a decisions.md entry to protect a future
    reader from missing.

    Cross-category interaction with a DefinitionDifference/
    SQLStructuralDifference on the same fixture's overlapping rows is
    untested and out of scope (Build 2, Day 1's own stated boundary,
    mirroring decision 11) -- this function assumes the query result
    difference is attributable to the freshness cause alone, which only
    holds for a fixture built the way Case 8/Case 9 are (freshness is the
    sole cause present).
    """
    complete_count = _row_count(complete_db_path, table_name)
    stale_count = _row_count(stale_db_path, table_name)

    if complete_count == stale_count:
        return None

    raw_delta = freshness_attribution(stale_db_path, complete_db_path, query_sql)
    dollar_impact = -raw_delta if source == "a" else raw_delta

    return DataQualityIssue(
        category=category,
        source=source,
        description=(
            f"source_{source}'s as-delivered snapshot has {stale_count} row(s) in "
            f"'{table_name}', but the complete counterfactual snapshot has "
            f"{complete_count} -- {complete_count - stale_count:+d} row(s) difference."
        ),
        confidence=_CONFIDENCE,
        dollar_impact=dollar_impact,
    )


def check_stale_extract(
    complete_db_path: str,
    stale_db_path: str,
    table_name: str,
    query_sql: str,
    source: Literal["a", "b"],
) -> DataQualityIssue | None:
    """Stale-extract detection: scattered individual rows missing from
    `stale_db_path` relative to `complete_db_path`, no structural grouping
    among them (Case 8). Delegates directly to _check_completeness with
    category="stale_extract" -- see that function and this module's
    docstring for the full mechanism and the stale_extract/missing_partition
    distinction (fixture-construction-only, not detection-only)."""
    return _check_completeness(
        complete_db_path, stale_db_path, table_name, query_sql, source, "stale_extract"
    )


def check_missing_partition(
    complete_db_path: str,
    stale_db_path: str,
    table_name: str,
    query_sql: str,
    source: Literal["a", "b"],
) -> DataQualityIssue | None:
    """Missing-partition detection: an entire contiguous, identifiable slice
    (e.g. every row for one date) absent from `stale_db_path` relative to
    `complete_db_path` (Case 9). Delegates directly to _check_completeness
    with category="missing_partition" -- see that function and this
    module's docstring for the full mechanism and the
    stale_extract/missing_partition distinction (fixture-construction-only,
    not detection-only): this function will fire identically to
    check_stale_extract on any fixture with a row-count delta, Case 8
    included -- see test_data_quality.py's cross-category discrimination
    test, which confirms this is by design, not a bug."""
    return _check_completeness(
        complete_db_path, stale_db_path, table_name, query_sql, source, "missing_partition"
    )
