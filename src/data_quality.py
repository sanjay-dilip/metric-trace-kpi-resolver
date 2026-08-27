"""Deterministic data-quality/freshness pre-check (Build 2, Days 2-4). Per the
locked architecture decision (Build 2, Day 1): this is a plain, directly
callable module, exactly like src/sql_diff.py or src/self_consistency.py --
not an LLM-directed agent, no orchestration, nothing LangGraph-related.
That packaging question does not arise again until Build 4.

check_referential_integrity (Day 4) is a GENUINELY DISTINCT mechanism from
check_stale_extract/check_missing_partition, not a third row-count-diff
category wearing a new label. It tests whether every foreign-key value in a
fact-style table resolves to an existing row in a dimension-style table --
a relational-integrity question, not a completeness/row-count question. It
uses a different arithmetic primitive too: single_cause_attribution (one
database, two SQL variants -- baseline vs. "FK-filtered"), the same shape
self-consistency's own corrected-query pattern uses, not
freshness_attribution's "same query, two databases" shape _check_completeness
relies on. This was a locked design requirement for Day 4, verified directly
against this project's own committed seed data before writing any detection
logic: Case 1 (tests/fixtures/scenarios.py, scripts/build_seed_data.py) is
the one existing fixture with a genuine fact/dimension relationship
(orders.customer_id -> customers.customer_id), and its data already
contains a real orphan reference (order_id=3, customer_id=99) -- but that
orphan is identical on both sides of Case 1 and serves an entirely
different diagnostic purpose (the join-type SQL-structural diff), not a
cross-source data-quality discrepancy, so it could not be reused as Case
10's fixture data. Case 10 reuses the EXISTING table-building helpers
(_build_customers_table, _build_orders_table, already defined for Case 1)
-- the schema itself was not reinvented -- but its row data is new,
authored specifically to isolate a referential-integrity cause the way
Case 8/9 isolate their own freshness causes. A self-referencing nullable
column within one table was explicitly considered and rejected for
simulating this (not an honest representation of the category) -- Case 10
uses two real tables with a real foreign-key relationship between them.

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
import sqlglot
from sqlglot import exp

from src.reconciliation import freshness_attribution, single_cause_attribution
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


def _orphan_count(
    fact_db_path: str,
    dimension_db_path: str,
    fact_table: str,
    dimension_table: str,
    fk_column: str,
    dimension_key_column: str,
) -> int:
    """Count fact rows whose `fk_column` value has no matching row in
    `dimension_table`'s `dimension_key_column`. When `fact_db_path` and
    `dimension_db_path` are the same file (Case 10's own shape -- both
    tables live in one DuckDB file, same as Case 1's precedent), the
    dimension table is queried directly by name on the single open
    connection. When they differ (used only by this module's own
    cross-category discrimination test, running the check against Case
    8/9's fact-only files with an external dimension file supplied), the
    dimension file is ATTACHed read-only under an alias so a single query
    can join across both.

    Assumes `fk_column` is never NULL across every row tested -- none of
    this project's committed fixtures (Case 1 or Case 10) populate a
    nullable FK. A genuinely optional/nullable FK (where NULL correctly
    means "no reference intended," not an orphan) would need NULL-aware
    handling this function does not attempt; that is a different, untested
    shape, not the orphan-reference shape Case 10 tests.
    """
    con = duckdb.connect(fact_db_path, read_only=True)
    try:
        if fact_db_path == dimension_db_path:
            dimension_ref = dimension_table
        else:
            con.execute(f"ATTACH '{dimension_db_path}' AS dim_db (READ_ONLY)")
            dimension_ref = f"dim_db.{dimension_table}"
        query = (
            f"SELECT COUNT(*) FROM {fact_table} "
            f"WHERE {fk_column} NOT IN (SELECT {dimension_key_column} FROM {dimension_ref})"
        )
        return int(con.execute(query).fetchone()[0])
    finally:
        con.close()


def _fact_table_qualifier(query_sql: str, fact_table: str) -> str:
    """Build 3, Day 1, Part 3 bug fix: find how `query_sql` actually refers
    to `fact_table` in its own FROM/JOIN clauses -- its alias if one is
    used (e.g. "orders o" -> "o"), or the bare table name if it isn't
    aliased at all. Needed because check_referential_integrity's
    dollar-impact step appends an `AND {fk_column} IN (...)` clause onto
    query_sql, and a BARE fk_column reference is ambiguous whenever
    query_sql already joins fact_table to dimension_table on that same
    column (both tables then have a column of that name in scope) --
    confirmed as a real crash (Build 3, Day 1, Part 2, Case 12:
    duckdb.BinderException, "Ambiguous reference to column name
    'customer_id'"). Qualifying with the bare fact_table name alone is not
    sufficient once query_sql aliases it (DuckDB rejects a query's own
    real table name once an alias is in play: "Referenced table 'orders'
    not found! Candidate tables: 'o'", confirmed directly) -- this
    function resolves the correct qualifier either way, rather than
    guessing one shape works for both."""
    tree = sqlglot.parse_one(query_sql)
    for table in tree.find_all(exp.Table):
        if table.name.lower() == fact_table.lower():
            return table.alias or table.name
    return fact_table


def check_referential_integrity(
    fact_db_path: str,
    dimension_db_path: str,
    fact_table: str,
    dimension_table: str,
    fk_column: str,
    dimension_key_column: str,
    query_sql: str,
    source: Literal["a", "b"],
) -> DataQualityIssue | None:
    """Referential-integrity detection (Build 2, Day 4): does every
    `fk_column` value in `fact_table` resolve to an existing
    `dimension_key_column` value in `dimension_table`? This is a
    genuinely distinct mechanism from check_stale_extract/
    check_missing_partition (see this module's docstring) -- a relational
    lookup, not a row-count comparison -- and cannot fire or be confused
    with either of those checks: it does not compare two snapshots of the
    same table at all, and this module's own cross-category discrimination
    test (test_data_quality.py) confirms directly that it returns None on
    Case 8/9's data (every FK value there resolves against a supplied
    dimension table), the inverse of Day 3's discrimination proof.

    Returns None when zero orphan rows exist. When one or more do, returns
    a populated DataQualityIssue with category="referential_integrity",
    confidence="high" (see _CONFIDENCE -- a COUNT(*) orphan check is just
    as mechanically exact as a row-count diff), a description stating the
    orphan count, and a real, execution-derived dollar_impact:
    `single_cause_attribution` (src/reconciliation.py, the same
    one-database/two-SQL-variant shape self-consistency's own
    corrected-query pattern uses, not freshness_attribution's two-database
    shape) compares `query_sql` (baseline, as-written) against a
    "FK-filtered" corrected variant -- `query_sql` with an added
    `AND {qualifier}.{fk_column} IN (SELECT {dimension_key_column} FROM {dimension_table})`
    clause, run against `fact_db_path`, where `{qualifier}` is resolved by
    `_fact_table_qualifier` (this module, Build 3 Day 1 Part 3 bug fix) --
    `fact_table`'s alias if `query_sql` uses one, or `fact_table` itself if
    it doesn't. A BARE, unqualified `fk_column` reference was the original
    (Build 2, Day 4) approach and worked for every fixture that existed
    then (Cases 10/11, neither of which joins `fact_table` to
    `dimension_table`), but crashes with a DuckDB BinderException
    ("ambiguous reference") the moment `query_sql` joins the two on that
    exact column, which is exactly what Case 12 (Build 3, Day 1, Part 2)
    does -- confirmed as a real, previously-undiscovered gap, not a
    hypothetical one, and fixed here without changing the surrounding
    query-construction shape (still a single string-append, just a
    correctly-qualified one).

    This string-append approach still requires `query_sql` to already
    contain a WHERE clause, true of every fixture query in this project
    (Case 1/4/6/7/8/9/10/12 all do) -- a deliberate, documented scope
    narrowing to this project's own committed query shapes, the same kind
    of pragmatic limit src/query_mutation.py's rule-based mutators already
    accept, not a general SQL-rewriting engine.
    It also assumes `dimension_table` is queryable by that bare name in
    `fact_db_path` (true whenever fact and dimension are co-located in one
    file, Case 10's shape) -- the dollar-impact computation, unlike
    `_orphan_count` above, does not support a cross-database fact/dimension
    split; the only place this module needs that split is the
    discrimination test above, which never reaches this branch (Case 8/9
    have zero orphans against the dimension supplied to them).

    The raw (corrected - baseline) delta is signed with the EXACT
    convention already established for SelfConsistencyIssue.dollar_impact
    and _check_completeness's own dollar_impact: negated for source="a",
    used as-is for source="b" -- no new sign rule invented for this cause
    type either.

    Cross-category interaction with a DefinitionDifference/
    SQLStructuralDifference/other DataQualityIssue on the same fixture's
    overlapping rows is untested and out of scope (Build 2, Day 1's own
    stated boundary, mirroring decision 11) -- this function assumes the
    query result difference is attributable to the orphan reference alone,
    which only holds for a fixture built the way Case 10 is (referential
    integrity is the sole cause present). This function also does not
    resolve, and deliberately does not need to resolve, the still-open
    check_stale_extract/check_missing_partition dispatch-rule question
    (CONTEXT.md Open Items): that item concerns two mechanism-identical
    row-count checks firing on the same completeness-style input, which
    this function's relational-lookup mechanism and dimension-table
    requirement never trigger.
    """
    orphan_count = _orphan_count(
        fact_db_path, dimension_db_path, fact_table, dimension_table, fk_column, dimension_key_column
    )
    if orphan_count == 0:
        return None

    qualifier = _fact_table_qualifier(query_sql, fact_table)
    corrected_sql = (
        f"{query_sql} AND {qualifier}.{fk_column} IN (SELECT {dimension_key_column} FROM {dimension_table})"
    )
    raw_delta = single_cause_attribution(fact_db_path, query_sql, corrected_sql)
    dollar_impact = -raw_delta if source == "a" else raw_delta

    return DataQualityIssue(
        category="referential_integrity",
        source=source,
        description=(
            f"source_{source}'s '{fact_table}' has {orphan_count} row(s) whose "
            f"'{fk_column}' value does not match any '{dimension_key_column}' in "
            f"'{dimension_table}'."
        ),
        confidence=_CONFIDENCE,
        dollar_impact=dollar_impact,
    )
