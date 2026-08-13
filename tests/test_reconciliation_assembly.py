"""Tests for src.reconciliation_assembly.assemble_reconciliation_line_items
(Build 1, Day 7, Task 1, Part B). Covers all three cause shapes it must
assemble: a clean single cause with no interaction (Case 1), a Shapley-
attributed interacting pair (Case 2), and self-consistency issues both
without (Case 4) and with (Case 7) a folded suppressed cross-source cause.

Every dollar_impact assertion below cross-checks two things: (1) the
Shapley/single-cause figures match Day 5 Task 2's already-committed raw
values, negated per the known_gap sign convention (Day 6 close-out); (2)
every line item's sign matches its scenario's known_gap sign, per this
task's own directional-consistency requirement.

Case 3 is deliberately NOT included here: assembling it exposed a real,
previously undiscovered gap (see test_case_3_raises_on_the_unaddressed_date_field_collision
below) -- sql_diff's date_field structural finding and definition_diff's
date_field definitional finding are the same underlying fact for Case 3,
with no suppression rule for that pairing anywhere in this codebase
(decision 10, docs/decisions.md, only covers the distinct/aggregation
collision). Feeding both into assemble_reconciliation_line_items without
resolving that first would silently double-count the cause -- the function
correctly raises instead. Building that suppression rule is new,
separately-scoped work (parallel to decision 10's own history), not
something this task grew to include."""

import pytest

from config import DATA_SAMPLE_DIR
from src.definition_diff import diff_definitions
from src.reconciliation_assembly import assemble_reconciliation_line_items
from src.self_consistency import (
    assemble_definitional_evidence_with_dollar_impacts,
    assemble_structural_and_definitional_evidence,
)
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.scenarios import (
    CASE_1_JOIN_TYPE,
    CASE_2_MULTI_CAUSE,
    CASE_3_HYBRID_FALLBACK,
    CASE_4_GOVERNANCE_DRIFT,
    CASE_7_PRECEDENCE_CONFLICT,
)


def _seed_paths(scenario):
    return (
        str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_a.duckdb"),
        str(DATA_SAMPLE_DIR / f"{scenario.seed_table}_b.duckdb"),
    )


def test_case_1_single_cause_join_type():
    """Case 1's lone join_type SQLStructuralDifference, no interaction, no
    self-consistency issues -- single_cause_attribution, sign-oriented to
    known_gap (+3400.0), so the +300.0 line item shares its sign."""
    db_a, db_b = _seed_paths(CASE_1_JOIN_TYPE)
    sql_diffs = diff_sql(parse_sql(CASE_1_JOIN_TYPE.source_a.sql), parse_sql(CASE_1_JOIN_TYPE.source_b.sql))
    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_1_JOIN_TYPE.source_a, CASE_1_JOIN_TYPE.source_b, db_a, db_b
    )
    sql_diffs, dd = assemble_structural_and_definitional_evidence(sql_diffs, dd)

    items = assemble_reconciliation_line_items(
        CASE_1_JOIN_TYPE.source_a, CASE_1_JOIN_TYPE.source_b, db_a, sql_diffs, dd, sci
    )

    assert len(items) == 1
    assert items[0].computed_by == "single_cause_attribution"
    assert items[0].dollar_impact == 300.0
    assert items[0].dollar_impact > 0
    assert CASE_1_JOIN_TYPE.known_gap > 0  # same sign


def test_case_2_shapley_interacting_pair():
    """Case 2's two genuinely interacting definitional causes
    (excluded_statuses, aggregation), Shapley-attributed and negated per
    the known_gap convention -- reproduces Day 5 Task 2's committed raw
    figures (-2.5, +1.5) with the sign flipped (+2.5, -1.5)."""
    db_a, db_b = _seed_paths(CASE_2_MULTI_CAUSE)
    sql_diffs = diff_sql(parse_sql(CASE_2_MULTI_CAUSE.source_a.sql), parse_sql(CASE_2_MULTI_CAUSE.source_b.sql))
    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_2_MULTI_CAUSE.source_a, CASE_2_MULTI_CAUSE.source_b, db_a, db_b
    )
    sql_diffs, dd = assemble_structural_and_definitional_evidence(sql_diffs, dd)
    assert {d.field for d in dd} == {"excluded_statuses", "aggregation"}

    excluded_statuses_diff = next(d for d in dd if d.field == "excluded_statuses")
    aggregation_diff = next(d for d in dd if d.field == "aggregation")

    items = assemble_reconciliation_line_items(
        CASE_2_MULTI_CAUSE.source_a,
        CASE_2_MULTI_CAUSE.source_b,
        db_a,
        sql_diffs,
        dd,
        sci,
        interacting_pairs=[(excluded_statuses_diff, aggregation_diff)],
    )

    assert len(items) == 2
    excluded_item = next(item for item in items if "excluded_statuses" in item.cause)
    aggregation_item = next(item for item in items if "aggregation" in item.cause)

    assert excluded_item.computed_by == "shapley_pair_attribution"
    assert excluded_item.dollar_impact == 2.5  # -(-2.5)
    assert aggregation_item.computed_by == "shapley_pair_attribution"
    assert aggregation_item.dollar_impact == -1.5  # -(1.5)
    assert sum(item.dollar_impact for item in items) == 1.0  # -(4 - 5), same sign as known_gap (+1250.0)
    assert CASE_2_MULTI_CAUSE.known_gap > 0


