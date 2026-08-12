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


if __name__ == "__main__":
    build_all()
    print(f"Seed data written to {DATA_SAMPLE_DIR}")
