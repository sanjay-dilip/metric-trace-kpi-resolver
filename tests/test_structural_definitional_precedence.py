"""Tests for src.self_consistency.assemble_structural_and_definitional_evidence
(Build 1, Day 5, Task 1b): implements decision 10 (docs/decisions.md) --
sql_diff's `distinct` finding is suppressed in favor of definition_diff's
`aggregation` finding when both trace to the same underlying COUNT/COUNT
DISTINCT fact. A Case 2 audit found decision 10 had been recorded in the
decision log with no enforcing code anywhere; this closes that gap.

Also covers decision 12 (Build 1, Day 7, Task 1b): the same class of
collision, discovered a second time -- sql_diff's `date_field` finding and
definition_diff's `date_field` finding both trace to the same underlying
date-column swap for Case 3 (order_date vs. created_at), with no
suppression rule reconciling them before this task. Resolved the same way
decision 10 was, by the same function, with the same "same underlying
fact" precision standard (_same_date_field_fact, side-matched exact
equality, not mere category co-presence).

Also covers Build 3, Day 1, Part 6's filter/excluded_statuses rule (the
third collision this function resolves, and the first one resolved in
the OPPOSITE direction from decisions 10/12): sql_diff's `filter` finding
and definition_diff's `excluded_statuses` finding both trace to the same
status column for Case 13, but only when excluded_statuses' confidence is
exactly "low" -- here the DefinitionDifference is suppressed and the
SQLStructuralDifference survives, since a "low"-confidence inferred value
is a guess while filter's presence/absence is mechanically certain.
Proven against the real Case 13 collision and two over-fire cases (a
medium/high-confidence excluded_statuses finding on the same column; a
filter finding on an unrelated column paired with a genuine low-confidence
excluded_statuses finding), the same standard decisions 10/12 were each
held to."""

import pytest

from src.definition_diff import diff_definitions
from src.schema import DefinitionDifference, SQLStructuralDifference
from src.self_consistency import assemble_structural_and_definitional_evidence
from src.scenario import DashboardSource, DeclaredDefinition
from src.sql_diff import diff_sql
from src.sql_parser import parse_sql
from tests.fixtures.scenarios import (
    CASE_2_MULTI_CAUSE,
    CASE_3_HYBRID_FALLBACK,
    CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION,
    CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES,
    CASE_15_DATE_FIELD_INFERRED_ONLY,
    CASE_17_JOIN_TYPE_EXCLUDED_STATUSES_INTERACTING,
    CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED,
    SCENARIOS,
)


def test_case_2_distinct_suppressed_aggregation_and_excluded_statuses_survive():
    """The real collision: A is COUNT(DISTINCT customer_id), B is
    COUNT(customer_id). sql_diff fires `distinct`, definition_diff fires
    `aggregation` (count_distinct vs count) for the same fact -- `distinct`
    must be removed, `aggregation` must survive untouched, and the unrelated
    `excluded_statuses` finding must pass through regardless."""
    sql_diffs = diff_sql(
        parse_sql(CASE_2_MULTI_CAUSE.source_a.sql), parse_sql(CASE_2_MULTI_CAUSE.source_b.sql)
    )
    def_diffs = diff_definitions(CASE_2_MULTI_CAUSE.source_a, CASE_2_MULTI_CAUSE.source_b)

    assert "distinct" in {d.category for d in sql_diffs}
    assert {d.field for d in def_diffs} == {"excluded_statuses", "aggregation"}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "distinct" not in {d.category for d in sql_after}
    assert {d.field for d in def_after} == {"excluded_statuses", "aggregation"}