def test_case_3_raises_on_the_unaddressed_date_field_collision():
    """A genuine, previously undiscovered gap surfaced while building this
    module: sql_diff's date_field SQLStructuralDifference and
    definition_diff's date_field DefinitionDifference trace to the exact
    same fact for Case 3 (both describe order_date vs. created_at), and no
    suppression rule for this pairing exists (decision 10 only covers
    distinct/aggregation). Feeding both in without resolving that first
    must raise -- NOT silently double-count the cause by guessing a target
    value for the structural finding."""
    db_a, db_b = _seed_paths(CASE_3_HYBRID_FALLBACK)
    sql_diffs = diff_sql(
        parse_sql(CASE_3_HYBRID_FALLBACK.source_a.sql), parse_sql(CASE_3_HYBRID_FALLBACK.source_b.sql)
    )
    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_3_HYBRID_FALLBACK.source_a, CASE_3_HYBRID_FALLBACK.source_b, db_a, db_b
    )
    assert "date_field" in {d.category for d in sql_diffs}
    assert "date_field" in {d.field for d in dd}

    date_field_diff = next(d for d in dd if d.field == "date_field")
    excluded_statuses_diff = next(d for d in dd if d.field == "excluded_statuses")

    with pytest.raises(ValueError, match="date_field"):
        assemble_reconciliation_line_items(
            CASE_3_HYBRID_FALLBACK.source_a,
            CASE_3_HYBRID_FALLBACK.source_b,
            db_a,
            sql_diffs,  # deliberately NOT excluding the colliding structural finding
            dd,
            sci,
            interacting_pairs=[(date_field_diff, excluded_statuses_diff)],
        )


def test_case_4_self_consistency_only():
    """Case 4 has a self-consistency issue with no suppressed cross-source
    counterpart (Day 7 Part A) -- one line item, computed_by reflects that
    there was nothing to fold."""
    db_a, db_b = _seed_paths(CASE_4_GOVERNANCE_DRIFT)
    sql_diffs = diff_sql(
        parse_sql(CASE_4_GOVERNANCE_DRIFT.source_a.sql), parse_sql(CASE_4_GOVERNANCE_DRIFT.source_b.sql)
    )
    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_4_GOVERNANCE_DRIFT.source_a, CASE_4_GOVERNANCE_DRIFT.source_b, db_a, db_b
    )
    sql_diffs, dd = assemble_structural_and_definitional_evidence(sql_diffs, dd)
    assert dd == []

    items = assemble_reconciliation_line_items(
        CASE_4_GOVERNANCE_DRIFT.source_a, CASE_4_GOVERNANCE_DRIFT.source_b, db_a, sql_diffs, dd, sci
    )

    assert len(items) == 1
    assert items[0].computed_by == "self_consistency_correction"
    assert items[0].dollar_impact == 200.0
    assert CASE_4_GOVERNANCE_DRIFT.known_gap > 0  # same sign


def test_case_7_self_consistency_plus_suppressed_cross_source():
    """Case 7's self-consistency issue DOES have a suppressed cross-source
    counterpart (Day 7 Part A) -- one line item, but computed_by must
    reflect the combined mechanism, and its dollar_impact must equal
    known_gap exactly (excluded_statuses is Case 7's only differing field)."""
    db_a, db_b = _seed_paths(CASE_7_PRECEDENCE_CONFLICT)
    sql_diffs = diff_sql(
        parse_sql(CASE_7_PRECEDENCE_CONFLICT.source_a.sql), parse_sql(CASE_7_PRECEDENCE_CONFLICT.source_b.sql)
    )
    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_7_PRECEDENCE_CONFLICT.source_a, CASE_7_PRECEDENCE_CONFLICT.source_b, db_a, db_b
    )
    sql_diffs, dd = assemble_structural_and_definitional_evidence(sql_diffs, dd)
    assert dd == []  # suppressed from the reported findings list

    items = assemble_reconciliation_line_items(
        CASE_7_PRECEDENCE_CONFLICT.source_a, CASE_7_PRECEDENCE_CONFLICT.source_b, db_a, sql_diffs, dd, sci
    )

    assert len(items) == 1
    assert items[0].computed_by == "self_consistency_correction+suppressed_cross_source"
    assert items[0].dollar_impact == 200.0
    assert items[0].dollar_impact == CASE_7_PRECEDENCE_CONFLICT.known_gap
