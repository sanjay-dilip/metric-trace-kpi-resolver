"""Tests for src.data_quality: the stale-extract, missing-partition, and
referential-integrity checks (Build 2, Days 2-4). Proves, for each: that it
fires correctly on a real, designed-to-fire fixture (Case 8 for
stale_extract, Case 9 for missing_partition, Case 10 for
referential_integrity), and that it does NOT false-positive on a clean
scratch case -- the same non-firing standard Day 4/Day 5's precedence-rule
verification (Case 7, decision 10) was held to. Also proves (Day 3) that
check_missing_partition and check_stale_extract are mechanism-identical by
design: check_missing_partition fires on Case 8's scattered-row data
exactly like check_stale_extract does, since the category distinction
lives entirely in fixture construction, not in either function's logic
(src/data_quality.py's module docstring). And proves the inverse (Day 4):
check_referential_integrity does NOT fire on Case 8 or Case 9's data,
confirming it is a genuinely distinct mechanism, not a third row-count
check in disguise. Case 11 (Day 4 close-out) closes a real gap a review
found: Case 10 alone only ever exercised check_referential_integrity's
source="a" dollar_impact sign branch with real execution -- the source="b"
sign-flip was applied in code by direct structural analogy to
check_stale_extract's own already-proven convention, but had never itself
been proven by running the code with the cause on source_b. Case 11 is
Case 10's exact mirror image, proving the sign-flip by execution rather
than by code inspection alone -- the same shape of gap Day 6's
self-consistency close-out found and closed with its own dedicated
source="b" test."""

import duckdb

from config import DATA_SAMPLE_DIR
from src.data_quality import check_missing_partition, check_referential_integrity, check_stale_extract
from tests.fixtures.scenarios import (
    CASE_8_STALE_EXTRACT,
    CASE_9_MISSING_PARTITION,
    CASE_10_REFERENTIAL_INTEGRITY,
    CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B,
)


def test_case_8_fires_with_execution_derived_dollar_impact_matching_known_gap():
    """Case 8 is a single-cause fixture by design (no definitional or
    structural cause present) -- its dollar_impact must equal known_gap
    exactly, the same standard Cases 1-4/7 are held to."""
    s = CASE_8_STALE_EXTRACT
    stale_db = str(DATA_SAMPLE_DIR / f"{s.seed_table}_a.duckdb")
    complete_db = str(DATA_SAMPLE_DIR / f"{s.freshness_complete_seed_table}_a.duckdb")

    issue = check_stale_extract(complete_db, stale_db, "orders", s.source_a.sql, "a")

    assert issue is not None
    assert issue.category == "stale_extract"
    assert issue.source == "a"
    assert issue.confidence == "high"
    assert "3 row(s)" in issue.description
    assert "4" in issue.description
    assert issue.dollar_impact == s.known_gap == -150.0


def test_does_not_fire_when_row_counts_match(tmp_path):
    """Non-firing verification, required by this session's own brief:
    construct two snapshots with identical row counts and confirm
    check_stale_extract returns None rather than false-positiving."""
    rows = [(1, 1, 100.0, "active", "2024-02-01"), (2, 1, 200.0, "active", "2024-03-01")]

    complete_path = str(tmp_path / "identical_complete.duckdb")
    stale_path = str(tmp_path / "identical_stale.duckdb")
    for path in (complete_path, stale_path):
        con = duckdb.connect(path)
        con.execute(
            "CREATE OR REPLACE TABLE orders "
            "(order_id INTEGER, customer_id INTEGER, amount DOUBLE, status VARCHAR, order_date DATE)"
        )
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", rows)
        con.close()

    result = check_stale_extract(complete_path, stale_path, "orders", "SELECT SUM(amount) FROM orders", "a")

    assert result is None