def test_does_not_over_fire_on_unrelated_distinct_and_aggregation_findings():
    """A distinct-category finding and an aggregation-category finding can
    co-occur without tracing to the SAME fact decision 10 covers: A is
    COUNT(DISTINCT customer_id), B is MAX(customer_id) -- a genuinely
    different aggregation function, not just "the same function without
    DISTINCT". Decision 10's own suppression (distinct-category structural
    vs. aggregation-category DEFINITIONAL) must not fire here; 'max' does
    not satisfy _same_count_distinct_fact's suffix relationship against
    'count_distinct', so `distinct` survives.

    Build 3, Day 5, Part 2 update: this fixture ALSO exercises decision 26
    (the aggregation-category STRUCTURAL vs. aggregation-category
    DEFINITIONAL pairing, a separate rule from decision 10's above) --
    COUNT vs. MAX is a genuine same-fact aggregation collision by decision
    26's own definition, so as of that rule's introduction `aggregation`
    IS correctly suppressed here, unlike `distinct`. This test's own
    assertions never checked the `aggregation` category before this
    update (only `distinct` and definitional equality), so decision 26's
    new behavior didn't fail silently -- it just wasn't covered. Now
    explicitly asserted, so this fixture proves BOTH: decision 10 does
    NOT over-fire, and decision 26 correctly DOES fire, on the exact same
    real SQL pair."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT COUNT(DISTINCT customer_id) AS x FROM customers "
            "WHERE status != 'churned' AND signup_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date", excluded_statuses=["churned"], aggregation="count_distinct"
        ),
    )
    source_b = DashboardSource(
        label="finance_query",
        sql=(
            "SELECT MAX(customer_id) AS x FROM customers "
            "WHERE status != 'churned' AND signup_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date", excluded_statuses=["churned"], aggregation="max"
        ),
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert {d.category for d in sql_diffs} == {"distinct", "aggregation"}
    assert "aggregation" in {d.field for d in def_diffs}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert {d.category for d in sql_after} == {"distinct"}  # aggregation suppressed by decision 26
    assert def_after == def_diffs


def test_case_3_date_field_suppressed_excluded_statuses_survives():
    """The real collision (decision 12): Case 3's A uses order_date, B uses
    created_at -- sql_diff fires `date_field` structurally, definition_diff
    fires `date_field` definitionally (inferred, since B has no declared
    definition), both describing the exact same column swap. `date_field`
    must be removed from sql_differences; definition_differences (both
    date_field and the unrelated excluded_statuses finding) must survive
    untouched."""
    sql_diffs = diff_sql(parse_sql(CASE_3_HYBRID_FALLBACK.source_a.sql), parse_sql(CASE_3_HYBRID_FALLBACK.source_b.sql))
    def_diffs = diff_definitions(CASE_3_HYBRID_FALLBACK.source_a, CASE_3_HYBRID_FALLBACK.source_b)

    assert "date_field" in {d.category for d in sql_diffs}
    assert {d.field for d in def_diffs} == {"date_field", "excluded_statuses"}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "date_field" not in {d.category for d in sql_after}
    assert {d.field for d in def_after} == {"date_field", "excluded_statuses"}


def test_does_not_over_fire_on_unrelated_date_field_findings():
    """A date_field structural finding and a date_field definitional
    finding can co-occur without tracing to the same fact: A's WHERE clause
    references TWO date-like columns (order_date, updated_at -- a
    genuinely ambiguous structural finding, not one clean column swap),
    while A/B's DECLARED date_field values differ on a completely
    unrelated pair (order_date vs. ship_date). Both sides are self-
    consistent (declared values match their own SQL's unambiguous or
    best-guess implementation), so Day 4's precedence rule does not
    interfere -- this isolates decision 12's own suppression logic.
    Suppression must not fire; both findings are independently real."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND updated_at <= '2024-06-01'"
        ),
        declared_definition=DeclaredDefinition(date_field="order_date", excluded_statuses=[], aggregation="sum"),
    )
    source_b = DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE ship_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(date_field="ship_date", excluded_statuses=[], aggregation="sum"),
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert "date_field" in {d.category for d in sql_diffs}
    assert "date_field" in {d.field for d in def_diffs}

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert "date_field" in {d.category for d in sql_after}
    assert def_after == def_diffs


