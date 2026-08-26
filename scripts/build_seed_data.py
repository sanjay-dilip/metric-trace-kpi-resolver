"""One-off setup script: builds the synthetic row-level DuckDB seed data
backing each of the 7 scenario fixtures in tests/fixtures/scenarios.py.

Design: Scenario.seed_table (src/scenario.py) is a single str per scenario,
but a scenario's two sides sometimes need to represent genuinely different
underlying data (Case 5's unexplained residual specifically requires this --
source_a.sql and source_b.sql are literally identical text, so the only way
running them can produce a real, non-zero delta is if they execute against
different data). So seed_table is treated as a base identifier, not a
literal single shared table name (the field's docstring from the schema PR
explicitly anticipated this: "or seed-function identifier, if that ends up
cleaner once you see the fixture-build step"). Each seed_table value maps to
two separate DuckDB database files under DATA_SAMPLE_DIR:
    {seed_table}_a.duckdb  -- backs source_a.sql, unmodified
    {seed_table}_b.duckdb  -- backs source_b.sql, unmodified
Every table inside is named exactly as referenced in that scenario's SQL
text (e.g. "orders", "customers"), so source_a.sql / source_b.sql run
against their respective file with zero rewriting.

For every scenario except Case 5, the _a and _b files hold identical row
data -- the two sides represent the same underlying reality, and the
reported difference comes entirely from how each side's SQL/declared
definition queries it. Case 5 is the deliberate exception: its _a and _b
files hold different data, simulating a cause (e.g. staleness) that is
genuinely outside sql_diff/definition_diff's scope, which is exactly what
the case exists to demonstrate.

Idempotent: re-running this script deletes and rebuilds every file listed
in SEED_SPECS from scratch.

Case 8 (Build 2, Day 2) extends this convention for freshness fixtures,
per Scenario.freshness_complete_seed_table's own docstring (src/scenario.py,
Build 2 Day 1, decision 15): its seed_table ("case_08_stale_extract")
resolves to the existing "_a"/"_b" pair exactly as above (case_08's "_a"
file is the stale/as-delivered snapshot, "_b" is already complete -- only
A has a freshness cause in this fixture). freshness_complete_seed_table
("case_08_stale_extract_complete") is a SECOND, independent base name,
resolved via the identical "_a"/"_b" suffixing: {base}_a.duckdb is the
complete counterfactual for source A (what check_stale_extract's
complete_db_path points at), {base}_b.duckdb is built too, for
consistency with every other base name in this file always producing a
matched "_a"/"_b" pair -- even though Case 8's own test only exercises
the "_a" side, since B was never stale. Both complete-pair files hold
CASE_8_ORDERS_COMPLETE; the "_a" stale file alone differs, holding
CASE_8_ORDERS_STALE_A.

Case 9 (Build 2, Day 3) follows the identical seed_table/
freshness_complete_seed_table pairing convention as Case 8 -- only the
seed DATA differs, in a way specifically chosen to read as a genuine
missing partition rather than a relabeled Case 8. Case 8's stale file is
missing one scattered row (order_id 3) with no structural relationship to
the rows that remain. Case 9's stale file is missing BOTH rows for a
single order_date ('2024-01-10') entirely -- every row in that date-slice
is gone, while a different date (2024-02-01) with its own row is
untouched. order_date is a natural partition key for a synthetic orders
table (the same column every real-world date-partitioned orders table
would partition on), so "every row for one order_date is absent" reads as
a genuine partition boundary, not an arbitrary row subset picked to hit a
target row count.

Case 10 (Build 2, Day 4) reuses the existing customers/orders table
schema (_build_customers_table, _build_orders_table -- the same functions
Case 1 already uses) rather than inventing a new fact/dimension shape --
Case 1's own data was confirmed (src/data_quality.py's module docstring)
to already have a real customers-dimension/orders-fact relationship with
a real orphan reference, but that orphan is identical on both of Case 1's
sides and serves a different diagnostic purpose (join-type SQL diff), so
it could not be reused as Case 10's fixture data. Case 10's OWN data is
new: source_a's orders table contains a genuine orphan row (customer_id=99,
which no row in customers has); source_b's orders table simply omits that
row -- source_b's data represents the trustworthy state, the same
"only A has the freshness/quality cause" pattern Case 8/9 already use.
Both sides' customers dimension table is identical (customer_id 1 and 2
only) -- the discrepancy lives entirely in orders, matching a real-world
scenario where a fact table gains a phantom row referencing a customer
that was deleted or never created upstream, not a dimension-side problem.

Case 11 (Build 2, Day 4 close-out) is Case 10's mirror image: the orphan
row sits on source_b's orders table instead of source_a's, closing a real
gap a review found -- Case 10 alone never exercised
check_referential_integrity's source="b" sign-flip branch with real
execution, only Case 10's source="a" branch. Same schema, same customers
dimension shape, deliberately duplicated rather than imported (matching
every case's self-contained data convention in this file).
"""

