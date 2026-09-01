"""Build 1, Week 2 Day 1: the LLM explainer, the deterministic core's only
consumer-facing output. Takes a fully-assembled InvestigationEvidence
(src/reconciliation_assembly.py) -- every number here was already computed
by deterministic code before this module ever runs -- and asks Gemini (via
src/llm_client.py, never called directly) to write prose explaining it.

No new pydantic schema for the output: plain text, per the standing
decision from the original project brief. The LLM's only job is narrating
facts that already exist in `evidence`; it must not compute a number, infer
a cause, or invent SQL lineage of its own (the project's deterministic-
core-LLM-at-the-edge convention, now exercised for the first time).

Build 3, Day 4, Part 1: data_quality_issues (present on InvestigationEvidence
since Build 2, Day 1, populated since Build 2, Day 5) is now rendered too,
with explicit prompt instructions covering the two risks decisions 14 and 17
(docs/decisions.md) named: never sum a data_quality_issues dollar figure
against a reconciliation figure as if additive (decision 17), and never
describe an investigation as "no cause found" when reconciliation is empty
but data_quality_issues is not (decision 14's defect-1 pattern, applied to
this field).

Build 3, Day 4, Part 1 follow-up: live verification of the above (real API
calls against Cases 8, 9, 10, 11, 20) surfaced a third, previously-unnamed
defect -- the model correctly avoided both named traps but still
characterized every data-quality cause as only "partially" explaining the
gap, reasoning from unexplained_residual's nonzero value even when a
data_quality_issues figure fully accounts for it. A third instruction now
tells the model explicitly that unexplained_residual never accounts for
data_quality_issues (this project's own additive-only convention, Build 2
Day 5), so a nonzero residual is not grounds to call a data-quality finding
incomplete. The same session also fixed _format_evidence_prompt's crash
against a None unexplained_residual (PartialInvestigationEvidence,
tests/fixtures/benchmark_pipeline.py) with a small guard, not a redesign --
still not exercised by any committed scenario as of this entry.

Build 3, Day 4, Part 4 (DESIGN PROBE, not a trusted feature): an
evidence-only escalation instruction, letting the model itself decide from
`definition_differences` alone (no BenchmarkEntry.is_ambiguous access, no
label of any kind) whether to produce ESCALATION_STATEMENT verbatim instead
of a normal explanation, per decision 6's escalation requirement. This is
explicitly a probe of whether evidence-only recognition is viable at all --
live verification results are reported in this task's own PR, not silently
assumed to work; see docs/decisions.md for the eventual verdict once
written.

Build 3, Day 5, Part 4: closes decision 36's finding #3 (Build 3, Day 5,
Part 3's transcript re-audit). `evidence.data_quality_issues == []` is
ambiguous between two genuinely different states -- "a check was run and
found nothing" versus "no check has ever been dispatched for this
scenario at all" (src/reconciliation_assembly.py's own dispatch-table
limitation, a long-standing named Open Item). The live model response
for Case 3 collapsed that ambiguity into an unsupported affirmative
claim: "The data-quality issues were also found to be zero, indicating
that the data is fresh and complete" -- treating silence as confirmation.
`_format_evidence_prompt` and `explain_investigation` now take an
explicit `data_quality_checked` parameter (threaded from the real
dispatch status at the actual call site, `src.reconciliation_assembly
.is_data_quality_dispatched`, the same "thread a new parameter through
from where the real information lives" pattern decision 24 used for
`other_side_sql`) so the prompt can state the correct one of three
things: real issues found, checked-and-clean, or never checked at all --
with a matching instruction telling the model explicitly that "never
checked" is not evidence of cleanliness."""

import re
from typing import Literal

from pydantic import BaseModel

from src.llm_client import generate_structured
from src.schema import InvestigationEvidence

ESCALATION_STATEMENT = (
    "This investigation involves a genuine business-rule disagreement between the two "
    "sources, not a technical error -- a person familiar with both dashboards' intended "
    "purpose should review it and decide which definition is correct. No further "
    "automated explanation is provided."
)
"""Build 3, Day 4, Part 4 (design probe, not a trusted feature): the exact,
fixed sentence _format_evidence_prompt instructs the model to produce,
verbatim and alone, when it judges a scenario to be genuinely ambiguous --
matching decision 6's own requirement (docs/decisions.md) that escalation be
"one clear, unmistakable statement," never a hedge alongside a partial
answer. Exposed as a module constant (not inlined into the instruction
string) so a caller/checker can compare a response against it exactly,
the same way Build 3's future unsupported-claim-rate checker will need an
exact string to test for, not a fuzzy substring guess."""