def test_no_regressions_across_scenarios():
    """Case 2 (decision 10, distinct/aggregation), Case 3 (decision 12,
    date_field/date_field), Case 15 (Build 3, Day 3, Part 7 -- the same
    date_field/date_field collision as Case 3, but inferred-vs-inferred
    rather than declared-vs-inferred), and Case 19 (Build 3, Day 3, Part 9
    -- the same collision again, this time both sides declared at
    confidence="high", the first technical-scenario proof of decision 12
    at that confidence level) are the only fixtures in SCENARIOS affected
    by this rule; every other fixture's sql_differences/
    definition_differences must pass through unchanged. Renamed from
    "..._across_all_7_fixtures": SCENARIOS has grown well past 7 since
    this test was first written, and the old name was stale."""
    affected = {
        "case_02_multi_cause": ["distinct"],
        "case_03_hybrid_fallback": ["date_field"],
        "case_15_date_field_inferred_only": ["date_field"],
        "case_19_date_field_excluded_statuses_declared": ["date_field"],
    }
    for scenario in SCENARIOS:
        sql_diffs = diff_sql(parse_sql(scenario.source_a.sql), parse_sql(scenario.source_b.sql))
        def_diffs = diff_definitions(scenario.source_a, scenario.source_b)

        sql_after, def_after = assemble_structural_and_definitional_evidence(
            sql_diffs, def_diffs
        )

        suppressed_categories = affected.get(scenario.scenario_id, [])
        expected_categories = [d.category for d in sql_diffs if d.category not in suppressed_categories]
        assert [d.category for d in sql_after] == expected_categories
        assert [d.field for d in def_after] == [d.field for d in def_diffs]


def test_case_13_filter_suppresses_low_confidence_excluded_statuses():
    """The real collision (Build 3, Day 1, Part 6): source_a filters status
    via a real NOT IN exclusion, source_b has no status filter at all --
    sql_diff fires `filter`, definition_diff fires `excluded_statuses`
    (inferred, confidence="low" on source_b's zero-status-filter side),
    both describing the same fact. `excluded_statuses` must be removed
    from definition_differences -- the REVERSE of decisions 10/12's own
    direction -- and `filter` must survive untouched in sql_differences."""
    s = CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION
    sql_diffs = diff_sql(parse_sql(s.source_a.sql), parse_sql(s.source_b.sql))
    def_diffs = diff_definitions(s.source_a, s.source_b)

    assert {d.category for d in sql_diffs} == {"filter"}
    assert [(d.field, d.confidence) for d in def_diffs] == [("excluded_statuses", "low")]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert {d.category for d in sql_after} == {"filter"}
    assert def_after == []


@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_medium_or_high_confidence_excluded_statuses_now_suppresses_filter_instead(confidence):
    """Build 3, Day 2, Part 15, decision 22: the confidence gate that
    previously made medium/high confidence a no-op (both findings
    survive, Build 3 Day 1 Part 6's original scope boundary) is REMOVED.
    A filter finding and an excluded_statuses finding on the same column,
    at confidence="medium"/"high" (a real, non-empty exclusion set the
    inference or declaration is genuinely confident about), now suppress
    in the OPPOSITE direction from the low-confidence case: the
    DefinitionDifference survives, the SQLStructuralDifference is
    removed. This is the direct, real-collision-motivated fix for the
    Build 3 Day 2 Part 14 finding (decision 21) that this exact pairing,
    left unsuppressed, crashes the Shapley-pair engine outright.
    Deliberately constructed directly (not run through diff_definitions),
    same reasoning as before this rename: neither declared-vs-declared
    nor the inferred path naturally produces medium/high confidence
    paired with an unfiltered other side the way this rule needs to be
    stress-tested in isolation."""
    filter_finding = SQLStructuralDifference(
        category="filter",
        description="source_a filters on 'status', source_b's query has no equivalent filter on 'status'",
        query_a_snippet="NOT status IN ('churned')",
        query_b_snippet="(no filter on this column)",
    )
    excluded_statuses_finding = DefinitionDifference(
        field="excluded_statuses",
        source_a_value="churned",
        source_b_value="(none)",
        source="declared" if confidence == "high" else "inferred",
        confidence=confidence,
    )

    sql_after, def_after = assemble_structural_and_definitional_evidence(
        [filter_finding], [excluded_statuses_finding]
    )

    assert sql_after == []
    assert def_after == [excluded_statuses_finding]