from pathlib import Path

import duckdb

from config import DATA_SAMPLE_DIR


def _build_customers_table(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.execute(
        "CREATE OR REPLACE TABLE customers "
        "(customer_id INTEGER, status VARCHAR, signup_date DATE)"
    )
    con.executemany("INSERT INTO customers VALUES (?, ?, ?)", rows)


def _build_orders_table(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.execute(
        "CREATE OR REPLACE TABLE orders "
        "(order_id INTEGER, customer_id INTEGER, amount DOUBLE, "
        "status VARCHAR, order_date DATE)"
    )
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", rows)


def _build_orders_table_with_created_at(
    con: duckdb.DuckDBPyConnection, rows: list[tuple]
) -> None:
    con.execute(
        "CREATE OR REPLACE TABLE orders "
        "(order_id INTEGER, amount DOUBLE, status VARCHAR, "
        "order_date DATE, created_at DATE)"
    )
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", rows)


# --- Case 1: join-type diff. order_id 3 has no matching customer, so LEFT
# JOIN keeps it and INNER JOIN drops it -- the sole source of the delta. ---
CASE_1_CUSTOMERS = [(1, "active", "2024-01-01"), (2, "active", "2024-01-01"), (3, "active", "2024-01-01")]
CASE_1_ORDERS = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 2, 200.0, "active", "2024-03-01"),
    (3, 99, 300.0, "active", "2024-01-15"),   # no matching customer_id
    (4, 3, 150.0, "cancelled", "2024-04-01"), # excluded by status, both sides
    (5, 1, 50.0, "active", "2023-12-01"),     # excluded by date, both sides
]

# --- Case 2: reused verbatim from the earlier diagnostic session's
# synthetic customers table (dedup / status-boundary overlap design). ---
CASE_2_CUSTOMERS = [
    (1, "active", "2024-02-01"),
    (1, "active", "2024-02-01"),   # duplicate row, non-trial
    (2, "trial", "2024-03-01"),
    (2, "trial", "2024-03-01"),    # duplicate row, trial -- the overlap case
    (3, "active", "2024-01-15"),
    (4, "churned", "2024-01-10"),
    (5, "trial", "2024-04-01"),
    (6, "active", "2023-12-01"),   # before cutoff
    (7, "active", "2024-05-01"),
]

# --- Case 3: reused verbatim from the earlier diagnostic session's
# synthetic orders table (mis-dated / status-boundary overlap design). ---
CASE_3_ORDERS = [
    (1, 100.0, "active", "2024-02-01", "2024-02-01"),   # clean
    (2, 200.0, "active", "2023-12-15", "2024-01-05"),   # date-only
    (3, 300.0, "refunded", "2024-01-10", "2024-01-10"), # status-only
    (4, 400.0, "refunded", "2023-11-01", "2024-01-20"), # overlap row
    (5, 500.0, "cancelled", "2024-01-25", "2023-12-20"),# excluded both, unrelated
    (6, 150.0, "active", "2024-03-01", "2024-03-01"),   # clean
]

# --- Case 4: governance drift. order_id 2 is refunded -- A's declared
# definition says it should be excluded, but A's SQL (status != 'cancelled')
# doesn't actually exclude it; B's SQL correctly does. ---
CASE_4_ORDERS = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 1, 200.0, "refunded", "2024-03-01"),  # A's SQL wrongly includes this
    (3, 1, 150.0, "cancelled", "2024-01-15"), # excluded by both
    (4, 1, 300.0, "active", "2023-12-01"),    # excluded by date, both sides
]

# --- Case 5: unexplained residual. source_a.sql and source_b.sql are
# literally identical text, so the _a and _b files deliberately hold
# DIFFERENT data -- simulating a cause (e.g. staleness) outside
# sql_diff/definition_diff's scope. Sums chosen to match the fixture's
# existing reported_value_a/reported_value_b (180000.0 / 176200.0). ---
CASE_5_ORDERS_A = [(1, 1, 180000.0, "active", "2024-02-01")]
CASE_5_ORDERS_B = [(1, 1, 176200.0, "active", "2024-02-01")]