def test_case_9_missing_partition_fires_with_execution_derived_dollar_impact_matching_known_gap():
    """Case 9 is a single-cause fixture by design (no definitional or
    structural cause present) -- its dollar_impact must equal known_gap
    exactly, the same standard Case 8 and Cases 1-4/7 are held to."""
    s = CASE_9_MISSING_PARTITION
    stale_db = str(DATA_SAMPLE_DIR / f"{s.seed_table}_a.duckdb")
    complete_db = str(DATA_SAMPLE_DIR / f"{s.freshness_complete_seed_table}_a.duckdb")

    issue = check_missing_partition(complete_db, stale_db, "orders", s.source_a.sql, "a")

    assert issue is not None
    assert issue.category == "missing_partition"
    assert issue.source == "a"
    assert issue.confidence == "high"
    assert "2 row(s)" in issue.description
    assert "4" in issue.description
    assert issue.dollar_impact == s.known_gap == -250.0


def test_check_missing_partition_does_not_fire_when_row_counts_match(tmp_path):
    """Non-firing verification for check_missing_partition specifically,
    matching the standard check_stale_extract was already held to above --
    not assumed to transfer just because the two share a mechanism."""
    rows = [(1, 1, 100.0, "active", "2024-02-01"), (2, 1, 200.0, "active", "2024-03-01")]

    complete_path = str(tmp_path / "identical_complete.duckdb")
    stale_path = str(tmp_path / "identical_stale.duckdb")
    for path in (complete_path, stale_path):
        con = duckdb.connect(path)
        con.execute(
            "CREATE OR REPLACE TABLE orders "
            "(order_id INTEGER, customer_id INTEGER, amount DOUBLE, status VARCHAR, order_date DATE)"
        )
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", rows)
        con.close()

    result = check_missing_partition(complete_path, stale_path, "orders", "SELECT SUM(amount) FROM orders", "a")

    assert result is None


def test_check_missing_partition_fires_identically_on_case_8_cross_category_discrimination():
    """Cross-category discrimination check (Build 2, Day 3), required by
    this session's own brief: run check_missing_partition against Case 8's
    stale/complete pair -- a SCATTERED-rows fixture, not a contiguous
    partition. It fires (same mechanism as check_stale_extract, so it
    will), producing the identical row counts and dollar_impact
    check_stale_extract itself finds on Case 8, differing only in
    `category`. This is NOT a bug: it confirms check_stale_extract and
    check_missing_partition are mechanism-identical by design, and that
    the stale_extract/missing_partition distinction lives entirely in how
    a fixture's seed data is constructed, never in detection logic (see
    src/data_quality.py's module docstring). A future reader must not
    mistake "fires on both" for a defect."""
    s = CASE_8_STALE_EXTRACT
    stale_db = str(DATA_SAMPLE_DIR / f"{s.seed_table}_a.duckdb")
    complete_db = str(DATA_SAMPLE_DIR / f"{s.freshness_complete_seed_table}_a.duckdb")

    stale_issue = check_stale_extract(complete_db, stale_db, "orders", s.source_a.sql, "a")
    partition_issue = check_missing_partition(complete_db, stale_db, "orders", s.source_a.sql, "a")

    assert stale_issue is not None
    assert partition_issue is not None
    assert partition_issue.category == "missing_partition"
    assert stale_issue.category == "stale_extract"
    assert partition_issue.source == stale_issue.source
    assert partition_issue.confidence == stale_issue.confidence
    assert partition_issue.dollar_impact == stale_issue.dollar_impact
    assert partition_issue.description == stale_issue.description


def test_case_10_referential_integrity_fires_with_execution_derived_dollar_impact_matching_known_gap():
    """Case 10 is a single-cause fixture by design (no definitional or
    structural cause present) -- its dollar_impact must equal known_gap
    exactly, the same standard Case 8/9 and Cases 1-4/7 are held to. Both
    orders (fact) and customers (dimension) live in the same seed file
    (fact_db_path == dimension_db_path), Case 10's own shape."""
    s = CASE_10_REFERENTIAL_INTEGRITY
    db = str(DATA_SAMPLE_DIR / f"{s.seed_table}_a.duckdb")

    issue = check_referential_integrity(db, db, "orders", "customers", "customer_id", "customer_id", s.source_a.sql, "a")

    assert issue is not None
    assert issue.category == "referential_integrity"
    assert issue.source == "a"
    assert issue.confidence == "high"
    assert "1 row(s)" in issue.description
    assert issue.dollar_impact == s.known_gap == 300.0