def test_does_not_over_fire_on_unrelated_filter_column():
    """Second required over-fire proof: a filter finding on a column OTHER
    than status, paired with a genuine low-confidence excluded_statuses
    finding -- suppression must not fire, proving the "same fact" check is
    a real column-identity check, not just category co-presence (the same
    precision standard _same_date_field_fact's own over-fire test was held
    to for decision 12)."""
    unrelated_filter_finding = SQLStructuralDifference(
        category="filter",
        description="source_a filters on 'region', source_b's query has no equivalent filter on 'region'",
        query_a_snippet="region = 'us'",
        query_b_snippet="(no filter on this column)",
    )
    excluded_statuses_finding = DefinitionDifference(
        field="excluded_statuses",
        source_a_value="churned",
        source_b_value="(none)",
        source="inferred",
        confidence="low",
    )

    sql_after, def_after = assemble_structural_and_definitional_evidence(
        [unrelated_filter_finding], [excluded_statuses_finding]
    )

    assert sql_after == [unrelated_filter_finding]
    assert def_after == [excluded_statuses_finding]


@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_does_not_over_fire_on_unrelated_filter_column_at_medium_or_high_confidence(confidence):
    """Same over-fire proof as directly above, but at the confidence
    levels Part 15 newly suppresses in the OPPOSITE direction -- the
    "same fact" column-identity check must still correctly refuse to
    match an unrelated column regardless of which direction confidence
    would otherwise route the suppression, proving the confidence-gate
    removal (decision 22) did not loosen the column check itself."""
    unrelated_filter_finding = SQLStructuralDifference(
        category="filter",
        description="source_a filters on 'region', source_b's query has no equivalent filter on 'region'",
        query_a_snippet="region = 'us'",
        query_b_snippet="(no filter on this column)",
    )
    excluded_statuses_finding = DefinitionDifference(
        field="excluded_statuses",
        source_a_value="churned",
        source_b_value="(none)",
        source="declared" if confidence == "high" else "inferred",
        confidence=confidence,
    )

    sql_after, def_after = assemble_structural_and_definitional_evidence(
        [unrelated_filter_finding], [excluded_statuses_finding]
    )

    assert sql_after == [unrelated_filter_finding]
    assert def_after == [excluded_statuses_finding]


def test_case_14_both_sides_filtering_status_produces_no_filter_finding_at_all():
    """Case 14 (Build 3, Day 3, Part 6): the deliberate opposite of Case
    13's construction -- BOTH sides have a real status predicate
    (source_a: NOT IN exclusion; source_b: an inclusion-style '=' the
    inference layer doesn't recognize). src.sql_diff._diff_filters is
    presence-only, so this must never produce a `filter`
    SQLStructuralDifference at all -- confirmed here directly against the
    raw sql_diff output, before any suppression rule even runs. This is
    what keeps decisions 18/22's filter/excluded_statuses rule from ever
    having anything to fire against for this fixture."""
    s = CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES
    sql_diffs = diff_sql(parse_sql(s.source_a.sql), parse_sql(s.source_b.sql))

    assert "filter" not in {d.category for d in sql_diffs}


def test_case_14_date_field_suppressed_by_decision_12_leaving_exactly_two_causes():
    """The real collision this fixture DOES hit: source_a's declared
    'order_date' and source_b's inferred 'created_at' (medium confidence)
    trace to the same date-column swap sql_diff's own `date_field` finding
    describes -- decision 12's existing suppression (already proven at
    confidence="medium", the same level this fixture reaches) removes the
    redundant structural finding, leaving exactly definition_differences =
    [date_field, excluded_statuses] and sql_differences = [] -- the 2-cause
    shape this fixture's whole design depends on to reach the Shapley-pair
    branch rather than the 1-cause or 3+-cause ones."""
    s = CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES
    sql_diffs = diff_sql(parse_sql(s.source_a.sql), parse_sql(s.source_b.sql))
    def_diffs = diff_definitions(s.source_a, s.source_b)

    assert {d.category for d in sql_diffs} == {"date_field"}
    assert [(d.field, d.confidence) for d in def_diffs] == [
        ("date_field", "medium"),
        ("excluded_statuses", "low"),
    ]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == []
    assert [d.field for d in def_after] == ["date_field", "excluded_statuses"]