# --- Case 6: negative control. Identical data in both files, identical
# SQL -- sums to the fixture's existing reported_value (95000.0 both sides). ---
CASE_6_ORDERS = [
    (1, 1, 60000.0, "active", "2024-02-01"),
    (2, 1, 35000.0, "active", "2024-03-01"),
    (3, 1, 20000.0, "cancelled", "2024-01-10"), # excluded
    (4, 1, 10000.0, "active", "2023-12-01"),    # excluded by date
]

# --- Case 8: stale extract (Build 2, Day 2). source_a is the stale side --
# its as-delivered extract is missing order_id 3 (e.g. it ran before that
# order was recorded). source_b already reflects the complete data (never
# stale), so CASE_8_ORDERS_B == CASE_8_ORDERS_COMPLETE exactly: B's own
# "as-delivered" data and "the complete truth" are the same fact for a
# fixture where only A has a freshness cause. Both source_a.sql/
# source_b.sql are identical text (matching Case 5/6's clean-fixture
# pattern) -- the entire gap comes from A's underlying data being
# incomplete, not from any query or definition difference. ---
CASE_8_ORDERS_COMPLETE = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 1, 200.0, "active", "2024-03-01"),
    (3, 1, 150.0, "active", "2024-01-15"),   # missing from A's stale extract
    (4, 1, 50.0, "cancelled", "2023-12-01"), # excluded by status regardless
]
CASE_8_ORDERS_STALE_A = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 1, 200.0, "active", "2024-03-01"),
    (4, 1, 50.0, "cancelled", "2023-12-01"),
]

# --- Case 9: missing partition (Build 2, Day 3). source_a's stale extract
# is missing the ENTIRE '2024-01-10' date-slice (order_id 1 and 2, both
# rows), not a scattered single row -- a genuine contiguous partition
# absent, distinct from Case 8's scattered single-row gap. source_b
# already reflects the complete data, matching Case 8's "only A has a
# freshness cause" pattern exactly. ---
CASE_9_ORDERS_COMPLETE = [
    (1, 1, 100.0, "active", "2024-01-10"),
    (2, 1, 150.0, "active", "2024-01-10"),   # same date-slice as order_id 1
    (3, 1, 200.0, "active", "2024-02-01"),
    (4, 1, 50.0, "cancelled", "2023-12-01"), # excluded by status regardless
]
CASE_9_ORDERS_STALE_A = [
    (3, 1, 200.0, "active", "2024-02-01"),
    (4, 1, 50.0, "cancelled", "2023-12-01"),
]

# --- Case 10: referential integrity (Build 2, Day 4). Both sides share the
# same customers dimension (customer_id 1 and 2 only). source_a's orders
# fact table has a genuine orphan row (order_id 3, customer_id=99 -- no
# such customer exists); source_b's orders table simply doesn't have that
# row, representing the trustworthy state (only A has the data-quality
# cause, matching Case 8/9's own pattern). ---
CASE_10_CUSTOMERS = [(1, "active", "2024-01-01"), (2, "active", "2024-01-01")]
CASE_10_ORDERS_A = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 2, 200.0, "active", "2024-03-01"),
    (3, 99, 300.0, "active", "2024-01-15"),  # orphan: customer_id 99 does not exist
    (4, 1, 50.0, "cancelled", "2023-12-01"), # excluded by status regardless
]
CASE_10_ORDERS_B = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 2, 200.0, "active", "2024-03-01"),
    (4, 1, 50.0, "cancelled", "2023-12-01"),
]

# --- Case 11: referential integrity, mirrored onto source_b (Build 2, Day 4
# close-out). Case 10 only ever puts the orphan reference on source_a, so
# check_referential_integrity's source="b" sign-flip branch had zero
# execution-derived proof -- the exact same shape of gap Day 6's
# self-consistency close-out found and closed with a dedicated source="b"
# test, not assumed safe by code inspection alone. Case 11 is the mirror
# image of Case 10: source_b's orders table has the orphan row
# (customer_id=99), source_a's omits it. Same customers dimension as
# Case 10 (customer_id 1 and 2 only), duplicated here rather than
# imported, matching every other case's self-contained-fixture-data
# convention in this file. ---
CASE_11_CUSTOMERS = [(1, "active", "2024-01-01"), (2, "active", "2024-01-01")]
CASE_11_ORDERS_A = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 2, 200.0, "active", "2024-03-01"),
    (4, 1, 50.0, "cancelled", "2023-12-01"),
]
CASE_11_ORDERS_B = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 2, 200.0, "active", "2024-03-01"),
    (3, 99, 300.0, "active", "2024-01-15"),  # orphan: customer_id 99 does not exist
    (4, 1, 50.0, "cancelled", "2023-12-01"), # excluded by status regardless
]