def _format_evidence_block(evidence: InvestigationEvidence, data_quality_checked: bool = True) -> str:
    """Every field of `evidence` is rendered into a block of markdown-ish
    text verbatim -- nothing added, nothing summarized away before the LLM
    sees it (per the Day 8 task's own original requirement). Factored out
    of `_format_evidence_prompt` (Build 4, Day 1, Part 1) so a second,
    independent consumer -- `assess_confidence`'s own prompt, below -- can
    see EXACTLY the same rendered evidence the explainer does, without
    duplicating this rendering logic a second time. `_format_evidence_prompt`
    itself is unchanged in behavior by this extraction; it now just calls
    this function for the block it already built inline.

    data_quality_checked (Build 3, Day 5, Part 4): the real dispatch
    status for this scenario -- True when a data-quality/freshness check
    was actually run (found an issue or not; `evidence.data_quality_issues`
    itself already distinguishes those two), False when no check was ever
    dispatched at all (src.reconciliation_assembly.is_data_quality_dispatched).
    Only matters when evidence.data_quality_issues is empty, since a
    non-empty list already proves a check ran. Defaults to True -- the
    correct, backward-compatible default for every existing caller/test
    that doesn't construct a real Scenario and has no dispatch status to
    thread through -- so the "never checked" framing below is opt-in,
    not silently assumed."""
    lines: list[str] = []

    lines.append("## Reconciled causes (each is a confirmed cause with its dollar contribution to the gap)")
    if evidence.reconciliation:
        for item in evidence.reconciliation:
            lines.append(f"- {item.cause} | dollar_impact={item.dollar_impact:+.2f} | computed_by={item.computed_by}")
    else:
        lines.append("- None. No reconciled cause was found for this investigation.")

    lines.append("")
    lines.append("## Definitional differences (metric-definition mismatches between source A and source B)")
    if evidence.definition_differences:
        for d in evidence.definition_differences:
            lines.append(
                f"- field={d.field} | source_a_value={d.source_a_value!r} | source_b_value={d.source_b_value!r} "
                f"| source={d.source} | confidence={d.confidence}"
            )
    else:
        lines.append("- None.")

    lines.append("")
    lines.append("## Self-consistency issues (a source's own SQL contradicts that same source's declared definition)")
    if evidence.self_consistency_issues:
        for issue in evidence.self_consistency_issues:
            lines.append(
                f"- source={issue.source} | declared_field={issue.declared_field} "
                f"| declared_value={issue.declared_value!r} | implemented_value={issue.implemented_value!r} "
                f"| confidence={issue.confidence} | dollar_impact={issue.dollar_impact:+.2f}"
            )
    else:
        lines.append("- None.")

    lines.append("")
    lines.append("## SQL structural differences (independent of metric-definition semantics)")
    if evidence.sql_differences:
        for s in evidence.sql_differences:
            lines.append(f"- category={s.category} | {s.description}")
    else:
        lines.append("- None.")

    lines.append("")
    lines.append(
        "## Data-quality issues (freshness/completeness defects, computed independently of the "
        "reconciled causes above -- see the instructions below for how these relate)"
    )
    if evidence.data_quality_issues:
        for q in evidence.data_quality_issues:
            lines.append(
                f"- category={q.category} | source={q.source} | {q.description} "
                f"| confidence={q.confidence} | dollar_impact={q.dollar_impact:+.2f}"
            )
    elif data_quality_checked:
        lines.append("- None. A data-quality/freshness check was run for this investigation and found no issues.")
    else:
        lines.append(
            "- Not checked. No data-quality/freshness check has been run for this investigation "
            "-- this is NOT confirmation that the data is fresh, complete, or clean, only that "
            "no such check exists for it yet."
        )

    lines.append("")
    if evidence.unexplained_residual is None:
        lines.append(
            "## Unexplained residual\nNot computed -- this investigation could not be fully "
            "reconciled (see the findings above for what was determined)."
        )
    else:
        lines.append(f"## Unexplained residual\n{evidence.unexplained_residual:+.2f}")

    return "\n".join(lines)


