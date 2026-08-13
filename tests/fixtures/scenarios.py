"""The 7 hand-designed test scenarios used to build and validate Build 1's
deterministic tools (Days 2-6). Each is a committed Scenario instance, not a
throwaway object — SQL is realistic enough for sqlglot to parse and for the
intended structural/definitional difference to actually be present in the
SQL text, per each case's design."""

from src.scenario import DeclaredDefinition, DashboardSource, Scenario

# Case 1: Clean single-cause, both declared identically, join-type difference.
# A's LEFT JOIN keeps orders with no matching customer row; B's INNER JOIN drops
# them. Same declared definition on both sides, so the only cause is structural.
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
# AND a DISTINCT difference (A counts distinct customers, B does not).
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
    # Calibrated to real execution (Decision 13's resolution) -- see Case 1's comment.
    reported_value_a=5.0,
    reported_value_b=4.0,
    known_gap=5.0 - 4.0,
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

SCENARIOS = [
    CASE_1_JOIN_TYPE,
    CASE_2_MULTI_CAUSE,
    CASE_3_HYBRID_FALLBACK,
    CASE_4_GOVERNANCE_DRIFT,
    CASE_5_UNEXPLAINED_RESIDUAL,
    CASE_6_NEGATIVE_CONTROL,
    CASE_7_PRECEDENCE_CONFLICT,
]
