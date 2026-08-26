"""Build 1, Week 2 Day 1: the LLM explainer, the deterministic core's only
consumer-facing output. Takes a fully-assembled InvestigationEvidence
(src/reconciliation_assembly.py) -- every number here was already computed
by deterministic code before this module ever runs -- and asks Gemini (via
src/llm_client.py, never called directly) to write prose explaining it.

No new pydantic schema for the output: plain text, per the standing
decision from the original project brief. The LLM's only job is narrating
facts that already exist in `evidence`; it must not compute a number, infer
a cause, or invent SQL lineage of its own (the project's deterministic-
core-LLM-at-the-edge convention, now exercised for the first time)."""

from src.llm_client import generate_structured
from src.schema import InvestigationEvidence


def _format_evidence_prompt(evidence: InvestigationEvidence) -> str:
    """Every field of `evidence` is rendered into the prompt verbatim --
    nothing added, nothing summarized away before the LLM sees it (per the
    Day 8 task's own requirement)."""
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
    lines.append(f"## Unexplained residual\n{evidence.unexplained_residual:+.2f}")

    evidence_block = "\n".join(lines)

    return (
        "You are explaining the results of a deterministic KPI-discrepancy investigation "
        "to a business stakeholder. Every fact below was already computed by deterministic "
        "code (SQL parsing, metric-definition comparison, and real query execution) before "
        "you were called -- you are narrating these facts, not computing or discovering "
        "anything new.\n\n"
        f"{evidence_block}\n\n"
        "Instructions:\n"
        "- Reference only the facts listed above. Do not invent a cause, mechanism, or "
        "dollar figure that is not explicitly present here.\n"
        "- State the unexplained residual honestly. If it is not (near) zero, say plainly "
        "that the gap is not fully explained by the causes found -- do not paper over it, "
        "and do not guess at a plausible-sounding reason to fill the gap.\n"
        "- If there are no reconciled causes and a nonzero residual, say explicitly that no "
        "cause was identified and the entire gap remains unexplained. Do not fabricate a "
        "cause to make the explanation feel complete.\n"
        "- If there are no reconciled causes and the residual is zero, say plainly that the "
        "two sources agree and no discrepancy was found.\n"
        "- Write 2-4 short paragraphs in plain business language, no markdown headers."
    )


def explain_investigation(evidence: InvestigationEvidence) -> str:
    """Construct a prompt from `evidence`'s actual field values and return
    Gemini's prose explanation via generate_structured (plain text --
    response_schema left as None, this task's designated use)."""
    prompt = _format_evidence_prompt(evidence)
    result = generate_structured(prompt)
    assert isinstance(result, str)
    return result