def _format_evidence_prompt(evidence: InvestigationEvidence, data_quality_checked: bool = True) -> str:
    """Builds the explainer's full prompt: `_format_evidence_block`'s
    rendered facts, followed by the explainer-specific instructions (write
    an explanation, the escalation check, etc.) -- see that function's own
    docstring for `data_quality_checked`'s meaning, threaded straight
    through unchanged."""
    evidence_block = _format_evidence_block(evidence, data_quality_checked)

    instructions = [
        "Instructions:",
        "- Reference only the facts listed above. Do not invent a cause, mechanism, or "
        "dollar figure that is not explicitly present here.",
    ]

    if evidence.unexplained_residual is None:
        instructions.append(
            "- The unexplained residual was not computed for this investigation (see above). "
            "Do not state a residual figure, and do not assume it is zero or any other value "
            "-- say plainly, based only on the findings actually listed above, that the full "
            "financial gap could not be fully reconciled with the tools available."
        )
    else:
        instructions.append(
            "- State the unexplained residual honestly. If it is not (near) zero, say plainly "
            "that the gap is not fully explained by the causes found -- do not paper over it, "
            "and do not guess at a plausible-sounding reason to fill the gap."
        )

    if evidence.unexplained_residual is not None:
        instructions += [
            "- If there are no reconciled causes, no data-quality issues, and a nonzero "
            "residual, say explicitly that no cause was identified and the entire gap remains "
            "unexplained. Do not fabricate a cause to make the explanation feel complete.",
            "- If there are no reconciled causes, no data-quality issues, and the residual is "
            "zero, say plainly that the two sources agree and no discrepancy was found.",
        ]

    if evidence.data_quality_issues and evidence.reconciliation:
        instructions.append(
            "- The reconciled causes and the data-quality issues above are SEPARATE, "
            "INDEPENDENTLY-COMPUTED findings. They may describe overlapping or related "
            "underlying facts. Do NOT add a data-quality issue's dollar_impact to a "
            "reconciled cause's dollar_impact as if they were two additive causes -- report "
            "them as two distinct findings, each in its own right, and do not state or imply "
            "a combined total between them."
        )
    elif evidence.data_quality_issues and not evidence.reconciliation:
        instructions.append(
            "- There are no reconciled causes above, but this does NOT mean nothing was "
            "found. A real, quantified cause is listed under data-quality issues. Explain "
            "that cause normally, the way you would explain any other finding -- do not "
            "describe this investigation as having found no cause, and do not say the gap "
            "is entirely unexplained when a data-quality issue accounts for it."
        )

    if not evidence.data_quality_issues and not data_quality_checked:
        instructions.append(
            "- No data-quality/freshness check has been run for this investigation (see the "
            "Data-quality issues section above, marked \"Not checked\"). Do NOT state or imply "
            "that the data is fresh, complete, clean, or free of data-quality issues -- that "
            "would be a claim this investigation never actually checked, not a fact it "
            "confirmed. Simply omit any claim about data quality, or state plainly that no "
            "such check was performed for this investigation."
        )

    if evidence.data_quality_issues:
        instructions.append(
            "- The unexplained residual figure above does NOT include or account for the "
            "data-quality issues listed -- by this project's own accounting convention, a "
            "data-quality issue's dollar_impact is never subtracted out of the residual. A "
            "nonzero residual is therefore NOT evidence that a data-quality cause only "
            "partially explains the gap, and it is not grounds to say other causes may exist "
            "or that further investigation is needed. Explain each data-quality issue as a "
            "complete, confirmed finding in its own right, and discuss the residual (if "
            "nonzero) only as a separate fact about what the reconciled causes above do not "
            "cover -- do not connect the two."
        )

    instructions.append(
        "- ESCALATION CHECK (evaluate this before writing your explanation): if, and only "
        "if, the metric-definition differences above include TWO OR MORE entries that are "
        "ALL source='declared' (not 'inferred') AND ALL confidence='high', AND you judge "
        "that each side's declared value plausibly represents a real, defensible business "
        "convention for this metric -- not simply one side being wrong, outdated, or a "
        "technical mistake -- then this may be a genuine business-rule disagreement rather "
        "than a technical bug. In that case, and ONLY in that case, respond with EXACTLY "
        f"the following sentence and nothing else -- no other paragraph, no hedge, no "
        f"partial explanation: \"{ESCALATION_STATEMENT}\" Do NOT escalate merely because a "
        "confidence level is low, because a value looks unclear or hard to interpret, or "
        "because this investigation is incomplete (for example, the unexplained residual "
        "could not be computed) -- those situations call for stating your uncertainty about "
        "the cause in a normal explanation, not for this escalation statement. If the "
        "escalation criteria above are not clearly met, do not escalate under any "
        "circumstance -- write your normal explanation instead."
    )
    instructions.append(
        "- Unless the escalation statement above applies, write 2-4 short paragraphs in "
        "plain business language, no markdown headers."
    )

    return (
        "You are explaining the results of a deterministic KPI-discrepancy investigation "
        "to a business stakeholder. Every fact below was already computed by deterministic "
        "code (SQL parsing, metric-definition comparison, and real query execution) before "
        "you were called -- you are narrating these facts, not computing or discovering "
        "anything new.\n\n"
        f"{evidence_block}\n\n" + "\n".join(instructions)
    )


def explain_investigation(evidence: InvestigationEvidence, data_quality_checked: bool = True) -> str:
    """Construct a prompt from `evidence`'s actual field values and return
    Gemini's prose explanation via generate_structured (plain text --
    response_schema left as None, this task's designated use).

    data_quality_checked: see _format_evidence_prompt's own docstring --
    threaded straight through, unmodified, to the one function that
    actually uses it."""
    prompt = _format_evidence_prompt(evidence, data_quality_checked)
    result = generate_structured(prompt)
    assert isinstance(result, str)
    return result


