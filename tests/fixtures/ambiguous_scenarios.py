"""Proof-of-concept ambiguous (genuinely-disputed business-rule) scenarios,
Build 3, Day 2, Part 3 -- Option A: two dashboards disagree not because one
side has a data problem or an undeclared/inferred definition, but because
both sides declared a real, defensible metric definition and those
definitions legitimately conflict. Unlike every technical fixture in
scenarios.py, the correct system behavior here is NOT to find a cause and
report a number -- it is to find the cause AND decline to declare either
side wrong, per decision 6's escalation-recall metric. Not added to
SCENARIOS, matching the convention every other proof-of-concept fixture in
this project follows (Cases 12/13) -- these are proof-of-concept only,
not yet confirmed ready for the live benchmark set."""

from src.scenario import DeclaredDefinition, DashboardSource, Scenario

# Refund timing: two legitimate accounting conventions exist for when a
# refund should reduce reported revenue. Source A's finance team books
# refunds against the month the refund was actually issued (cash-accounting
# convention) -- this matches when cash actually left the bank account and
# is the convention their external financial statements and cash-flow
# forecasting depend on, since a refund physically debits the current
# month's cash position regardless of when the original sale happened.
# Source B's growth/retention team instead restates the refund back against
# the month of the original purchase (cohort convention) -- this is
# standard practice for cohort-based retention and LTV reporting, where a
# February refund on a November purchase is really telling you something
# about November's cohort quality, not February's cash flow, and burying it
# in February's number would corrupt every cohort comparison the retention
# team runs. Neither team is wrong; they are answering different questions
# with the same underlying refund event, and reconciling the two requires
# knowing which question the requester actually wants answered -- not
# picking a winner.
AMBIGUOUS_REFUND_TIMING = Scenario(
    scenario_id="ambiguous_refund_timing",
    description=(
        "Ambiguous business-rule scenario: source_a attributes a refund to "
        "the month it was issued (cash-accounting convention), source_b "
        "attributes it to the month of the original purchase (cohort/"
        "retention convention) -- both declared, both defensible, no "
        "single correct answer to unilaterally pick."
    ),
    source_a=DashboardSource(
        label="finance_cash_view",
        sql=(
            "SELECT SUM(amount) AS net_refunds FROM refunds "
            "WHERE refund_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="refund_date",
            excluded_statuses=[],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="retention_cohort_view",
        sql=(
            "SELECT SUM(amount) AS net_refunds FROM refunds "
            "WHERE purchase_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="purchase_date",
            excluded_statuses=[],
            aggregation="sum",
        ),
    ),
    # Calibrated to real execution (scripts/build_seed_data.py,
    # AMBIGUOUS_REFUND_TIMING_REFUNDS), per decision 13's convention.
    reported_value_a=880.0,
    reported_value_b=530.0,
    known_gap=880.0 - 530.0,
    seed_table="ambiguous_refund_timing",
)

# Revenue recognition timing: two legitimate revenue-recognition conventions
# exist for when a sale should count toward reported revenue. Source A's
# sales team books revenue at the moment a contract is signed (booking-date
# convention) -- sales compensation, pipeline forecasting, and board-level
# bookings targets are all built around when the deal closed, since that is
# the moment sales effort produced value and the only date sales actually
# controls. Source B's finance team instead recognizes revenue only once
# the product or service is actually delivered, deferring anything still
# pending delivery (delivery-date / deferred-revenue convention) -- this is
# required by standard revenue-recognition accounting principles (e.g. ASC
# 606), since counting a contract as revenue before the company has
# performed its obligation would overstate the period's true financial
# results. Neither team is wrong; sales bookings and recognized GAAP
# revenue are legitimately different numbers that answer different
# questions, and treating one as simply "the correct number" would break
# whichever team's process depends on the other convention. Deliberately a
# two-cause interacting shape (date_field AND excluded_statuses both
# differ), not a renamed copy of the single-cause refund-timing scenario
# above.
#
# FINDING, reported rather than resolved (Build 3, Day 2, Part 3): diff_definitions
# correctly produces both DefinitionDifferences (date_field, excluded_statuses),
# confirming the mechanical cause IS genuinely findable. But
# assemble_investigation_evidence currently raises for this scenario --
# source_b's status filter (absent on source_a) is ALSO picked up by
# sql_diff as a real `filter` structural finding, and decision 18
# (docs/decisions.md) explicitly left the confidence="medium"/"high"
# filter/excluded_statuses collision UNHANDLED (both findings survive
# untouched -- decision 18 only suppresses at confidence="low"). Here
# excluded_statuses is declared, confidence="high", so nothing suppresses
# it: 3 remaining cross-source causes reach assemble_investigation_evidence's
# 3+-causes guard, which correctly refuses to guess rather than silently
# picking a sub-pair. This is a natural, high-confidence, real-narrative
# scenario reaching a gap previously only reached by Case 13's synthetic,
# low-confidence collision-proof fixture -- not resolved here, per this
# task's own instruction to report rather than unilaterally fix a gap
# surfaced mid-authoring. No BenchmarkEntry added for this scenario yet.
AMBIGUOUS_REVENUE_RECOGNITION = Scenario(
    scenario_id="ambiguous_revenue_recognition",
    description=(
        "Ambiguous business-rule scenario: source_a recognizes revenue at "
        "contract booking (sales-bookings convention), source_b recognizes "
        "it at delivery and excludes anything still pending delivery "
        "(deferred-revenue/GAAP convention) -- both declared, both "
        "defensible, no single correct answer to unilaterally pick."
    ),
    source_a=DashboardSource(
        label="sales_bookings_view",
        sql=(
            "SELECT SUM(amount) AS revenue FROM contracts "
            "WHERE booking_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="booking_date",
            excluded_statuses=[],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="finance_recognized_revenue_view",
        sql=(
            "SELECT SUM(amount) AS revenue FROM contracts "
            "WHERE status != 'pending_delivery' AND delivery_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="delivery_date",
            excluded_statuses=["pending_delivery"],
            aggregation="sum",
        ),
    ),
    # Calibrated to real execution (scripts/build_seed_data.py,
    # AMBIGUOUS_REVENUE_RECOGNITION_CONTRACTS), per decision 13's convention.
    reported_value_a=2400.0,
    reported_value_b=2450.0,
    known_gap=2400.0 - 2450.0,
    seed_table="ambiguous_revenue_recognition",
)

# Customer-counting convention (Build 3, Day 2, Part 7): two legitimate,
# distinct definitions of "how many customers do we have." Source A's
# retention/LTV team counts every customer who has ever signed up,
# regardless of their current status or how recently they've engaged --
# lifetime customer count is the correct denominator for lifetime-value
# and long-horizon retention-curve reporting, since a customer who
# churned two years ago still contributed real historical value and
# still belongs in a cohort analysis of "everyone we've ever acquired."
# Source B's growth/engagement team instead counts only customers who are
# currently active and have engaged within the current reporting period,
# excluding anyone who has churned -- this is the correct number for
# answering "how big is our live, engaged customer base right now,"
# which is what current-period engagement dashboards, active-seat
# billing, and this-quarter health metrics actually need; folding in
# every customer who ever existed, churned or not, would make the
# current-period number meaningless for that purpose. Neither team is
# wrong; "how many customers do we have" genuinely means two different
# things depending on whether the question is about history or about
# right now. Deliberately reproduces the same two-cause shape (date_field
# AND excluded_statuses both differ) as AMBIGUOUS_REVENUE_RECOGNITION, on
# purpose -- to test whether that scenario's 3+-cause collision was tied
# to its specific business-rule shape or is a more general risk across
# any declared, high-confidence ambiguous pair with this shape.
AMBIGUOUS_CUSTOMER_COUNTING = Scenario(
    scenario_id="ambiguous_customer_counting",
    description=(
        "Ambiguous business-rule scenario: source_a counts every customer "
        "who has ever signed up (lifetime/LTV convention), source_b counts "
        "only customers active in the current period, excluding churned "
        "ones (current-period engagement convention) -- both declared, "
        "both defensible, no single correct answer to unilaterally pick."
    ),
    source_a=DashboardSource(
        label="retention_ltv_view",
        sql=(
            "SELECT COUNT(DISTINCT customer_id) AS customer_count FROM customers "
            "WHERE signup_date >= '2000-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="signup_date",
            excluded_statuses=[],
            aggregation="count_distinct",
        ),
    ),
    source_b=DashboardSource(
        label="engagement_current_period_view",
        sql=(
            "SELECT COUNT(DISTINCT customer_id) AS customer_count FROM customers "
            "WHERE status != 'churned' AND last_active_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="last_active_date",
            excluded_statuses=["churned"],
            aggregation="count_distinct",
        ),
    ),
    # Calibrated to real execution (scripts/build_seed_data.py,
    # AMBIGUOUS_CUSTOMER_COUNTING_CUSTOMERS), per decision 13's convention.
    reported_value_a=450.0,
    reported_value_b=300.0,
    known_gap=450.0 - 300.0,
    seed_table="ambiguous_customer_counting",
)

# Attribution/join convention (Build 3, Day 2, Part 7): two legitimate
# conventions for which reporting period a deal's revenue belongs to.
# Source A's marketing team attributes a deal to the period marketing
# first engaged the customer (first-touch attribution), and counts the
# full pipeline value regardless of eventual outcome -- this is the
# correct number for measuring marketing's contribution and channel
# effectiveness, since marketing's job is generating qualified pipeline,
# not closing it, and a deal marketing sourced still demonstrates
# marketing's impact even if sales later loses or hasn't yet closed it.
# Source B's sales team instead attributes revenue to the period the deal
# actually closed (last-touch attribution), and counts only closed-won
# deals -- this is the correct number for sales compensation and revenue
# reporting, since commission and recognized revenue are only ever paid
# on deals that actually closed, and crediting a period with pipeline
# that never converted (or hasn't converted yet) would overstate what
# sales actually delivered. Neither team is wrong; marketing-effectiveness
# reporting and sales-compensation reporting are legitimately different
# questions about the same underlying deals. A third, structurally
# distinct business domain from the two scenarios above (subscriptions,
# customer status), deliberately still constructed with the same
# two-cause shape (date_field AND excluded_statuses both differ) to keep
# testing the same hypothesis about AMBIGUOUS_REVENUE_RECOGNITION's
# collision.
AMBIGUOUS_ATTRIBUTION = Scenario(
    scenario_id="ambiguous_attribution",
    description=(
        "Ambiguous business-rule scenario: source_a attributes deal value "
        "to the period of first marketing touch, counting all pipeline "
        "regardless of outcome (first-touch/marketing convention); "
        "source_b attributes it to the period the deal closed, counting "
        "only closed-won deals (last-touch/sales-comp convention) -- both "
        "declared, both defensible, no single correct answer to "
        "unilaterally pick."
    ),
    source_a=DashboardSource(
        label="marketing_first_touch_view",
        sql=(
            "SELECT SUM(amount) AS revenue FROM deals "
            "WHERE first_touch_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="first_touch_date",
            excluded_statuses=[],
            aggregation="sum",
        ),
    ),
    source_b=DashboardSource(
        label="sales_comp_last_touch_view",
        sql=(
            "SELECT SUM(amount) AS revenue FROM deals "
            "WHERE status NOT IN ('lost', 'open') AND close_date >= '2024-01-01'"
        ),
        declared_definition=DeclaredDefinition(
            date_field="close_date",
            excluded_statuses=["lost", "open"],
            aggregation="sum",
        ),
    ),
    # Calibrated to real execution (scripts/build_seed_data.py,
    # AMBIGUOUS_ATTRIBUTION_DEALS), per decision 13's convention.
    reported_value_a=24000.0,
    reported_value_b=24500.0,
    known_gap=24000.0 - 24500.0,
    seed_table="ambiguous_attribution",
)
