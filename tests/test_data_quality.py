"""Tests for src.data_quality: the stale-extract and missing-partition
completeness checks (Build 2, Days 2-3). Proves, for each: that it fires
correctly on a real, designed-to-fire fixture (Case 8 for stale_extract,
Case 9 for missing_partition), and that it does NOT false-positive on two
snapshots with identical row counts -- the same non-firing standard
Day 4/Day 5's precedence-rule verification (Case 7, decision 10) was held
to. Also proves (Day 3) that check_missing_partition and
check_stale_extract are mechanism-identical by design: check_missing_partition
fires on Case 8's scattered-row data exactly like check_stale_extract does,
since the category distinction lives entirely in fixture construction, not
in either function's logic (src/data_quality.py's module docstring)."""

import duckdb

from config import DATA_SAMPLE_DIR
from src.data_quality import check_missing_partition, check_stale_extract
from tests.fixtures.scenarios import CASE_8_STALE_EXTRACT, CASE_9_MISSING_PARTITION


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