class ConfidenceAssessment(BaseModel):
    """Build 4, Day 1, Part 1: the confidence self-report step's output.
    Schema-enforced structured output (this project's own standing rule),
    not scraped from free text -- validated the same way
    `LLMClaimGrading` (`tests/fixtures/eval_scoring.py`) is, via
    prompt-instructed JSON + manual `model_validate_json`, not
    `generate_structured`'s own `response_schema` parameter. That
    parameter is deliberately NOT used here: decision 33 (`docs/decisions.md`)
    already found, by live testing, that the configured free-tier model
    rejects `response_format=json_schema` outright for this exact kind of
    call -- the same provider/model limitation, not re-tested here since
    it was already proven and the workaround it required is already this
    project's own established pattern."""

    confidence: Literal["low", "medium", "high"]
    reason: str
    """One sentence, not a paragraph -- bounded by the prompt instruction
    itself (`_format_confidence_prompt`, below), not enforced by the
    schema (a plain `str` field cannot enforce sentence count on its own;
    the instruction is the only real bound, matching this project's own
    "instruct in the prompt, don't over-engineer the schema" convention
    for every other free-text field, e.g. `DataQualityIssue.description`)."""


_CONFIDENCE_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _format_confidence_prompt(evidence: InvestigationEvidence, data_quality_checked: bool = True) -> str:
    """Build 4, Day 1, Part 1's own prompt -- reuses `_format_evidence_block`
    (the SAME rendered facts `_format_evidence_prompt` builds for the
    explainer) rather than duplicating that rendering a second time, per
    this task's own explicit requirement. Deliberately does NOT reuse
    `_format_evidence_prompt` itself: that function's own instructions
    (write an explanation, the escalation check, the 2-4 paragraph
    format) are explainer-specific and would directly contradict this
    step's own "respond with only a JSON object" instruction if both were
    present in the same prompt.

    No explanation text is ever passed in here -- only the same rendered
    evidence the explainer itself sees, never `explain_investigation`'s
    own output. This is deliberate, not an oversight: decision 31
    (`docs/decisions.md`) chose evidence-only recognition to test first,
    before assuming a model needs to see its own prior explanation to
    judge confidence in the underlying evidence. No label of any kind
    (e.g. `BenchmarkEntry.is_ambiguous`) is available here either, same
    standing convention decision 31's own probe established -- the whole
    point is testing whether the model can tell without being told."""
    evidence_block = _format_evidence_block(evidence, data_quality_checked)
    return (
        "You are assessing your OWN confidence in how complete and correct a deterministic "
        "KPI-discrepancy investigation's findings are, based ONLY on the evidence below. "
        "Every fact below was already computed by deterministic code (SQL parsing, "
        "metric-definition comparison, and real query execution) -- you are not computing or "
        "discovering anything new, only judging how confident a reader should be that these "
        "findings are a complete and correct account of the discrepancy.\n\n"
        f"{evidence_block}\n\n"
        "Respond with ONLY a single JSON object, no other text before or after it, no "
        "markdown code fences, with EXACTLY these two fields: "
        '{"confidence": "low"|"medium"|"high", "reason": "..."}. '
        'The "reason" field must be exactly one sentence, not a paragraph -- state the single '
        "biggest factor driving your confidence level, in plain business language."
    )


def assess_confidence(evidence: InvestigationEvidence, data_quality_checked: bool = True) -> ConfidenceAssessment:
    """Build 4, Day 1, Part 1: a new, independent step, deliberately not a
    parameter folded into `explain_investigation` -- this needs to run as
    its own distinct call, not the same generation as the explanation, so
    it can be evaluated (and, in a future part, gated on) separately from
    whether the explanation itself was produced.

    Runs UNCONDITIONALLY on every scenario passed to it -- no Python-computed
    eligibility gate deciding whether to bother calling this. Decision 31's
    own evidence-only escalation probe already showed what a narrow,
    criteria-based eligibility rule costs (it structurally excluded every
    single-cause ambiguous scenario, Pattern 1, regardless of model
    behavior) -- that shape is deliberately not reintroduced here. Whether
    and where a cutoff gets built on top of this step's own output is a
    separate, later decision, not made by this function.

    data_quality_checked: same meaning as `explain_investigation`'s own
    parameter (see `_format_evidence_block`'s docstring) -- threaded
    through so both steps see the identical rendered evidence for the
    same scenario, never a different rendering of it."""
    prompt = _format_confidence_prompt(evidence, data_quality_checked)
    raw = generate_structured(prompt)
    assert isinstance(raw, str)
    cleaned = _CONFIDENCE_JSON_FENCE_RE.sub("", raw).strip()
    return ConfidenceAssessment.model_validate_json(cleaned)
