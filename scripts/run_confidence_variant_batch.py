"""Build 4, confidence-step evidence-structure test (decision 42 addendum):
tests whether the data-quality "Not checked" caveat's dominance in
assess_confidence's responses (decision 42) is a consequence of it being
BUNDLED inside the same evidence block as the causal evidence, rather than
a wording or placement artifact within that block (both already ruled out
by scripts/inspect_confidence_prompts.py).

Two new prompt variants, built here only -- src/explainer.py's
_format_confidence_prompt (baseline, already measured in decision 42) and
_format_evidence_block (used by the explainer too, must stay untouched)
are NOT modified. Both variants reuse the exact same evidence-fact text
_format_evidence_block already renders -- only its arrangement changes:

- Variant 1 (omission): the "## Data-quality issues" section is removed
  entirely from the block before it's shown to the model. The model judges
  confidence in the causal evidence never having seen the caveat at all.
- Variant 2 (separation): the caveat is removed from the evidence block
  the same way, but reintroduced as a short, clearly-labeled note AFTER
  the full confidence-judgment request (including the JSON-output
  instruction) -- testing whether it's the caveat's presence at all, or
  specifically its positional/contextual competition with the causal
  evidence, that drives citation.

Only scenarios where evidence.data_quality_issues is empty AND
data_quality_checked is False are affected by either variant (the only
case _format_evidence_block renders the "Not checked" caveat at all) --
true for all 12 scenarios in this batch, confirmed by decision 42's own
batch (every scenario there logged dq_checked=False).

Not committed as a pytest-collected test -- matches this project's own
standing practice for every live-API-call verification: a manual,
reported-by-hand invocation, since it costs real (CPU-bound Ollama)
inference time."""

import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.explainer import ConfidenceAssessment, _format_evidence_block
from src.llm_client import generate_structured
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
_KNOWN_TRICKY: list[Scenario] = [CASE_2_MULTI_CAUSE, CASE_19_DATE_FIELD_EXCLUDED_STATUSES_DECLARED]

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_CONFIDENCE_TASK_PREAMBLE = (
    "You are assessing your OWN confidence in how complete and correct a deterministic "
    "KPI-discrepancy investigation's findings are, based ONLY on the evidence below. "
    "Every fact below was already computed by deterministic code (SQL parsing, "
    "metric-definition comparison, and real query execution) -- you are not computing or "
    "discovering anything new, only judging how confident a reader should be that these "
    "findings are a complete and correct account of the discrepancy.\n\n"
)
_CONFIDENCE_TASK_INSTRUCTION = (
    "Respond with ONLY a single JSON object, no other text before or after it, no "
    "markdown code fences, with EXACTLY these two fields: "
    '{"confidence": "low"|"medium"|"high", "reason": "..."}. '
    'The "reason" field must be exactly one sentence, not a paragraph -- state the single '
    "biggest factor driving your confidence level, in plain business language."
)
_SEPARATED_NOTE = (
    "Note: data-quality checks were not run for this scenario -- this does not bear on the "
    "causal evidence above."
)


def _seed_42_sample() -> list[Scenario]:
    pool = [s for s in SCENARIOS if s.scenario_id not in {sc.scenario_id for sc in _KNOWN_TRICKY}]
    random.seed(42)
    return random.sample(pool, 4)


def _strip_dq_section(block: str) -> str:
    """Removes the '## Data-quality issues' section (header through its one
    content line) from a rendered _format_evidence_block output, leaving
    every other section -- including the blank-line spacing convention
    the rest of the block already uses -- untouched. Relies on the DQ
    section always being followed by a blank line then '## Unexplained
    residual', per _format_evidence_block's own fixed section order
    (confirmed identical across all 12 scenarios by
    scripts/inspect_confidence_prompts.py)."""
    dq_marker = "## Data-quality issues"
    residual_marker = "## Unexplained residual"
    dq_idx = block.index(dq_marker)
    residual_idx = block.index(residual_marker)
    before = block[:dq_idx].rstrip("\n")
    after = block[residual_idx:]
    return f"{before}\n\n{after}"


def _extract_dq_caveat_present(evidence, data_quality_checked: bool) -> bool:
    """True only when _format_evidence_block would have rendered the
    'Not checked' caveat -- the one case both variants act on."""
    return not evidence.data_quality_issues and not data_quality_checked


def build_omission_prompt(evidence, data_quality_checked: bool = True) -> str:
    block = _format_evidence_block(evidence, data_quality_checked)
    if _extract_dq_caveat_present(evidence, data_quality_checked):
        block = _strip_dq_section(block)
    return _CONFIDENCE_TASK_PREAMBLE + block + "\n\n" + _CONFIDENCE_TASK_INSTRUCTION


def build_separated_prompt(evidence, data_quality_checked: bool = True) -> str:
    block = _format_evidence_block(evidence, data_quality_checked)
    caveat_present = _extract_dq_caveat_present(evidence, data_quality_checked)
    if caveat_present:
        block = _strip_dq_section(block)
    prompt = _CONFIDENCE_TASK_PREAMBLE + block + "\n\n" + _CONFIDENCE_TASK_INSTRUCTION
    if caveat_present:
        prompt += "\n\n" + _SEPARATED_NOTE
    return prompt


def _call(prompt: str) -> ConfidenceAssessment:
    raw = generate_structured(prompt)
    assert isinstance(raw, str)
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()
    return ConfidenceAssessment.model_validate_json(cleaned)


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
        raise RuntimeError(f"seed-42 sample mismatch: got {actual_ids}, expected {expected_ids}")

    batch: list[Scenario] = _AMBIGUOUS_ORDER + _KNOWN_TRICKY + seed_sample
    assert len(batch) == 12

    results = []
    for i, scenario in enumerate(batch, start=1):
        dispatched = is_data_quality_dispatched(scenario.scenario_id)
        evidence = assemble_investigation_evidence(scenario)

        print(f"\n[{i}/12] {scenario.scenario_id} -- variant 1 (omission)...")
        start = time.monotonic()
        a1 = _call(build_omission_prompt(evidence, dispatched))
        e1 = time.monotonic() - start
        print(f"  -> {a1.confidence}  ({e1:.1f}s)  {a1.reason}")

        print(f"[{i}/12] {scenario.scenario_id} -- variant 2 (separation)...")
        start = time.monotonic()
        a2 = _call(build_separated_prompt(evidence, dispatched))
        e2 = time.monotonic() - start
        print(f"  -> {a2.confidence}  ({e2:.1f}s)  {a2.reason}")

        results.append((scenario.scenario_id, a1.confidence, a1.reason, e1, a2.confidence, a2.reason, e2))

    print("\n\n=== Summary (12 scenarios x 2 variants = 24 calls) ===")
    for scenario_id, c1, r1, e1, c2, r2, e2 in results:
        print(f"{scenario_id}")
        print(f"  omission:   {c1} ({e1:.1f}s) -- {r1}")
        print(f"  separation: {c2} ({e2:.1f}s) -- {r2}")


if __name__ == "__main__":
    main()
