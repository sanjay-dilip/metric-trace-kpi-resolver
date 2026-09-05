"""Issue #171: a checkpointing variant of run_decision5_gate.py, written
because two full 24-scenario attempts were killed mid-run by a host-level
low-memory safety trigger (documented risk: decision 41's own "keep free
RAM above ~2GB" working rule) -- the original script holds all results in
memory and only prints/computes thresholds at the very end, so a kill
loses 100% of a run's progress, not just the unfinished part.

Runs a single contiguous slice of BENCHMARK_ENTRIES (by index, both ends
inclusive of start, exclusive of end -- standard Python slicing) and
writes each scenario's raw ScenarioScore fields to a JSON file as soon as
it's scored, so a kill only loses scenarios not yet written. Same two
real Ollama calls per scenario (explain_investigation + assess_confidence)
as the original script -- no scoring logic duplicated or changed, only
score_scenario's already-existing output serialized to disk.

Usage: python scripts/run_decision5_gate_partial.py START END OUTPUT.json
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import assess_confidence, explain_investigation
from src.reconciliation_assembly import is_data_quality_dispatched
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES
from tests.fixtures.benchmark_pipeline import assemble_investigation_evidence_for_benchmark
from tests.fixtures.eval_scoring import score_scenario


def main() -> None:
    start, end, output_path = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    entries = BENCHMARK_ENTRIES[start:end]
    results: list[dict] = []

    if output_path.exists():
        results = json.loads(output_path.read_text())
        already_done = {r["scenario_id"] for r in results}
        entries = [e for e in entries if e.scenario.scenario_id not in already_done]
        print(f"Resuming {output_path}: {len(already_done)} scenario(s) already recorded, {len(entries)} remaining.")

    batch_start = time.monotonic()
    for i, entry in enumerate(entries, start=1):
        scenario_id = entry.scenario.scenario_id
        dispatched = is_data_quality_dispatched(scenario_id)
        print(f"\n[{i}/{len(entries)} this batch] {scenario_id} (is_ambiguous={entry.is_ambiguous}) -- calling Ollama...")

        evidence = assemble_investigation_evidence_for_benchmark(entry)

        t0 = time.monotonic()
        prose = explain_investigation(evidence, data_quality_checked=dispatched)
        t1 = time.monotonic()
        confidence = assess_confidence(evidence, data_quality_checked=dispatched)
        t2 = time.monotonic()

        score = score_scenario(entry, prose, confidence)

        print(
            f"  explain={t1 - t0:.1f}s confidence={t2 - t1:.1f}s "
            f"| root_cause_correct={score.root_cause_correct} "
            f"| confidence={confidence.confidence} escalation_status={score.escalation_status} "
            f"| unsupported_claim_patterns={score.unsupported_claim_patterns}"
        )

        results.append(
            {
                "scenario_id": score.scenario_id,
                "is_ambiguous": entry.is_ambiguous,
                "root_cause_correct": score.root_cause_correct,
                "escalation_status": score.escalation_status,
                "unsupported_claim_patterns": score.unsupported_claim_patterns,
            }
        )
        output_path.write_text(json.dumps(results, indent=2))

    print(f"\nBatch done: {len(entries)} scenario(s), {time.monotonic() - batch_start:.1f}s wall clock.")
    print(f"Wrote {len(results)} total scenario(s) to {output_path}")


if __name__ == "__main__":
    main()
