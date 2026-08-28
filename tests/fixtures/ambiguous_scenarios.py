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
