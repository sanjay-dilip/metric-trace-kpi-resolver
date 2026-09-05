"""Build 4, decision 45: runs decision 5's ablation gate for real, for the
first time, against all 24 BENCHMARK_ENTRIES -- root-cause accuracy,
escalation recall, false-escalation rate, and unsupported-claim rate,
computed together in one pass. Not committed as a pytest-collected test --
matches this project's own standing practice for every prior live-API-call
verification (Build 1 Week 2 Day 1 onward, most recently
scripts/run_confidence_batch.py): a manual, reported-by-hand invocation,
since it costs real (here, real wall-clock CPU) inference time against the
Ollama provider, not a repeatable-in-CI check.

Two real calls per scenario (explain_investigation + assess_confidence),
48 calls total across 24 scenarios -- decision 45's own explicit,
accepted cost, not an oversight. The escalation cutoff itself is NOT new
logic: confidence=="low" -> escalate, "medium"/"high" -> don't, the exact,
already-tested rule decision 42 computed and rejected on a 12-scenario
sample. This script does not retune it -- see docs/decisions.md decision
45 for why that door is closed."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import assess_confidence, explain_investigation
from src.reconciliation_assembly import is_data_quality_dispatched
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES
from tests.fixtures.benchmark_pipeline import assemble_investigation_evidence_for_benchmark
from tests.fixtures.eval_scoring import ScenarioScore, score_scenario


def main() -> None:
    total = len(BENCHMARK_ENTRIES)
    scores: list[ScenarioScore] = []
    batch_start = time.monotonic()

    for i, entry in enumerate(BENCHMARK_ENTRIES, start=1):
        scenario_id = entry.scenario.scenario_id
        dispatched = is_data_quality_dispatched(scenario_id)
        print(
            f"\n[{i}/{total}] {scenario_id} (is_ambiguous={entry.is_ambiguous}, "
            f"data_quality_checked={dispatched}) -- calling Ollama..."
        )

        evidence = assemble_investigation_evidence_for_benchmark(entry)

        t0 = time.monotonic()
        prose = explain_investigation(evidence, data_quality_checked=dispatched)
        t1 = time.monotonic()
        confidence = assess_confidence(evidence, data_quality_checked=dispatched)
        t2 = time.monotonic()

        score = score_scenario(entry, prose, confidence)
        scores.append(score)

        print(
            f"  explain={t1 - t0:.1f}s confidence={t2 - t1:.1f}s "
            f"| root_cause_correct={score.root_cause_correct} "
            f"| confidence={confidence.confidence} escalation_status={score.escalation_status} "
            f"| unsupported_claim_patterns={score.unsupported_claim_patterns}"
        )

    batch_elapsed = time.monotonic() - batch_start
    print(f"\n\n=== Raw per-scenario results ({total}/{total}), {batch_elapsed:.1f}s total wall clock ===")
    for entry, score in zip(BENCHMARK_ENTRIES, scores):
        print(
            f"{score.scenario_id} | is_ambiguous={entry.is_ambiguous} | "
            f"root_cause_correct={score.root_cause_correct} | "
            f"escalation_status={score.escalation_status} | "
            f"unsupported_claim_patterns={score.unsupported_claim_patterns}"
        )

    # --- Decision 5's four thresholds, computed directly from the raw scores above. ---

    root_cause_correct_count = sum(1 for s in scores if s.root_cause_correct is True)
    root_cause_accuracy = root_cause_correct_count / total

    ambiguous_scores = [s for e, s in zip(BENCHMARK_ENTRIES, scores) if e.is_ambiguous]
    non_ambiguous_scores = [s for e, s in zip(BENCHMARK_ENTRIES, scores) if not e.is_ambiguous]

    true_escalations = sum(1 for s in ambiguous_scores if s.escalation_status == "true_escalation")
    escalation_recall = true_escalations / len(ambiguous_scores)

    false_escalations = sum(1 for s in non_ambiguous_scores if s.escalation_status == "false_escalation")
    false_escalation_rate = false_escalations / len(non_ambiguous_scores)

    unsupported_claim_count = sum(1 for s in scores if s.unsupported_claim_patterns)
    unsupported_claim_rate = unsupported_claim_count / total

    print("\n=== Decision 5 gate, all four thresholds ===")
    print(
        f"1. Root-cause accuracy: {root_cause_correct_count}/{total} = {root_cause_accuracy:.1%} "
        f"(threshold: >=80%) -- {'PASS' if root_cause_accuracy >= 0.80 else 'FAIL'}"
    )
    print(
        f"2. Escalation recall (ambiguous subset): {true_escalations}/{len(ambiguous_scores)} = "
        f"{escalation_recall:.1%} (threshold: >=90%) -- {'PASS' if escalation_recall >= 0.90 else 'FAIL'}"
    )
    print(
        f"3. False-escalation rate (non-ambiguous subset): {false_escalations}/{len(non_ambiguous_scores)} = "
        f"{false_escalation_rate:.1%} (threshold, as recorded in decision 6: <=2/15 raw count -- "
        f"note the actual non-ambiguous count is {len(non_ambiguous_scores)}, not 15) -- "
        f"{'PASS' if false_escalations <= 2 else 'FAIL'} (raw-count rule) / "
        f"{'PASS' if false_escalation_rate <= 2 / 15 else 'FAIL'} (2/15 rate applied to this subset size)"
    )
    print(
        f"4. Unsupported-claim rate: {unsupported_claim_count}/{total} = {unsupported_claim_rate:.1%} "
        f"(threshold: <=10%) -- {'PASS' if unsupported_claim_rate <= 0.10 else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
