"""Tests for tests.fixtures.benchmark_pipeline.assemble_investigation_evidence_for_benchmark
(Build 3, Day 2, Part 4, A3-ii)."""

import pytest

from src.reconciliation_assembly import assemble_investigation_evidence
from tests.fixtures.ambiguous_scenarios import AMBIGUOUS_REVENUE_RECOGNITION
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES, BenchmarkEntry
from tests.fixtures.benchmark_pipeline import assemble_investigation_evidence_for_benchmark
from tests.fixtures.scenarios import SCENARIOS


def test_ambiguous_revenue_recognition_returns_partial_evidence_via_wrapper():
    """The whole point of A3-ii: a genuinely ambiguous scenario whose
    interacting causes exceed the 2-cause Shapley pairing this project
    supports must still surface its individually-found causes through the
    benchmark wrapper, rather than the caller getting nothing but a raised
    exception."""
    entry = next(e for e in BENCHMARK_ENTRIES if e.scenario.scenario_id == "ambiguous_revenue_recognition")
    assert entry.is_ambiguous is True
    assert entry.expected_behavior == "escalate"

    evidence = assemble_investigation_evidence_for_benchmark(entry)

    fields = {d.field: (d.source_a_value, d.source_b_value) for d in evidence.definition_differences}
    assert fields == {
        "date_field": ("booking_date", "delivery_date"),
        "excluded_statuses": ("(none)", "pending_delivery"),
    }
    assert all(d.source == "declared" and d.confidence == "high" for d in evidence.definition_differences)
    assert evidence.reconciliation == []
    assert evidence.unexplained_residual is None


def test_technical_scenario_3plus_causes_still_raises_through_wrapper():
    """The single most important behavior: a technical (is_ambiguous=False)
    scenario whose genuine 3+-cause failure fires must re-raise unchanged
    through the wrapper, not get silently swallowed. None of the 11
    existing Case 1-11 fixtures naturally reaches 3 remaining causes
    (checked directly, not assumed), so this reuses
    AMBIGUOUS_REVENUE_RECOGNITION's own real 3-cause scenario under a
    synthetic is_ambiguous=False entry -- the same underlying failure,
    the opposite ambiguity flag."""
    synthetic_technical_entry = BenchmarkEntry(
        scenario=AMBIGUOUS_REVENUE_RECOGNITION,
        ground_truth_check_field="reconciliation",
        is_ambiguous=False,
        expected_behavior="answer",
    )

    with pytest.raises(ValueError, match="remaining cross-source causes for scenario"):
        assemble_investigation_evidence_for_benchmark(synthetic_technical_entry)

    # Confirm it's the exact same failure assemble_investigation_evidence itself raises.
    with pytest.raises(ValueError, match="remaining cross-source causes for scenario"):
        assemble_investigation_evidence(AMBIGUOUS_REVENUE_RECOGNITION)


def test_assemble_investigation_evidence_unaffected_for_all_11_cases():
    """assemble_investigation_evidence itself is unmodified by this task --
    confirm by rerunning every Case 1-11 fixture directly (not through the
    wrapper), not by inspecting the diff alone. Every case must still
    either resolve to a real float residual or (Case 5) a fully-unexplained
    known_gap, with the sum-check invariant holding exactly, same as
    before this task."""
    for scenario in SCENARIOS:
        evidence = assemble_investigation_evidence(scenario)
        assert isinstance(evidence.unexplained_residual, float)
        total = sum(item.dollar_impact for item in evidence.reconciliation)
        assert total + evidence.unexplained_residual == scenario.known_gap
