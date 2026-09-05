"""Issue #171: combines the JSON output of one or more
run_decision5_gate_partial.py invocations and computes decision 5's four
thresholds exactly the way scripts/run_decision5_gate.py does -- same
formulas, same threshold values, no scoring logic re-derived here.

Usage: python scripts/combine_decision5_gate_results.py FILE1.json [FILE2.json ...]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES


def main() -> None:
    scores: list[dict] = []
    for path in sys.argv[1:]:
        scores.extend(json.loads(Path(path).read_text()))

    total = len(BENCHMARK_ENTRIES)
    expected_ids = {e.scenario.scenario_id for e in BENCHMARK_ENTRIES}
    got_ids = {s["scenario_id"] for s in scores}
    if got_ids != expected_ids:
        missing = expected_ids - got_ids
        extra = got_ids - expected_ids
        raise RuntimeError(f"Scenario set mismatch. Missing: {missing or None}. Unexpected: {extra or None}.")
    if len(scores) != total:
        raise RuntimeError(f"Duplicate scenario_id(s) across input files: {len(scores)} rows for {total} scenarios.")

    print(f"\n=== Combined per-scenario results ({len(scores)}/{total}) ===")
    for s in scores:
        print(
            f"{s['scenario_id']} | is_ambiguous={s['is_ambiguous']} | "
            f"root_cause_correct={s['root_cause_correct']} | "
            f"escalation_status={s['escalation_status']} | "
            f"unsupported_claim_patterns={s['unsupported_claim_patterns']}"
        )

    root_cause_correct_count = sum(1 for s in scores if s["root_cause_correct"] is True)
    root_cause_accuracy = root_cause_correct_count / total

    ambiguous_scores = [s for s in scores if s["is_ambiguous"]]
    non_ambiguous_scores = [s for s in scores if not s["is_ambiguous"]]

    true_escalations = sum(1 for s in ambiguous_scores if s["escalation_status"] == "true_escalation")
    escalation_recall = true_escalations / len(ambiguous_scores)

    false_escalations = sum(1 for s in non_ambiguous_scores if s["escalation_status"] == "false_escalation")
    false_escalation_rate = false_escalations / len(non_ambiguous_scores)

    unsupported_claim_count = sum(1 for s in scores if s["unsupported_claim_patterns"])
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