# --- Case 7: precedence-rule conflict. order_id 2 (cancelled) is wrongly
# INCLUDED by A's as-written SQL (which only excludes 'refunded', not
# 'cancelled' as A declares) -- A's SQL under-excludes relative to its own
# declaration. order_id 3 (refunded) is wrongly EXCLUDED by A's as-written
# SQL relative to what A's declaration alone would imply -- A's SQL excludes
# a status ('refunded') that A's declared excluded_statuses (['cancelled'])
# never named. Together these give "A corrected to its own declaration"
# (excluding only 'cancelled') a materially different sum (400.0) than A's
# as-written SQL (300.0) -- a real, measurable 100.0 gap, same as Case 4's
# audit found, supporting a future Day 5 dollar-impact computation without
# building it here. ---
CASE_7_ORDERS = [
    (1, 1, 100.0, "active", "2024-02-01"),
    (2, 1, 200.0, "cancelled", "2024-03-01"),  # A's SQL wrongly includes this
    (3, 1, 300.0, "refunded", "2024-01-15"),   # A's SQL wrongly excludes this
    (4, 1, 400.0, "active", "2023-12-01"),     # excluded by date, both sides
]


def _seed_file(seed_table: str, side: str, *table_builds: tuple) -> Path:
    """Create (or replace) one seed file at DATA_SAMPLE_DIR/{seed_table}_{side}.duckdb,
    running each (build_fn, rows) pair in table_builds against it in one connection."""
    path = DATA_SAMPLE_DIR / f"{seed_table}_{side}.duckdb"
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        for build_fn, rows in table_builds:
            build_fn(con, rows)
    finally:
        con.close()
    return path


def build_all() -> None:
    """(Re)builds every seed file referenced by tests/fixtures/scenarios.py."""
    DATA_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    for side in ("a", "b"):
        _seed_file(
            "case_01_join_type", side,
            (_build_customers_table, CASE_1_CUSTOMERS),
            (_build_orders_table, CASE_1_ORDERS),
        )
        _seed_file("case_02_multi_cause", side, (_build_customers_table, CASE_2_CUSTOMERS))
        _seed_file(
            "case_03_hybrid_fallback", side,
            (_build_orders_table_with_created_at, CASE_3_ORDERS),
        )
        _seed_file("case_04_governance_drift", side, (_build_orders_table, CASE_4_ORDERS))
        _seed_file("case_06_negative_control", side, (_build_orders_table, CASE_6_ORDERS))
        _seed_file("case_07_precedence_conflict", side, (_build_orders_table, CASE_7_ORDERS))

    _seed_file("case_05_unexplained_residual", "a", (_build_orders_table, CASE_5_ORDERS_A))
    _seed_file("case_05_unexplained_residual", "b", (_build_orders_table, CASE_5_ORDERS_B))

    _seed_file("case_08_stale_extract", "a", (_build_orders_table, CASE_8_ORDERS_STALE_A))
    _seed_file("case_08_stale_extract", "b", (_build_orders_table, CASE_8_ORDERS_COMPLETE))
    _seed_file("case_08_stale_extract_complete", "a", (_build_orders_table, CASE_8_ORDERS_COMPLETE))
    _seed_file("case_08_stale_extract_complete", "b", (_build_orders_table, CASE_8_ORDERS_COMPLETE))

    _seed_file("case_09_missing_partition", "a", (_build_orders_table, CASE_9_ORDERS_STALE_A))
    _seed_file("case_09_missing_partition", "b", (_build_orders_table, CASE_9_ORDERS_COMPLETE))
    _seed_file("case_09_missing_partition_complete", "a", (_build_orders_table, CASE_9_ORDERS_COMPLETE))
    _seed_file("case_09_missing_partition_complete", "b", (_build_orders_table, CASE_9_ORDERS_COMPLETE))

    _seed_file(
        "case_10_referential_integrity", "a",
        (_build_customers_table, CASE_10_CUSTOMERS),
        (_build_orders_table, CASE_10_ORDERS_A),
    )
    _seed_file(
        "case_10_referential_integrity", "b",
        (_build_customers_table, CASE_10_CUSTOMERS),
        (_build_orders_table, CASE_10_ORDERS_B),
    )

    _seed_file(
        "case_11_referential_integrity_source_b", "a",
        (_build_customers_table, CASE_11_CUSTOMERS),
        (_build_orders_table, CASE_11_ORDERS_A),
    )
    _seed_file(
        "case_11_referential_integrity_source_b", "b",
        (_build_customers_table, CASE_11_CUSTOMERS),
        (_build_orders_table, CASE_11_ORDERS_B),
    )


if __name__ == "__main__":
    build_all()
    print(f"Seed data written to {DATA_SAMPLE_DIR}")