def test_case_15_date_field_suppressed_leaving_exactly_one_cause():
    """Case 15 (Build 3, Day 3, Part 7 -- finalized 8-scenario list, item
    1): both sides undeclared, so date_field is inferred-vs-inferred, not
    declared-vs-inferred like Case 3/14. Neither side filters status or
    diverges on aggregation, so excluded_statuses/aggregation never
    differ -- date_field is the ONLY raw definitional finding, and its
    sql_diff structural counterpart (same order_date/ship_date swap) is
    suppressed by decision 12 at the same already-proven confidence="medium"
    level Case 3/14 exercise. This is what keeps Case 15 a genuine
    single-cause fixture (1 remaining cause -> single_cause_attribution),
    not an accidental multi-cause one."""
    s = CASE_15_DATE_FIELD_INFERRED_ONLY
    sql_diffs = diff_sql(parse_sql(s.source_a.sql), parse_sql(s.source_b.sql))
    def_diffs = diff_definitions(s.source_a, s.source_b)

    assert {d.category for d in sql_diffs} == {"date_field"}
    assert [(d.field, d.confidence, d.source) for d in def_diffs] == [("date_field", "medium", "inferred")]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == []
    assert [d.field for d in def_after] == ["date_field"]


def test_case_17_join_type_and_excluded_statuses_do_not_collide():
    """Case 17 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    3): join_type + excluded_statuses is NOT one of the pairs any
    suppression rule in this module covers (only distinct/aggregation,
    date_field/date_field, and filter/excluded_statuses are). Both
    findings must survive assemble_structural_and_definitional_evidence
    completely unchanged -- proving this genuinely new pairing correctly
    falls through every existing rule rather than accidentally matching
    one of them."""
    s = CASE_17_JOIN_TYPE_EXCLUDED_STATUSES_INTERACTING
    sql_diffs = diff_sql(parse_sql(s.source_a.sql), parse_sql(s.source_b.sql))
    def_diffs = diff_definitions(s.source_a, s.source_b)

    assert [d.category for d in sql_diffs] == ["join_type"]
    assert [(d.field, d.confidence) for d in def_diffs] == [("excluded_statuses", "high")]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == sql_diffs
    assert def_after == def_diffs


def test_case_19_date_field_suppressed_at_high_confidence():
    """Case 19 (Build 3, Day 3, Part 9 -- finalized 8-scenario list, item
    5): the first purely TECHNICAL fixture to prove decision 12's
    date_field suppression at confidence="high" (both sides declared) --
    every prior committed technical proof (Case 3, Case 14, Case 15) was
    at "medium". AMBIGUOUS_REVENUE_RECOGNITION already proved "high" but
    as an ambiguous scenario, not a technical one."""
    s = CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED
    sql_diffs = diff_sql(parse_sql(s.source_a.sql), parse_sql(s.source_b.sql))
    def_diffs = diff_definitions(s.source_a, s.source_b)

    assert {d.category for d in sql_diffs} == {"date_field"}
    assert [(d.field, d.confidence) for d in def_diffs] == [("date_field", "high"), ("excluded_statuses", "high")]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == []
    assert [d.field for d in def_after] == ["date_field", "excluded_statuses"]


# --- Build 3, Day 5, Part 2: decision 26's own resolution -- sql_diff's ---
# --- aggregation-category finding vs. definition_diff's aggregation-field ---
# --- finding, both tracing to the same underlying function-pair fact. ---
# --- None of the 21 committed Scenario objects (SCENARIOS plus the ---
# --- proof-only exclusions) has an aggregation-category ---
# --- SQLStructuralDifference at all (confirmed by direct sweep before ---
# --- writing these tests) -- decision 26's own original collision was ---
# --- found by a standalone probe (Build 3, Day 3, Part 4), never a ---
# --- committed fixture, so every test below builds its own DashboardSource ---
# --- pair directly, matching this module's own existing precedent for an ---
# --- inline (non-Scenario) construction. ---


