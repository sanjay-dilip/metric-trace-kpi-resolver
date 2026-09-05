"""Issue #173: proof plan for the cardinality-claim extractor
(src.explainer.extract_claimed_counts) before it is trusted for anything.

Runs extract_claimed_counts against six already-captured prose fragments,
all real, verbatim quotes already on record in docs/decisions.md -- no new
explainer generation, no new InvestigationEvidence construction. The real
category counts these transcripts describe (correctly or not) are hard-
coded below from the same already-published decision-log entries, not
re-derived here.

1. Case 20 (decisions 36/37, finding #1's own source) -- the positive case:
   the transcript this whole mechanism targets, claiming a count of 2
   against a real data_quality_issues length of 1.
2. Case 8 (decision 30) -- no count claim anywhere (singular references
   only, no explicit numeral or quantity word).
3. Case 9 (decision 30) -- same, no count claim.
4. case_16 (decision 42, Part 3 addendum) -- a stated count of zero
   ("did not identify any"), itself false against a real length of 1 --
   tests that extraction reports the CLAIM, not the truth.
5. ambiguous_revenue_recognition (decision 42, Part 3 addendum) -- two
   stated counts of zero, both false against real lengths of 2 and 0
   respectively (self_consistency_issues is genuinely empty for this
   scenario; only definition_differences' zero-claim is false).
6. ambiguous_active_user_convention (decision 42's own confidence-batch
   table) -- a correct stated count of 1, plus a genuinely ambiguous
   second clause ("did not account for X") not confidently classifiable
   as a zero-count claim or a bare existence complaint -- reported as-is,
   not forced into a pass/fail bucket.

Not committed as a pytest-collected test -- matches this project's own
standing practice for every live-API-call verification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import ClaimedCategoryCounts, extract_claimed_counts

_CASES: list[tuple[str, str, dict]] = [
    (
        "case_20_finding_1_positive_case",
        "Additionally, there are two data-quality issues that affect the discrepancy. In the "
        "case of source A, the as-delivered snapshot has 3 rows in 'orders', but the complete "
        "counterfactual snapshot has 4 rows.",
        {
            "real_data_quality_issues_len": 1,
            "expected": ClaimedCategoryCounts(
                reconciliation=None, data_quality_issues=2, definition_differences=None, self_consistency_issues=None
            ),
        },
    ),
    (
        "case_08_no_count_claim",
        "This data-quality issue accounts for the entire gap, and we have a confirmed cause for "
        "the discrepancy. The unexplained residual is -$150.00, which is a separate fact from "
        "the reconciled cause. It does not indicate the existence of other causes or the need "
        "for further investigation. The residual is simply a remaining amount that is not "
        "explained by the data-quality issue.",
        {
            "expected": ClaimedCategoryCounts(
                reconciliation=None, data_quality_issues=None, definition_differences=None, self_consistency_issues=None
            ),
        },
    ),
    (
        "case_09_no_count_claim",
        "The unexplained residual figure is -$250.00. This means that even after accounting for "
        "the data-quality issue, there is still a gap that remains unexplained. However, it's "
        "essential to note that the residual does not necessarily imply the existence of other "
        "causes or the need for further investigation.",
        {
            "expected": ClaimedCategoryCounts(
                reconciliation=None, data_quality_issues=None, definition_differences=None, self_consistency_issues=None
            ),
        },
    ),
    (
        "case_16_false_zero_claim",
        "The investigation did not identify any reconciled causes with a significant dollar impact.",
        {
            "real_reconciliation_len": 1,
            "expected": ClaimedCategoryCounts(
                reconciliation=0, data_quality_issues=None, definition_differences=None, self_consistency_issues=None
            ),
        },
    ),
    (
        "ambiguous_revenue_recognition_false_zero_claims",
        "It did not identify any definitional differences or self-consistency issues.",
        {
            "real_definition_differences_len": 2,
            "real_self_consistency_issues_len": 0,
            "expected": ClaimedCategoryCounts(
                reconciliation=None, data_quality_issues=None, definition_differences=0, self_consistency_issues=0
            ),
        },
    ),
    (
        "ambiguous_active_user_convention_correct_claim_plus_ambiguous_clause",
        "The investigation only found a single reconciled cause, but did not account for "
        "definitional differences or data-quality issues, which may be contributing to the "
        "discrepancy.",
        {
            "real_reconciliation_len": 1,
            "expected_reconciliation": 1,
            "note": "definition_differences/data_quality_issues clause is genuinely ambiguous -- "
            "reported as-is below, not scored pass/fail.",
        },
    ),
]


def main() -> None:
    print(f"Running {len(_CASES)} cases through extract_claimed_counts (real Ollama calls)...\n")
    for name, explanation, meta in _CASES:
        result = extract_claimed_counts(explanation)
        print(f"=== {name} ===")
        print(f"  explanation: {explanation!r}")
        print(f"  extracted:   {result.model_dump()}")
        if "expected" in meta:
            expected = meta["expected"]
            match = result == expected
            print(f"  expected:    {expected.model_dump()}")
            print(f"  MATCH: {match}")
        else:
            print(f"  expected_reconciliation: {meta.get('expected_reconciliation')}")
            print(f"  note: {meta.get('note')}")
        for k, v in meta.items():
            if k.startswith("real_"):
                print(f"  {k} = {v}")
        print()


if __name__ == "__main__":
    main()
