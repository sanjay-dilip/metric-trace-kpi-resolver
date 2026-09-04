"""Build 4, Day 1 Part 3 (decision 38's deferred restart, on Ollama):
re-runs the 12-scenario confidence-assessment batch from scratch against
the new default provider (Ollama, decision 41). Not committed as a
pytest-collected test -- matches this project's own standing practice
for every prior live-API-call verification (Build 1 Week 2 Day 1
onward): a manual, reported-by-hand invocation, since it costs real
(here, real wall-clock CPU) inference time, not a repeatable-in-CI check.

Batch composition and order are decision 38's own, reproduced exactly,
not re-rolled: the 6 committed ambiguous BenchmarkEntry scenarios (in
BENCHMARK_ENTRIES' own declared order), then case_02_multi_cause,
case_19_date_field_excluded_statuses_declared, then the 4 scenarios
drawn by random.seed(42)+random.sample() from the 16-scenario SCENARIOS
pool minus those two -- case_05_unexplained_residual, case_01_join_type,
case_16_excluded_statuses_declared, case_06_negative_control -- printed
here again only to prove the sample still reproduces byte-identically,
per decision 38 Part 2's own reproducibility requirement, not to
re-derive it fresh."""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import assess_confidence
from src.reconciliation_assembly import assemble_investigation_evidence, is_data_quality_dispatched
from src.scenario import Scenario
from tests.fixtures.ambiguous_scenarios import (
    AMBIGUOUS_ACTIVE_USER_CONVENTION,
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_REFUND_TIMING,
    AMBIGUOUS_REVENUE_RECOGNITION,
)
from tests.fixtures.scenarios import SCENARIOS, CASE_2_MULTI_CAUSE, CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED

_AMBIGUOUS_ORDER: list[Scenario] = [
    AMBIGUOUS_REFUND_TIMING,
    AMBIGUOUS_REVENUE_RECOGNITION,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_ACTIVE_USER_CONVENTION,
]

_KNOWN_TRICKY: list[Scenario] = [
    CASE_2_MULTI_CAUSE,
    CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED,
]


def _seed_42_sample() -> list[Scenario]:
    pool = [s for s in SCENARIOS if s.scenario_id not in {sc.scenario_id for sc in _KNOWN_TRICKY}]
    random.seed(42)
    return random.sample(pool, 4)


def main() -> None:
    seed_sample = _seed_42_sample()
    expected_ids = [
        "case_05_unexplained_residual",
        "case_01_join_type",
        "case_16_excluded_statuses_declared",
        "case_06_negative_control",
    ]
    actual_ids = [s.scenario_id for s in seed_sample]
    if actual_ids != expected_ids:
        raise RuntimeError(
            f"seed-42 sample did not reproduce decision 38's recorded order: got {actual_ids}, expected {expected_ids}"
        )
    print(f"Seed-42 sample reproduced: {actual_ids}")

    batch: list[Scenario] = _AMBIGUOUS_ORDER + _KNOWN_TRICKY + seed_sample
    assert len(batch) == 12

    results = []
    for i, scenario in enumerate(batch, start=1):
        dispatched = is_data_quality_dispatched(scenario.scenario_id)
        print(f"\n[{i}/12] {scenario.scenario_id} (data_quality_checked={dispatched}) -- calling Ollama...")
        start = time.monotonic()
        evidence = assemble_investigation_evidence(scenario)
        assessment = assess_confidence(evidence, data_quality_checked=dispatched)
        elapsed = time.monotonic() - start
        print(f"  -> confidence={assessment.confidence}  ({elapsed:.1f}s)")
        print(f"  reason: {assessment.reason}")
        results.append((scenario.scenario_id, dispatched, assessment.confidence, assessment.reason, elapsed))

    print("\n\n=== Summary (12/12) ===")
    for scenario_id, dispatched, confidence, reason, elapsed in results:
        print(f"{scenario_id} | dq_checked={dispatched} | {confidence} | {elapsed:.1f}s")
        print(f"    {reason}")


if __name__ == "__main__":
    main()