def test_real_aggregation_function_collision_is_suppressed():
    """The real collision decision 26 names directly: a plain SUM-vs-COUNT
    aggregation-function difference, both sides declared. sql_diff fires
    `aggregation` (SUM(amount) vs COUNT(amount)); definition_diff fires
    `aggregation` ('sum' vs 'count') for the same fact. Per decision 26's
    locked direction, the structural finding is suppressed and the
    definitional finding survives -- flat, unconditional on confidence,
    matching decisions 10/12's own default direction rather than decision
    22's confidence-dependent reversal."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql="SELECT SUM(amount) AS x FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(
            date_field="order_date", excluded_statuses=["cancelled"], aggregation="sum"
        ),
    )
    source_b = DashboardSource(
        label="dashboard_b",
        sql="SELECT COUNT(amount) AS x FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(
            date_field="order_date", excluded_statuses=["cancelled"], aggregation="count"
        ),
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert [d.category for d in sql_diffs] == ["aggregation"]
    assert [(d.field, d.source_a_value, d.source_b_value) for d in def_diffs] == [("aggregation", "sum", "count")]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == []
    assert def_after == def_diffs


def test_aggregation_suppression_does_not_over_fire_on_unrelated_declared_values():
    """Over-fire case 1 (required, same standard as every prior suppression
    rule): sql_diff flags a real SUM-vs-COUNT function pair, but the
    definitional finding describes a genuinely DIFFERENT, unrelated pair
    ('avg' vs 'min' -- a declared value need not match the real SQL at
    all, which is exactly what a self-consistency check would separately
    catch). _same_aggregation_function_fact must refuse to match a
    structural fact against a definitional finding that doesn't actually
    describe it -- suppression must not fire, and both findings survive
    independently."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql="SELECT SUM(amount) AS x FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(
            date_field="order_date", excluded_statuses=["cancelled"], aggregation="avg"
        ),
    )
    source_b = DashboardSource(
        label="dashboard_b",
        sql="SELECT COUNT(amount) AS x FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(
            date_field="order_date", excluded_statuses=["cancelled"], aggregation="min"
        ),
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert [d.category for d in sql_diffs] == ["aggregation"]
    assert [(d.field, d.source_a_value, d.source_b_value) for d in def_diffs] == [("aggregation", "avg", "min")]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == sql_diffs
    assert def_after == def_diffs


def test_aggregation_fallback_surfaces_sql_finding_when_no_declaration_covers_it():
    """Over-fire case 2 / the fallback branch (required, stated explicitly
    in the implementation, not an accidental fallthrough): neither side
    declares a definition, and inference on the more-than-one-aggregate
    side produces a low-confidence "(ambiguous)" value that never matches
    any real function name. sql_diff's genuine `aggregation` finding
    (SUM+COUNT on one side vs. a single COUNT on the other -- a differing
    NUMBER of aggregate calls, not a same-position function swap) must
    survive completely unsuppressed -- this must not collapse to "no
    finding at all"."""
    source_a = DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount), COUNT(amount) AS x FROM orders "
            "WHERE status != 'cancelled' AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    )
    source_b = DashboardSource(
        label="dashboard_b",
        sql="SELECT COUNT(amount) AS x FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=None,
    )

    sql_diffs = diff_sql(parse_sql(source_a.sql), parse_sql(source_b.sql))
    def_diffs = diff_definitions(source_a, source_b)
    assert [d.category for d in sql_diffs] == ["aggregation"]
    assert [(d.field, d.source_a_value, d.confidence) for d in def_diffs] == [
        ("aggregation", "(ambiguous)", "low")
    ]

    sql_after, def_after = assemble_structural_and_definitional_evidence(sql_diffs, def_diffs)

    assert sql_after == sql_diffs
    assert def_after == def_diffs
