"""Decision 46 (docs/decisions.md): before/after proof that setting
temperature=0 and a fixed seed on the Ollama request (src/llm_client.py)
actually eliminates the non-determinism decision 45's pre-merge follow-up
found (3 of 12 scenarios flipped ConfidenceAssessment.confidence between
two independent runs with no prompt/code change in between).

Reuses the exact same 12-scenario batch composition and order as
scripts/run_confidence_batch.py (decision 38's own batch, reproduced
byte-identically here too) -- the same scenarios the "before" finding was
measured against. Runs that batch TWICE, back to back, against the NEW
deterministic settings, and diffs every scenario's (confidence, reason)
pair between the two runs. If the fix works, every scenario should be
byte-identical run-to-run -- this script proves that directly, not by
inspection of the settings alone.

Not committed as a pytest-collected test -- matches this project's own
standing practice for every live-API-call verification (manual,
reported-by-hand, since it costs real wall-clock CPU inference time)."""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import ConfidenceAssessment, assess_confidence
from src.llm_client import LLMSettings
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


def _run_batch(batch: list[Scenario], run_label: str) -> dict[str, ConfidenceAssessment]:
    results: dict[str, ConfidenceAssessment] = {}
    for i, scenario in enumerate(batch, start=1):
        dispatched = is_data_quality_dispatched(scenario.scenario_id)
        print(f"\n[{run_label} {i}/{len(batch)}] {scenario.scenario_id} -- calling Ollama...")
        evidence = assemble_investigation_evidence(scenario)
        start = time.monotonic()
        assessment = assess_confidence(evidence, data_quality_checked=dispatched)
        elapsed = time.monotonic() - start
        print(f"  -> confidence={assessment.confidence}  ({elapsed:.1f}s)")
        results[scenario.scenario_id] = assessment
    return results


def main() -> None:
    settings = LLMSettings()
    print(f"llm_provider={settings.llm_provider} ollama_temperature={settings.ollama_temperature} ollama_seed={settings.ollama_seed}")
    if settings.llm_provider != "ollama":
        raise RuntimeError("This script only makes sense against the Ollama provider.")

    seed_sample = _seed_42_sample()
    batch: list[Scenario] = _AMBIGUOUS_ORDER + _KNOWN_TRICKY + seed_sample
    assert len(batch) == 12

    batch_start = time.monotonic()
    run_a = _run_batch(batch, "RUN A")
    run_b = _run_batch(batch, "RUN B")
    total_elapsed = time.monotonic() - batch_start

    print(f"\n\n=== Determinism comparison, {total_elapsed:.1f}s total wall clock ===")
    label_matches = 0
    reason_matches = 0
    for scenario in batch:
        sid = scenario.scenario_id
        a, b = run_a[sid], run_b[sid]
        label_match = a.confidence == b.confidence
        reason_match = a.reason == b.reason
        label_matches += int(label_match)
        reason_matches += int(reason_match)
        print(f"{sid}")
        print(f"  RUN A: confidence={a.confidence} | reason={a.reason!r}")
        print(f"  RUN B: confidence={b.confidence} | reason={b.reason!r}")
        print(f"  label_match={label_match} reason_match={reason_match}")

    print(f"\nLabel matches: {label_matches}/{len(batch)}")
    print(f"Reason (verbatim) matches: {reason_matches}/{len(batch)}")


if __name__ == "__main__":
    main()