def test_check_referential_integrity_does_not_fire_when_all_fks_resolve(tmp_path):
    """Non-firing verification, required by this session's own brief:
    construct a fact table whose FK values all resolve against the
    dimension table and confirm check_referential_integrity returns None
    rather than false-positiving."""
    path = str(tmp_path / "clean_fk.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE OR REPLACE TABLE customers (customer_id INTEGER, status VARCHAR, signup_date DATE)")
    con.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [(1, "active", "2024-01-01"), (2, "active", "2024-01-01")],
    )
    con.execute(
        "CREATE OR REPLACE TABLE orders "
        "(order_id INTEGER, customer_id INTEGER, amount DOUBLE, status VARCHAR, order_date DATE)"
    )
    con.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
        [(1, 1, 100.0, "active", "2024-02-01"), (2, 2, 200.0, "active", "2024-03-01")],
    )
    con.close()

    result = check_referential_integrity(
        path, path, "orders", "customers", "customer_id", "customer_id",
        "SELECT SUM(amount) FROM orders", "a",
    )

    assert result is None


def test_check_referential_integrity_does_not_fire_on_case_8_or_case_9_cross_category_discrimination():
    """Cross-category discrimination check (Build 2, Day 4), required by
    this session's own brief -- the inverse of Day 3's own discrimination
    test: run check_referential_integrity against Case 8 and Case 9's
    fact-only seed files (neither has a customers table of its own) using
    Case 10's committed customers dimension file as the external dimension
    source (every customer_id referenced in Case 8/9's orders is 1, and
    Case 10's customers table has both 1 and 2, so every FK resolves).
    Confirms check_referential_integrity returns None on both, proving it
    is a genuinely distinct mechanism from check_stale_extract/
    check_missing_partition -- those two WOULD fire on Case 8/9 (that is
    the whole point of those fixtures), but a relational-lookup check
    correctly finds nothing wrong with the same data, since nothing about
    Case 8/9's cause is a foreign-key problem."""
    dimension_db = str(DATA_SAMPLE_DIR / f"{CASE_10_REFERENTIAL_INTEGRITY.seed_table}_a.duckdb")

    for s in (CASE_8_STALE_EXTRACT, CASE_9_MISSING_PARTITION):
        fact_db = str(DATA_SAMPLE_DIR / f"{s.seed_table}_a.duckdb")
        result = check_referential_integrity(
            fact_db, dimension_db, "orders", "customers", "customer_id", "customer_id",
            s.source_a.sql, "a",
        )
        assert result is None, f"{s.scenario_id} unexpectedly flagged a referential-integrity issue"


def test_case_11_referential_integrity_source_b_dollar_impact_sign_flip_matches_known_gap():
    """Day 4 close-out: Case 10 alone never exercised check_referential_integrity's
    source="b" branch with real execution -- only source="a" had an
    executed proof point. Case 11 is Case 10's exact mirror image (orphan
    row on source_b instead of source_a) and proves, by real execution
    rather than by code inspection alone, that the dollar_impact sign
    correctly flips for a source="b" issue: dollar_impact must equal
    known_gap exactly (-300.0), the same standard every other single-cause
    fixture in this project is held to."""
    s = CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B
    db = str(DATA_SAMPLE_DIR / f"{s.seed_table}_b.duckdb")

    issue = check_referential_integrity(db, db, "orders", "customers", "customer_id", "customer_id", s.source_b.sql, "b")

    assert issue is not None
    assert issue.category == "referential_integrity"
    assert issue.source == "b"
    assert issue.confidence == "high"
    assert "1 row(s)" in issue.description
    assert issue.dollar_impact == s.known_gap == -300.0
