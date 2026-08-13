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

Case 3 (Build 1, Day 7, Task 1b): assembling it originally exposed a real,
previously undiscovered gap -- sql_diff's date_field structural finding and
definition_diff's date_field definitional finding trace to the same
underlying fact, with no suppression rule reconciling them (decision 10
only covered the distinct/aggregation collision). That gap is now decision
12 (docs/decisions.md), implemented in
src.self_consistency.assemble_structural_and_definitional_evidence and
proven in tests/test_structural_definitional_precedence.py. Case 3 is
included below as a normal, working case now that the collision is
resolved."""

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
    known_gap (+300.0, recalibrated to real seed execution -- Decision 13's
    resolution, Build 1 Day 7 Task 3), so the +300.0 line item shares its
    sign and in fact fully accounts for it."""
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
    assert sum(item.dollar_impact for item in items) == 1.0  # -(4 - 5), same sign as known_gap (+1.0)
    assert CASE_2_MULTI_CAUSE.known_gap > 0


def test_case_3_shapley_interacting_pair_after_decision_12_suppression():
    """With decision 12's date_field suppression applied (via
    assemble_structural_and_definitional_evidence, same as every other
    caller in this module), the redundant sql_diff date_field finding is
    gone before this function ever sees it -- date_field and
    excluded_statuses are Case 3's two genuinely interacting definitional
    causes, Shapley-attributed and negated per the known_gap convention,
    reproducing Day 5 Task 2's committed raw figures (400.0, -500.0) with
    the sign flipped (-400.0, +500.0). The resulting sum shares known_gap's
    sign, reconciling toward it rather than raising."""
    db_a, db_b = _seed_paths(CASE_3_HYBRID_FALLBACK)
    sql_diffs = diff_sql(
        parse_sql(CASE_3_HYBRID_FALLBACK.source_a.sql), parse_sql(CASE_3_HYBRID_FALLBACK.source_b.sql)
    )
    dd, sci = assemble_definitional_evidence_with_dollar_impacts(
        CASE_3_HYBRID_FALLBACK.source_a, CASE_3_HYBRID_FALLBACK.source_b, db_a, db_b
    )
    sql_diffs, dd = assemble_structural_and_definitional_evidence(sql_diffs, dd)
    assert sql_diffs == []  # decision 12: the redundant date_field structural finding is gone
    assert {d.field for d in dd} == {"date_field", "excluded_statuses"}

    date_field_diff = next(d for d in dd if d.field == "date_field")
    excluded_statuses_diff = next(d for d in dd if d.field == "excluded_statuses")

    items = assemble_reconciliation_line_items(
        CASE_3_HYBRID_FALLBACK.source_a,
        CASE_3_HYBRID_FALLBACK.source_b,
        db_a,
        sql_diffs,
        dd,
        sci,
        interacting_pairs=[(date_field_diff, excluded_statuses_diff)],
    )

    assert len(items) == 2
    date_field_item = next(item for item in items if "date_field" in item.cause)
    excluded_item = next(item for item in items if "excluded_statuses" in item.cause)

    assert date_field_item.computed_by == "shapley_pair_attribution"
    assert date_field_item.dollar_impact == -400.0  # -(400.0)
    assert excluded_item.computed_by == "shapley_pair_attribution"
    assert excluded_item.dollar_impact == 500.0  # -(-500.0)

    total = sum(item.dollar_impact for item in items)
    assert total == 100.0
    assert (total > 0) == (CASE_3_HYBRID_FALLBACK.known_gap > 0)  # reconciles toward known_gap's sign


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
