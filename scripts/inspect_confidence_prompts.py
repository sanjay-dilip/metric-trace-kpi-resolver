"""One-off inspection script (not committed as a permanent tool) for the
Build 4 confidence-batch caveat investigation: renders the exact 12
_format_confidence_prompt() prompts sent during the real batch run
(scripts/run_confidence_batch.py) and reports structural facts about each
-- section order, line counts, and where the "Not checked" data-quality
caveat sits relative to the rest of the block -- without making any new
API calls. Deterministic: assemble_investigation_evidence and
is_data_quality_dispatched are pure functions of the same fixtures."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import _format_confidence_prompt, _format_evidence_block
from src.reconciliation_assembly import assemble_investigation_evidence, is_data_quality_dispatched
from tests.fixtures.ambiguous_scenarios import (
    AMBIGUOUS_ACTIVE_USER_CONVENTION,
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_REFUND_TIMING,
    AMBIGUOUS_REVENUE_RECOGNITION,
)
from tests.fixtures.scenarios import (
    CASE_1_JOIN_TYPE,
    CASE_2_MULTI_CAUSE,
    CASE_5_UNEXPLAINED_RESIDUAL,
    CASE_6_NEGATIVE_CONTROL,
    CASE_16_EXCLUDED_STATUSES_DECLARED,
    CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED,
)

BATCH = [
    AMBIGUOUS_REFUND_TIMING,
    AMBIGUOUS_REVENUE_RECOGNITION,
    AMBIGUOUS_CUSTOMER_COUNTING,
    AMBIGUOUS_ATTRIBUTION,
    AMBIGUOUS_CURRENCY_TIMING,
    AMBIGUOUS_ACTIVE_USER_CONVENTION,
    CASE_2_MULTI_CAUSE,
    CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED,
    CASE_5_UNEXPLAINED_RESIDUAL,
    CASE_1_JOIN_TYPE,
    CASE_16_EXCLUDED_STATUSES_DECLARED,
    CASE_6_NEGATIVE_CONTROL,
]

CITED_CAVEAT = {
    "ambiguous_refund_timing", "ambiguous_revenue_recognition", "ambiguous_customer_counting",
    "ambiguous_attribution", "ambiguous_active_user_convention", "case_02_multi_cause",
    "case_19_date_field_excluded_statuses_declared", "case_01_join_type", "case_16_excluded_statuses_declared",
}

SECTION_HEADERS = [
    "## Reconciled causes",
    "## Definitional differences",
    "## Self-consistency issues",
    "## SQL structural differences",
    "## Data-quality issues",
    "## Unexplained residual",
]


def main() -> None:
    for scenario in BATCH:
        evidence = assemble_investigation_evidence(scenario)
        dispatched = is_data_quality_dispatched(scenario.scenario_id)
        block = _format_evidence_block(evidence, dispatched)
        prompt = _format_confidence_prompt(evidence, dispatched)
        lines = block.split("\n")

        section_line_nums = {}
        for i, line in enumerate(lines):
            for h in SECTION_HEADERS:
                if line.startswith(h):
                    section_line_nums[h] = i

        empty_sections = sum(
            1
            for h in SECTION_HEADERS[:-1]
            if any(
                lines[j].strip().startswith("- None") or lines[j].strip().startswith("- Not checked")
                for j in range(section_line_nums[h], section_line_nums.get(SECTION_HEADERS[SECTION_HEADERS.index(h) + 1], len(lines)))
            )
        )

        dq_line_idx = section_line_nums["## Data-quality issues"]
        total_lines = len(lines)
        residual_idx = section_line_nums["## Unexplained residual"]

        print(f"\n=== {scenario.scenario_id} (cited_caveat={scenario.scenario_id in CITED_CAVEAT}) ===")
        print(f"  total evidence-block lines: {total_lines}")
        print(f"  DQ section starts at line {dq_line_idx} (of {total_lines}); residual section at {residual_idx} (last)")
        print(f"  empty/None sections: {empty_sections}/5 (excluding residual)")
        print(f"  reconciliation non-empty: {bool(evidence.reconciliation)} ({len(evidence.reconciliation)} items)")
        print(f"  definition_differences non-empty: {bool(evidence.definition_differences)} ({len(evidence.definition_differences)} items)")
        print(f"  unexplained_residual: {evidence.unexplained_residual}")
        print(f"  full confidence prompt char length: {len(prompt)}")


if __name__ == "__main__":
    main()
