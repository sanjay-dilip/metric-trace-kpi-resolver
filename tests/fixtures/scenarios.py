"""The 7 hand-designed test scenarios used to build and validate Build 1's
deterministic tools (Days 2-6). Each is a committed Scenario instance, not a
throwaway object — SQL is realistic enough for sqlglot to parse and for the
intended structural/definitional difference to actually be present in the
SQL text, per each case's design."""

from src.scenario import DeclaredDefinition, DashboardSource, Scenario

# Case 1: Clean single-cause, both declared identically, join-type difference.
# A's LEFT JOIN keeps orders with no matching customer row; B's INNER JOIN drops
# them. Same declared definition on both sides, so the only cause is structural.
# Deliberately given no _DATA_QUALITY_DISPATCH entry (reconciliation_assembly.py):
# this fixture's own orphan FK row (order_id=3 -> customer_id=99, absent from
# customers) would make check_referential_integrity fire with a dollar figure
# identical to this scenario's own join_type finding, reproducing decision 17's
# legibility risk (docs/decisions.md) on a live benchmark fixture rather than
# only on Case 12's dedicated collision-proof fixture. Leave undispatched.
CASE_1_JOIN_TYPE = Scenario(
    scenario_id="case_01_join_type",
    description="Clean single-cause: identical declared definitions, LEFT vs INNER join drops unmatched orders on B's side.",
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(o.amount) AS revenue FROM orders o "
            "LEFT JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.order_date >= '2024-01-01' AND o.status NOT IN ('cancelled', 'refunded')"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(o.amount) AS revenue FROM orders o "
            "INNER JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.order_date >= '2024-01-01' AND o.status NOT IN ('cancelled', 'refunded')"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    # reported_value_a/b are calibrated to real execution against this scenario's
    # own seed data (case_01_join_type_a/b.duckdb), per Decision 13's resolution
    # (docs/decisions.md, Build 1 Day 7 Task 3) -- not hand-typed at dashboard
    # scale, matching Case 7's original standard, now the convention for all 7.
    reported_value_a=600.0,
    reported_value_b=300.0,
    known_gap=600.0 - 300.0,
    seed_table="case_01_join_type",
)

# Case 2: Multi-cause, both declared, definitions genuinely differ (excluded_statuses)
# AND a DISTINCT difference (A counts distinct customers, B does not). Seed
# data recalibrated to realistic magnitude (Build 3, Day 2, Part 2b) -- SQL,
# declared definitions, and the two-cause shape are unchanged, only the
# underlying customer-table volume grew (scripts/build_seed_data.py,
# _generate_case_2_customers). Original hand-typed 9-row table produced
# single-digit reported values (5.0/4.0); this table produces hundreds.
CASE_2_MULTI_CAUSE = Scenario(
    scenario_id="case_02_multi_cause",
    description="Multi-cause: declared excluded_statuses genuinely differ, and A uses COUNT(DISTINCT) while B does not.",
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers "
            "WHERE status NOT IN ('churned') AND signup_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date",
            excluded_statuses=["churned"],
            aggregation="count_distinct",
        ),
    ),
    source_b=DashboardSource(
        label="growth_query",
        sql=(
            "SELECT COUNT(customer_id) AS active_customers FROM customers "
            "WHERE status NOT IN ('churned', 'trial') AND signup_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date",
            excluded_statuses=["churned", "trial"],
            aggregation="count",
        ),
    ),
    # Calibrated to real execution against the recalibrated seed data (Build
    # 3, Day 2, Part 2b), following Decision 13's own convention -- see Case
    # 1's comment for the original convention.
    reported_value_a=300.0,
    reported_value_b=280.0,
    known_gap=300.0 - 280.0,
    seed_table="case_02_multi_cause",
)

