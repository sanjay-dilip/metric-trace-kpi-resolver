"""Deterministic data-quality/freshness pre-check (Build 2, Day 2). Per the
locked architecture decision (Build 2, Day 1): this is a plain, directly
callable module, exactly like src/sql_diff.py or src/self_consistency.py --
not an LLM-directed agent, no orchestration, nothing LangGraph-related.
That packaging question does not arise again until Build 4.

check_stale_extract is deliberately scoped as a COMPLETENESS-diff mechanism,
framed as "stale extract" detection, not a timestamp-based staleness model.
It has no notion of extraction time, SLA, or freshness deadline -- it knows
only two DuckDB snapshots' row counts for one table and asks whether they
match. A real stale-extract detector in a production system would likely
also reason about *when* data arrived; this one deliberately does not, per
this session's own scope: "no timestamp, no SLA field, no new schema." A
reader should not mistake this for more than a binary structural
completeness check.

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


def check_stale_extract(
    complete_db_path: str,
    stale_db_path: str,
    table_name: str,
    query_sql: str,
    source: Literal["a", "b"],
) -> DataQualityIssue | None:
    """Compare `table_name`'s row count between `stale_db_path` (the
    scenario's seed_table -- per decision 15, the stale/as-delivered
    snapshot that actually produced the reported value) and
    `complete_db_path` (the scenario's freshness_complete_seed_table
    counterfactual). Row count, not partition count, is the comparison
    unit: this project's seed data (scripts/build_seed_data.py) has no
    partitioning scheme at all -- every table is a single flat DuckDB
    table with no partition column to compare -- so row count is the only
    completeness signal actually available, and the more direct one for
    what this check is trying to detect (are rows missing) regardless.

    Returns None when the counts match (no issue -- the extract is
    complete relative to the counterfactual). When they differ, returns a
    populated DataQualityIssue with category="stale_extract",
    confidence="high" (see _CONFIDENCE), a description stating the actual
    counts, and a real, execution-derived dollar_impact: `query_sql` (the
    scenario's actual query for `source`'s side) is run against both
    snapshots via freshness_attribution (src/reconciliation.py, same
    "same query, two databases" shape this cause type needs), and the
    raw (complete - stale) delta is signed with the EXACT convention
    already established for SelfConsistencyIssue.dollar_impact
    (src/self_consistency.py's compute_self_consistency_dollar_impacts):
    negated for source="a", used as-is for source="b" -- no new sign
    rule invented for this cause type.

    Signature extended beyond the three bare parameters (complete_db_path,
    stale_db_path, table_name) originally sketched for it -- `query_sql`
    and `source` were added because a caller needs both to compute a real
    dollar_impact and set DataQualityIssue.source correctly; a
    row-count-only check could report that an extract is incomplete, but
    not by how much it is worth, which this session's own brief
    explicitly requires. This is flagged here, but deliberately NOT
    logged as a docs/decisions.md entry the way decision 15 was, and that
    is itself a judgment call worth stating rather than leaving to
    infer: decision 15 was a genuine reinterpretation of an existing
    field's semantics (seed_table silently means something additional for
    a subset of scenarios) -- a real architectural choice with a lasting
    tradeoff a future reader could be misled by. This signature extension
    is neither: query_sql and source aren't reinterpreted, they're new
    parameters added because the function's own stated job (a real,
    correctly-signed dollar_impact) is mechanically impossible to satisfy
    without them -- the same shape as Day 6's dollar_impact placeholder
    naturally growing a real signature once execution was required,
    not a semantic pivot. Nothing about this changes what any EXISTING
    field or function means, so there is nothing for a decisions.md entry
    to protect a future reader from missing.

    Cross-category interaction with a DefinitionDifference/
    SQLStructuralDifference on the same fixture's overlapping rows is
    untested and out of scope (Build 2, Day 1's own stated boundary,
    mirroring decision 11) -- this function assumes the query result
    difference is attributable to staleness alone, which only holds for
    a fixture built the way Case 8 is (freshness is the sole cause
    present).
    """
    complete_count = _row_count(complete_db_path, table_name)
    stale_count = _row_count(stale_db_path, table_name)

    if complete_count == stale_count:
        return None

    raw_delta = freshness_attribution(stale_db_path, complete_db_path, query_sql)
    dollar_impact = -raw_delta if source == "a" else raw_delta

    return DataQualityIssue(
        category="stale_extract",
        source=source,
        description=(
            f"source_{source}'s as-delivered snapshot has {stale_count} row(s) in "
            f"'{table_name}', but the complete counterfactual snapshot has "
            f"{complete_count} -- {complete_count - stale_count:+d} row(s) difference."
        ),
        confidence=_CONFIDENCE,
        dollar_impact=dollar_impact,
    )
