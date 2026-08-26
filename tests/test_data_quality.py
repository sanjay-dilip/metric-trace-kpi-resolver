"""Tests for src.data_quality (Build 2, Day 2): the stale-extract
completeness check. Proves both directions -- that it fires correctly on
a real, designed-to-fire fixture (Case 8), and that it does NOT
false-positive on two snapshots with identical row counts -- the same
non-firing standard Day 4/Day 5's precedence-rule verification (Case 7,
decision 10) was held to."""

import duckdb

from config import DATA_SAMPLE_DIR
from src.data_quality import check_stale_extract
from tests.fixtures.scenarios import CASE_8_STALE_EXTRACT


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