# Case 3: Declared vs. undeclared. B has no declared_definition (hybrid fallback
# to inference applies); A's declared definition is present and self-consistent.
CASE_3_HYBRID_FALLBACK = Scenario(
    scenario_id="case_03_hybrid_fallback",
    description="Declared vs. undeclared: A has a declared definition, B has none and must be inferred from its SQL.",
    source_a=DashboardSource(
        label="dashboard_a",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled"],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="ad_hoc_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND created_at >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    # Calibrated to real execution (Decision 13's resolution) -- see Case 1's comment.
    reported_value_a=550.0,
    reported_value_b=450.0,
    known_gap=550.0 - 450.0,
    seed_table="case_03_hybrid_fallback",
)

# Case 4: Governance drift (self-consistency issue). A's declared definition says
# it excludes both cancelled and refunded orders, but A's SQL only excludes
# cancelled — A's SQL contradicts A's own declared definition.
CASE_4_GOVERNANCE_DRIFT = Scenario(
    scenario_id="case_04_governance_drift",
    description="Governance drift: A's declared definition excludes cancelled and refunded, but A's SQL only excludes cancelled.",
    source_a=DashboardSource(
        label="dashboard_a",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE status != 'cancelled' AND order_date >= '2024-01-01'",
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    # Calibrated to real execution (Decision 13's resolution) -- see Case 1's comment.
    reported_value_a=300.0,
    reported_value_b=100.0,
    known_gap=300.0 - 100.0,
    seed_table="case_04_governance_drift",
)

# Case 5: Unexplained residual. Declared definitions match and SQL is structurally
# identical on both sides, yet the reported values still differ — no definitional
# or structural cause exists for this gap within this tool's scope.
CASE_5_UNEXPLAINED_RESIDUAL = Scenario(
    scenario_id="case_05_unexplained_residual",
    description="Unexplained residual: declared definitions and SQL structure match on both sides, but a real gap remains.",
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    reported_value_a=180000.0,
    reported_value_b=176200.0,
    known_gap=180000.0 - 176200.0,
    seed_table="case_05_unexplained_residual",
)

# Case 6: Negative control. No declared definitions on either side, and the SQL
# is genuinely identical — the tool should find nothing, and the gap is ~0.
CASE_6_NEGATIVE_CONTROL = Scenario(
    scenario_id="case_06_negative_control",
    description="Negative control: no declared definitions, SQL is identical on both sides, near-zero gap expected.",
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    reported_value_a=95000.0,
    reported_value_b=95000.0,
    known_gap=95000.0 - 95000.0,
    seed_table="case_06_negative_control",
)

# Case 7: Precedence-rule conflict. Unlike Case 4 (governance drift), A and B
# declare GENUINELY DIFFERENT excluded_statuses (A: cancelled only; B:
# cancelled + refunded) -- so diff_definitions(A, B) actually populates a
# real, non-empty excluded_statuses finding. AND A's SQL contradicts A's own
# declared definition on that same field: A declares excluding 'cancelled'
# but A's SQL only excludes 'refunded' (status != 'refunded') -- a governance
# drift that also fires check_self_consistency on excluded_statuses. Both
# conditions hold on the same field simultaneously, which Case 4 cannot
# produce (Case 4's declared sides agree by design, so its cross-source diff
# is empty before the precedence rule ever runs). This is the case that
# proves the precedence rule actually suppresses something, not just that it
# runs without error.
CASE_7_PRECEDENCE_CONFLICT = Scenario(
    scenario_id="case_07_precedence_conflict",
    description=(
        "Precedence-rule conflict: A and B declare genuinely different "
        "excluded_statuses (declared conflict), AND A's SQL also "
        "contradicts A's own declared definition on that same field "
        "(governance drift) -- exercises the SelfConsistencyIssue "
        "suppressing the matching cross-source DefinitionDifference."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status != 'refunded' AND order_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled"],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded') AND order_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["cancelled", "refunded"],
            aggregation="sum",
        ),
    ),
    reported_value_a=300.0,
    reported_value_b=100.0,
    known_gap=300.0 - 100.0,
    seed_table="case_07_precedence_conflict",
)

# Case 8: stale extract (Build 2, Day 2). source_a.sql and source_b.sql are
# identical text (matching Case 5/6's clean-fixture pattern) and both sides
# declare no metric definition -- sql_diff, definition_diff, and
# self_consistency all find nothing (verified: no other cause type is
# present). The entire gap traces to source_a's seed_table
# ("case_08_stale_extract", the stale/as-delivered snapshot per decision
# 15) being missing order_id 3 relative to freshness_complete_seed_table
# ("case_08_stale_extract_complete", the complete counterfactual) --
# source_a is the stale side, chosen arbitrarily (matching Case 4/7's own
# precedent of putting the data-quality cause on side A). reported_value_a
# (300.0) and reported_value_b (450.0) are both real execution figures
# (scripts/build_seed_data.py, verified directly against the actual seed
# files, not hand-typed) -- per decision 13's calibration convention,
# restated explicitly for this new fixture shape by Scenario's own
# docstring (src/scenario.py).
CASE_8_STALE_EXTRACT = Scenario(
    scenario_id="case_08_stale_extract",
    description=(
        "Stale extract: source_a's snapshot is missing a row that "
        "source_b's (complete) snapshot has -- no definitional or "
        "structural cause present, isolating the freshness mechanism "
        "the same way Case 5/6 isolate their own single mechanism."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    reported_value_a=300.0,
    reported_value_b=450.0,
    known_gap=300.0 - 450.0,
    seed_table="case_08_stale_extract",
    freshness_complete_seed_table="case_08_stale_extract_complete",
)

# Case 9: missing partition (Build 2, Day 3). Shares Case 8's exact clean-
# fixture shape (identical SQL both sides, no declared definitions, no
# other cause type present) -- only the seed data differs, deliberately,
# to isolate missing_partition from stale_extract by construction rather
# than by any code difference (src/data_quality.py's check_missing_partition
# and check_stale_extract are the same detection mechanism; see that
# module's docstring). Case 8's stale side is missing one scattered row
# (order_id 3) with no structural relationship to what remains. Case 9's
# stale side is missing an entire contiguous, identifiable date-slice --
# every row for order_date='2024-01-10' (order_id 1 and 2, both rows) --
# while a different date (2024-02-01) is untouched, which is what makes
# this genuinely a "missing partition" rather than a relabeled Case 8.
# reported_value_a (200.0) and reported_value_b (450.0) are real execution
# figures (scripts/build_seed_data.py, verified directly against the
# actual seed files), per decision 13's calibration convention.
CASE_9_MISSING_PARTITION = Scenario(
    scenario_id="case_09_missing_partition",
    description=(
        "Missing partition: source_a's snapshot is missing an entire "
        "contiguous date-slice (every row for one order_date) that "
        "source_b's (complete) snapshot has -- no definitional or "
        "structural cause present, isolating the freshness mechanism "
        "the same way Case 8 isolates its own scattered-row variant."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    reported_value_a=200.0,
    reported_value_b=450.0,
    known_gap=200.0 - 450.0,
    seed_table="case_09_missing_partition",
    freshness_complete_seed_table="case_09_missing_partition_complete",
)

# Case 10: referential integrity (Build 2, Day 4). Single freshness/quality
# cause only, no definitional or structural cause present (identical SQL
# both sides, no declared definitions) -- matches Case 8/9's clean-fixture
# shape. Distinct mechanism from Case 8/9: source_a's orders (fact) table
# has one row referencing a customer_id (99) absent from the customers
# (dimension) table shared by both sides -- a genuine orphan foreign-key
# reference, not a row-count/completeness issue. source_b's orders table
# simply omits that row, representing the trustworthy state. No
# freshness_complete_seed_table is used here (unlike Case 8/9): referential
# integrity's dollar-impact counterfactual is computed within a single
# database (baseline query vs. the same query with an added FK-resolves
# filter, src/data_quality.py's check_referential_integrity), not across a
# stale/complete snapshot pair -- a genuinely different arithmetic shape,
# matching the different detection mechanism. reported_value_a (600.0) and
# reported_value_b (300.0) are real execution figures (scripts/build_seed_data.py,
# verified directly against the actual seed files), per decision 13's
# calibration convention.
CASE_10_REFERENTIAL_INTEGRITY = Scenario(
    scenario_id="case_10_referential_integrity",
    description=(
        "Referential integrity: source_a's orders (fact) table has an "
        "orphan row referencing a customer_id absent from the customers "
        "(dimension) table -- no definitional or structural cause "
        "present, isolating the referential-integrity mechanism the way "
        "Case 8/9 isolate their own completeness mechanism."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    reported_value_a=600.0,
    reported_value_b=300.0,
    known_gap=600.0 - 300.0,
    seed_table="case_10_referential_integrity",
)

# Case 11: referential integrity, mirrored onto source_b (Build 2, Day 4
# close-out). Case 10 alone never exercised check_referential_integrity's
# source="b" sign-flip branch with real execution -- only its source="a"
# branch had a real, executed proof point. This is a real gap a review
# found: Day 2/3's dollar_impact sign convention (negate for source="a",
# use as-is for source="b") is documented and was applied in
# check_referential_integrity's code by direct structural analogy, but
# that analogy had never been proven by running the code with the cause
# on source_b, the same shape of gap Day 6's self-consistency close-out
# found and closed with a dedicated source="b" test rather than trusting
# code inspection alone. Case 11 is Case 10's exact mirror image: the
# orphan row (customer_id=99) sits on source_b's orders table instead of
# source_a's. reported_value_a (300.0), reported_value_b (600.0), and
# known_gap (-300.0) are real execution figures (scripts/build_seed_data.py),
# per decision 13's calibration convention -- check_referential_integrity's
# dollar_impact for source="b" came out to exactly -300.0, matching
# known_gap exactly, confirming the sign-flip is correct by execution, not
# assumption.
CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B = Scenario(
    scenario_id="case_11_referential_integrity_source_b",
    description=(
        "Referential integrity, mirrored onto source_b: source_b's orders "
        "(fact) table has an orphan row referencing a customer_id absent "
        "from the customers (dimension) table -- Case 10's exact mirror "
        "image, proving check_referential_integrity's source=\"b\" "
        "sign-flip with real execution rather than by code inspection "
        "alone."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE status NOT IN ('cancelled') AND order_date >= '2024-01-01'"
        ),
        declared_definition=None,
    ),
    reported_value_a=300.0,
    reported_value_b=600.0,
    known_gap=300.0 - 600.0,
    seed_table="case_11_referential_integrity_source_b",
)

# Case 12: COLLISION PROOF FIXTURE (Build 3, Day 1, Part 2) -- proves overlap
# #7 from Build 3 Day 1 Part 1's inventory: sql_diff's join_type category and
# check_referential_integrity fire on the SAME underlying fact. Deliberately
# mirrors Case 1's own shape (LEFT vs INNER JOIN on the identical join
# condition; a real orphan row -- customer_id=99 -- with no matching
# customers row) but on wholly new, standalone seed data -- Case 1's own
# fixture, seed data, and dispatch entry are untouched by this session.
# Both source_a.sql and source_b.sql run against the SAME underlying data
# (identical CASE_12_CUSTOMERS/CASE_12_ORDERS on both seed files) -- the
# orphan row survives source_a's LEFT JOIN and is dropped by source_b's
# INNER JOIN, which is exactly what makes it a real join_type finding AND a
# real referential-integrity orphan simultaneously. reported_value_a
# (600.0), reported_value_b (300.0), and known_gap (300.0) are real
# execution figures (scripts/build_seed_data.py), per decision 13's
# calibration convention.
#
# EXCLUSION REASON, stated explicitly per this session's own requirement:
# collision proof fixture, no suppression rule exists yet -- including it
# in SCENARIOS would let two tools silently double-count the same fact in
# any pipeline run. This is not a temporary exclusion pending a trigger
# condition (unlike Cases 8-11 before their wiring) -- it stays excluded
# until a suppression/dispatch design is chosen in chat and implemented in
# a future session. NOT added to SCENARIOS.
CASE_12_JOIN_ORPHAN_COLLISION = Scenario(
    scenario_id="case_12_join_orphan_collision",
    description=(
        "Collision proof fixture: a real orphan FK row (customer_id=99) "
        "simultaneously produces sql_diff's join_type finding (LEFT keeps "
        "it, INNER drops it) and a check_referential_integrity orphan "
        "finding -- proves overlap #7 from Build 3 Day 1 Part 1's "
        "inventory on freshly built data, not by code inspection."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(o.amount) AS revenue FROM orders o "
            "LEFT JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.order_date >= '2024-01-01' AND o.status NOT IN ('cancelled')"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(o.amount) AS revenue FROM orders o "
            "INNER JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.order_date >= '2024-01-01' AND o.status NOT IN ('cancelled')"
        ),
        declared_definition=None,
    ),
    reported_value_a=600.0,
    reported_value_b=300.0,
    known_gap=600.0 - 300.0,
    seed_table="case_12_join_orphan_collision",
)

# Case 13: COLLISION PROOF FIXTURE (Build 3, Day 1, Part 2) -- proves overlap
# #4 from Build 3 Day 1 Part 1's inventory: sql_diff's filter category and
# definition_diff's excluded_statuses field fire on the SAME underlying
# fact. Neither side declares a metric definition, forcing the inferred
# path (src/definition_diff.py's _infer_excluded_statuses), not
# declared-vs-declared, which is a different code path entirely.
#
# DEVIATION FROM A LITERAL "both sides filter status, different values"
# CONSTRUCTION, stated explicitly: that shape was tried first and verified,
# by execution, NOT to produce a sql_diff finding at all --
# src/sql_diff.py's _diff_filters is presence-only (flags a column filtered
# on one side and absent on the other; it does not compare the VALUES
# inside a filter both sides share), so two different NOT IN sets on a
# column both sides filter never registers as a `filter` category
# difference. The shape that actually exercises this overlap is: source_a
# filters status via a real NOT IN exclusion ("status NOT IN ('churned')"),
# source_b has NO status filter whatsoever (not a differently-valued one).
# This is still a genuine NOT IN exclusion shape on the filtering side --
# not an inclusion filter substituted for convenience, which Part 1's
# inventory already confirmed _infer_excluded_statuses does not recognize
# at all. Both source_a.sql/source_b.sql run against the SAME underlying
# data (identical CASE_13_ORDERS on both seed files). reported_value_a
# (300.0), reported_value_b (450.0), and known_gap (-150.0) are real
# execution figures, per decision 13's calibration convention.
#
# EXCLUSION REASON, updated Build 3 Day 1 Part 6: the double-counting risk
# this exclusion originally guarded against is now resolved --
# assemble_structural_and_definitional_evidence's third rule
# (_same_filter_exclusion_fact, src/self_consistency.py) correctly
# suppresses this fixture's excluded_statuses DefinitionDifference in
# favor of the surviving filter SQLStructuralDifference, confirmed by
# execution. Still NOT added to SCENARIOS, for a different, narrower
# reason now: the surviving filter finding has no corrected-query
# mutation rule (construct_corrected_query, src/query_mutation.py), so
# assemble_investigation_evidence still raises for this scenario --
# blocked purely on filter's missing correction mechanism, not on any
# remaining collision/double-counting concern. No data-quality check or
# dispatch entry is involved for this fixture at all. NOT added to
# SCENARIOS.
CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION = Scenario(
    scenario_id="case_13_filter_excluded_statuses_collision",
    description=(
        "Collision proof fixture: source_a filters status via a real "
        "NOT IN exclusion, source_b has no status filter at all -- "
        "simultaneously produces sql_diff's filter finding (column "
        "presence differs) and definition_diff's excluded_statuses "
        "finding (inferred sets differ) -- proves overlap #4 from Build "
        "3 Day 1 Part 1's inventory on freshly built data, not by code "
        "inspection."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND status NOT IN ('churned')"
        ),
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01'",
        declared_definition=None,
    ),
    reported_value_a=300.0,
    reported_value_b=450.0,
    known_gap=300.0 - 450.0,
    seed_table="case_13_filter_excluded_statuses_collision",
)

# Case 14: finalized 8-scenario technical-benchmark list, item 6 (Build 3,
# Day 3, Part 6 -- CONTEXT.md). date_field + excluded_statuses, two-cause,
# excluded_statuses at LOW confidence (inferred) -- every other committed
# 2-cause excluded_statuses pair in this project (Case 2, Case 3, the three
# AMBIGUOUS_* two-cause scenarios) uses a declared, high-confidence
# excluded_statuses side; this is the first to exercise the Shapley-pair/
# correction machinery when one member of a genuinely interacting pair is
# low-confidence/inferred instead of declared.
#
# BOTH SIDES FILTER THE STATUS COLUMN, deliberately -- source_a via a real
# NOT IN exclusion ("status NOT IN ('churned')"), source_b via an
# inclusion-style predicate ("status = 'active'") that
# src.definition_diff._infer_excluded_statuses does not recognize as an
# exclusion shape (Day 3's own documented low-confidence branch), forcing
# low confidence. This is the OPPOSITE construction from Case 13's own
# "one side filters, the other doesn't" shape: Case 13 tried "both sides
# filter status, different values" first and confirmed by execution that
# src.sql_diff._diff_filters (presence-only) never produces a `filter`
# finding when both sides already filter the same column -- exactly the
# property this fixture depends on to stay clear of decisions 18/22's
# filter/excluded_statuses suppression rule entirely (that rule only ever
# has something to fire against when a real `filter` SQLStructuralDifference
# exists in the first place). Confirmed live for this exact fixture, not
# assumed to transfer from Case 13: sql_differences contains no `filter`
# category at any point in the pipeline.
#
# source_a's declared_definition is faithfully implemented by source_a.sql
# (no self-consistency issue); source_b has no declared_definition at all
# (hybrid fallback), so date_field and excluded_statuses both route through
# infer_definition_from_sql for source_b. date_field lands at confidence
# "medium" overall (min(high, medium) -- source_b's SQL has exactly one
# clear date-like column, created_at) -- decision 12's own precedence rule
# has only ever been proven at this confidence level, so this fixture
# stays within already-proven territory for that suppression, not a new
# confidence case. source_a's declared aggregation ("sum") matches
# source_b's inferred aggregation exactly (both SUM(amount)), so aggregation
# never differs -- remaining_causes is exactly 2 (date_field, excluded_statuses)
# after decision 12 suppresses the redundant sql_diff date_field finding,
# confirmed live.
#
# reported_value_a (950.0), reported_value_b (500.0), and known_gap (450.0)
# are real execution figures against this fixture's own seed data, per
# decision 13's calibration convention.
#
# A REAL, PREVIOUSLY-UNDOCUMENTED FINDING, confirmed live rather than
# assumed (decision 27, docs/decisions.md): assemble_investigation_evidence
# runs to completion on this fixture with NO exception -- proving the
# Shapley-pair/correction machinery does execute successfully with a
# low-confidence/inferred member, which was this fixture's whole design
# purpose -- but unexplained_residual is 650.0, NOT 0.0, even though this
# is a clean, fully-covered two-cause fixture with no data-quality/freshness
# cause and no third interacting cause. Root cause: correcting
# excluded_statuses toward source_b's inferred value ("(none)", the fixed
# placeholder src.definition_diff._infer_excluded_statuses returns for
# EVERY low-confidence case, decision-20/21's zero-exclusion convention)
# means "remove A's exclusion filter entirely" -- structurally different
# from what source_b's SQL actually does (an inclusion filter, "only
# active"), which this project's schema has no vocabulary to represent as
# a target for correction. See decision 27 for the full reasoning and the
# real dollar-impact breakdown.
#
# EXCLUSION REASON, stated explicitly per this session's own requirement:
# NOT added to SCENARIOS. This is not a crash and not a double-counting
# risk (like Cases 12/13) -- the pipeline completes and returns a real,
# non-crashing InvestigationEvidence -- but its unexplained_residual is
# large and genuinely misleading if read as "a small amount of gap
# couldn't be explained": 650.0 against a known_gap of only 450.0. Adding
# it to SCENARIOS today would put a fixture with a wrong-looking residual
# into every full-SCENARIOS residual sweep with no caveat attached, the
# same kind of silent misread Cases 8-11's own SCENARIOS entry explicitly
# warns against for a different reason. Stays excluded until a future
# session decides how to represent this honestly (documenting the
# residual as an accepted, structural consequence of low-confidence
# inference and adding it anyway, or resolving the underlying inclusion-
# vs-exclusion representation gap first) -- not resolved here.
CASE_14_DATE_FIELD_LOW_CONFIDENCE_EXCLUDED_STATUSES = Scenario(
    scenario_id="case_14_date_field_low_confidence_excluded_statuses",
    description=(
        "date_field + excluded_statuses, two-cause: source_b has no "
        "declared definition, and its inclusion-style status filter "
        "('status = 'active'') is not a recognized exclusion shape, "
        "forcing a low-confidence excluded_statuses inference. Both "
        "sides filter the status column, deliberately, so no sql_diff "
        "`filter` finding is ever produced."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE order_date >= '2024-01-01' AND status NOT IN ('churned')"
        ),
        declared_definition=DeclaredDefinition(
            date_field="order_date",
            excluded_statuses=["churned"],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql=(
            "SELECT SUM(amount) AS revenue FROM orders "
            "WHERE created_at >= '2024-01-01' AND status = 'active'"
        ),
        declared_definition=None,
    ),
    reported_value_a=950.0,
    reported_value_b=500.0,
    known_gap=950.0 - 500.0,
    seed_table="case_14_date_field_low_confidence_excluded_statuses",
)

# Case 15: finalized 8-scenario technical-benchmark list, item 1 (Build 3,
# Day 3, Part 7). date_field only, single-cause, INFERRED path -- NEITHER
# side declares a metric definition (both declared_definition=None), a
# genuinely different code shape from every prior date_field fixture:
# Case 3 and Case 14 are both declared-vs-undeclared (one side "high"
# confidence, weakest-link pulls the overall confidence down to the
# undeclared side's inferred level); Case 15 is undeclared-vs-undeclared,
# both legs routed through infer_definition_from_sql independently, and
# the overall confidence (min of two "medium"s) is "medium" from BOTH
# sides agreeing, not from one side dragging the other down.
#
# Neither side filters on status at all -- zero status predicates on
# either side, so excluded_statuses infers the same "(none)"/low-confidence
# value on both sides and never differs (confirmed live: no
# DefinitionDifference for this field). Both sides aggregate via
# SUM(amount) with no DISTINCT, so aggregation infers "sum"/medium
# confidence identically on both sides and never differs either
# (confirmed live). date_field is the ONLY field that differs -- source_a
# infers "order_date" (medium), source_b infers "ship_date" (medium),
# overall confidence "medium" (min(medium, medium)), source="inferred"
# (neither side declared). sql_diff's own `date_field` structural finding
# on the same order_date/ship_date swap collides with and is suppressed
# by decision 12 (already-proven confidence="medium" case, the same level
# this fixture reaches) -- confirmed live: sql_differences == [] after
# suppression, leaving exactly 1 remaining cause, routed through
# single_cause_attribution, not the Shapley-pair branch.
#
# reported_value_a (650.0), reported_value_b (550.0), and known_gap
# (100.0) are real execution figures against this fixture's own seed
# data, per decision 13's calibration convention. Confirmed live:
# assemble_investigation_evidence reconciles to unexplained_residual=0.0
# exactly (date_field's own dollar_impact, 100.0, equals known_gap in
# full) -- a clean, fully-explained single-cause fixture, added directly
# to SCENARIOS.
CASE_15_DATE_FIELD_INFERRED_ONLY = Scenario(
    scenario_id="case_15_date_field_inferred_only",
    description=(
        "date_field only, single-cause, inferred path: neither source "
        "declares a metric definition -- source_a's SQL filters on "
        "order_date, source_b's on ship_date, both inferred at medium "
        "confidence, with excluded_statuses and aggregation matching "
        "identically on both sides."
    ),
    source_a=DashboardSource(
        label="dashboard_a",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE order_date >= '2024-01-01'",
        declared_definition=None,
    ),
    source_b=DashboardSource(
        label="finance_query",
        sql="SELECT SUM(amount) AS revenue FROM orders WHERE ship_date >= '2024-01-01'",
        declared_definition=None,
    ),
    reported_value_a=650.0,
    reported_value_b=550.0,
    known_gap=650.0 - 550.0,
    seed_table="case_15_date_field_inferred_only",
)

SCENARIOS = [
    CASE_1_JOIN_TYPE,
    CASE_2_MULTI_CAUSE,
    CASE_3_HYBRID_FALLBACK,
    CASE_4_GOVERNANCE_DRIFT,
    CASE_5_UNEXPLAINED_RESIDUAL,
    CASE_6_NEGATIVE_CONTROL,
    CASE_7_PRECEDENCE_CONFLICT,
    CASE_8_STALE_EXTRACT,
    CASE_9_MISSING_PARTITION,
    CASE_10_REFERENTIAL_INTEGRITY,
    CASE_11_REFERENTIAL_INTEGRITY_SOURCE_B,
    CASE_15_DATE_FIELD_INFERRED_ONLY,
]
"""Build 3, Day 3, Part 7: Case 15 (finalized 8-scenario list, item 1) added
directly -- unlike Cases 8-11, it carries no exclusion-trigger caveat: it is
a clean single-cause fixture (definition_differences=[date_field],
sql_differences=[] after decision 12 suppression, no data_quality_issues)
that reconciles to unexplained_residual=0.0 exactly, confirmed by real
execution before being added, per this project's own standing discipline
(decision 13's calibration convention; every prior addition to this list
verified live first).

Build 2, Day 5: Cases 8-11's exclusion trigger ("wire data_quality_issues
into assemble_investigation_evidence") is now satisfied --
_resolve_data_quality_issues (src/reconciliation_assembly.py) populates
data_quality_issues for exactly these four scenario_ids -- so all four now
join SCENARIOS, per that trigger's own stated condition.

**Read this before trusting any full-SCENARIOS residual sweep or
"all fixtures reconcile" claim involving Cases 8-11 specifically:**
data_quality_issues is ADDITIVE EVIDENCE ONLY (Build 2, Day 5, locked
decision) -- it does not participate in reconciliation/unexplained_residual
math (see assemble_investigation_evidence's own docstring,
src/reconciliation_assembly.py, for the full reasoning). For all four of
these cases, evidence.reconciliation is [] and evidence.unexplained_residual
equals known_gap in full, THE SAME SHAPE as Case 5's true "no cause exists"
scenario -- even though Cases 8-11 each have a real, fully-quantified,
found cause (visible only in evidence.data_quality_issues, not in the
residual number). Do not read a full-SCENARIOS command/test that reports
unexplained_residual as claiming these four are "fully reconciled" or
"fully unexplained" -- neither framing is accurate; the correct framing is
"the definitional/structural/self-consistency machinery found nothing,
and separately, the data-quality check found something, and the two
numbers are not combined yet."

Also still separately deferred, unrelated to the above and NOT resolved
this session: src/explainer.py does not render data_quality_issues in its
prompt at all (Build 2, Day 1's original deferral, still standing) -- a
live explainer run against any of these four cases will describe them as
if no cause was found, the same prose shape as Case 5, since the
evidence object it's handed simply omits data_quality_issues from its
prompt text today. This is a second, independent gap from the residual-math
one above, tracked separately in CONTEXT.md's Open Items."""
