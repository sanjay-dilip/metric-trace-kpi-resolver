"""Tests for src.explainer (Build 1, Week 2 Day 1): the LLM explainer.
The Gemini call itself is mocked here (Level 1 of the workflow guide's
live-API-test policy) -- these tests prove the prompt is built from
evidence's actual field values, not that Gemini's real output is grounded.
The manual, per-fixture grounding check against real Gemini output is Part
3 of the Day 8 task and is not automated here (documented, not skipped)."""

from unittest.mock import MagicMock

from src import explainer
from src.explainer import (
    ESCALATION_STATEMENT,
    ConfidenceAssessment,
    _format_confidence_prompt,
    _format_evidence_block,
    _format_evidence_prompt,
    assess_confidence,
    explain_investigation,
)
from src.schema import (
    DataQualityIssue,
    DefinitionDifference,
    InvestigationEvidence,
    ReconciliationLineItem,
    SelfConsistencyIssue,
    SQLStructuralDifference,
)
from tests.fixtures.benchmark_pipeline import PartialInvestigationEvidence

_EMPTY_EVIDENCE = InvestigationEvidence(
    definition_differences=[],
    self_consistency_issues=[],
    sql_differences=[],
    data_quality_issues=[],
    escalations=[],
    reconciliation=[],
    unexplained_residual=0.0,
)


def test_prompt_includes_every_reconciliation_line_item():
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "reconciliation": [
                ReconciliationLineItem(cause="join_type mismatch", dollar_impact=300.0, computed_by="single_cause_attribution")
            ]
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "join_type mismatch" in prompt
    assert "+300.00" in prompt
    assert "single_cause_attribution" in prompt


def test_prompt_includes_every_definition_difference():
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "definition_differences": [
                DefinitionDifference(
                    field="excluded_statuses",
                    source_a_value="churned",
                    source_b_value="churned,trial",
                    source="declared",
                    confidence="high",
                )
            ]
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "excluded_statuses" in prompt
    assert "churned" in prompt
    assert "declared" in prompt


def test_prompt_includes_every_self_consistency_issue():
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "self_consistency_issues": [
                SelfConsistencyIssue(
                    source="a",
                    declared_field="excluded_statuses",
                    declared_value="churned",
                    implemented_value="churned,trial",
                    confidence="high",
                    dollar_impact=200.0,
                )
            ]
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "declared_field=excluded_statuses" in prompt
    assert "+200.00" in prompt


def test_prompt_includes_every_sql_structural_difference():
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "sql_differences": [
                SQLStructuralDifference(
                    category="join_type",
                    description="source_a uses LEFT JOIN, source_b uses INNER JOIN",
                    query_a_snippet="LEFT JOIN",
                    query_b_snippet="INNER JOIN",
                )
            ]
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "category=join_type" in prompt
    assert "LEFT JOIN, source_b uses INNER JOIN" in prompt


def test_prompt_states_unexplained_residual_value():
    evidence = _EMPTY_EVIDENCE.model_copy(update={"unexplained_residual": 3800.0})
    prompt = _format_evidence_prompt(evidence)
    assert "+3800.00" in prompt


def test_prompt_includes_every_data_quality_issue():
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "data_quality_issues": [
                DataQualityIssue(
                    category="stale_extract",
                    source="a",
                    description="source_a's extract is missing 1 row present in the complete snapshot",
                    confidence="high",
                    dollar_impact=-150.0,
                )
            ]
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "category=stale_extract" in prompt
    assert "source=a" in prompt
    assert "-150.00" in prompt


def test_prompt_defaults_to_checked_and_clean_when_data_quality_checked_omitted():
    """Build 3, Day 5, Part 4: data_quality_checked defaults to True --
    the backward-compatible default for every existing caller/test that
    has no real dispatch status to thread through. _EMPTY_EVIDENCE has no
    data_quality_issues, so omitting the new parameter must render the
    original 'checked and found no issues' framing, not the new 'never
    checked' one."""
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE)
    assert "None. A data-quality/freshness check was run for this investigation and found no issues." in prompt
    assert "Not checked" not in prompt


def test_prompt_states_never_checked_when_data_quality_checked_is_false():
    """Build 3, Day 5, Part 4 (decision 36's finding #3, Case 3's own
    quoted defect): the actual fix. When data_quality_checked=False and
    data_quality_issues is empty, the prompt must state plainly that no
    check was ever run -- not the same 'found no issues' framing a
    genuinely checked-and-clean scenario gets."""
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE, data_quality_checked=False)
    assert (
        "Not checked. No data-quality/freshness check has been run for this investigation "
        "-- this is NOT confirmation that the data is fresh, complete, or clean, only that "
        "no such check exists for it yet." in prompt
    )
    assert "A data-quality/freshness check was run for this investigation and found no issues." not in prompt


def test_prompt_instructs_against_claiming_freshness_when_never_checked():
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE, data_quality_checked=False)
    assert (
        "Do NOT state or imply that the data is fresh, complete, clean, or free of "
        "data-quality issues" in prompt
    )


def test_prompt_omits_never_checked_instruction_when_data_quality_checked_true():
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE, data_quality_checked=True)
    assert "Do NOT state or imply that the data is fresh" not in prompt


def test_prompt_never_checked_framing_does_not_apply_when_issues_are_actually_present():
    """data_quality_checked=False is meaningless once real issues exist --
    a non-empty data_quality_issues list already proves a check ran,
    regardless of what data_quality_checked says. The real dispatch
    status and a real issue list can never actually disagree in
    practice (is_data_quality_dispatched and _resolve_data_quality_issues
    are driven by the same table), but the rendering logic itself must
    not silently trust a wrong data_quality_checked=False over the real,
    populated list it was actually given."""
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "data_quality_issues": [
                DataQualityIssue(
                    category="stale_extract",
                    source="a",
                    description="source_a's extract is missing 1 row",
                    confidence="high",
                    dollar_impact=-150.0,
                )
            ],
        }
    )
    prompt = _format_evidence_prompt(evidence, data_quality_checked=False)
    assert "category=stale_extract" in prompt
    assert "Not checked" not in prompt
    assert "Do NOT state or imply that the data is fresh" not in prompt


def test_prompt_instructs_not_to_sum_data_quality_and_reconciliation_when_both_present():
    """Case 20's shape: reconciliation and data_quality_issues both non-empty --
    decision 17's own named risk (docs/decisions.md), now reachable in the prompt."""
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "reconciliation": [
                ReconciliationLineItem(cause="join_type mismatch", dollar_impact=300.0, computed_by="single_cause_attribution")
            ],
            "data_quality_issues": [
                DataQualityIssue(
                    category="referential_integrity",
                    source="a",
                    description="orphan row",
                    confidence="high",
                    dollar_impact=300.0,
                )
            ],
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "Do NOT add a data-quality issue's dollar_impact to a reconciled cause's dollar_impact" in prompt


def test_prompt_instructs_data_quality_cause_is_real_even_when_reconciliation_empty():
    """Cases 8-11's shape: data_quality_issues non-empty, reconciliation empty --
    decision 14's defect-1 pattern (an empty category narrated as 'does not
    exist'), applied to this field."""
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "data_quality_issues": [
                DataQualityIssue(
                    category="stale_extract",
                    source="a",
                    description="source_a's extract is missing 1 row",
                    confidence="high",
                    dollar_impact=-150.0,
                )
            ],
            "unexplained_residual": -150.0,
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "does NOT mean nothing was found" in prompt
    assert "do not describe this investigation as having found no cause" in prompt
    # The general "no cause identified" instruction is scoped to "no data-quality
    # issues" so it doesn't contradict the data-quality-specific instruction above.
    assert "no reconciled causes, no data-quality issues, and a nonzero residual" in prompt


def test_prompt_instructs_residual_does_not_excuse_a_data_quality_cause_as_partial():
    """Build 3, Day 4, Part 1 follow-up: live verification found the model
    correctly avoided the two named traps but still framed every
    data-quality cause as only 'partially' explaining the gap, reasoning
    from the nonzero residual. This third instruction fires whenever
    data_quality_issues is non-empty, regardless of reconciliation state."""
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "data_quality_issues": [
                DataQualityIssue(
                    category="stale_extract",
                    source="a",
                    description="source_a's extract is missing 1 row",
                    confidence="high",
                    dollar_impact=-150.0,
                )
            ],
            "unexplained_residual": -150.0,
        }
    )
    prompt = _format_evidence_prompt(evidence)
    assert "does NOT include or account for the data-quality issues listed" in prompt
    assert "NOT evidence that a data-quality cause only partially explains the gap" in prompt


def test_prompt_omits_data_quality_residual_instruction_when_no_data_quality_issues():
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE)
    assert "does NOT include or account for the data-quality issues listed" not in prompt


def test_prompt_includes_escalation_check_against_partial_investigation_evidence():
    """Build 3, Day 4, Part 4 Task 1's own explicit requirement: confirm
    directly, not assume, that the escalation instruction renders against
    PartialInvestigationEvidence the same way it does InvestigationEvidence."""
    partial = PartialInvestigationEvidence(
        definition_differences=[
            DefinitionDifference(
                field="date_field",
                source_a_value="order_date",
                source_b_value="created_at",
                source="declared",
                confidence="high",
            )
        ],
        self_consistency_issues=[],
        sql_differences=[],
        data_quality_issues=[],
    )
    prompt = _format_evidence_prompt(partial)
    assert "ESCALATION CHECK" in prompt
    assert ESCALATION_STATEMENT in prompt


def test_prompt_handles_none_residual_without_crashing():
    """PartialInvestigationEvidence (tests/fixtures/benchmark_pipeline.py)
    defaults unexplained_residual to None, honestly reflecting that the
    ambiguous-scenario wrapper's escalated path never computes it.
    _format_evidence_prompt previously crashed with
    TypeError: unsupported format string passed to NoneType.__format__."""
    partial = PartialInvestigationEvidence(
        definition_differences=[],
        self_consistency_issues=[],
        sql_differences=[],
        data_quality_issues=[],
    )
    prompt = _format_evidence_prompt(partial)
    assert "Not computed" in prompt
    assert "financial gap could not be fully reconciled" in prompt
    # The value-dependent residual instructions must not appear when there is no value.
    assert "If it is not (near) zero" not in prompt
    assert "no reconciled causes, no data-quality issues, and a nonzero residual" not in prompt
    assert "no reconciled causes, no data-quality issues, and the residual is" not in prompt


def test_prompt_includes_the_escalation_check_and_exact_statement():
    """Build 3, Day 4, Part 4 (design probe, not a trusted feature): the
    escalation instruction and ESCALATION_STATEMENT's exact text must
    always be present, regardless of evidence shape, since the model is
    meant to judge eligibility from the evidence alone, not from a
    Python-computed gate."""
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE)
    assert "ESCALATION CHECK" in prompt
    assert ESCALATION_STATEMENT in prompt
    assert "TWO OR MORE entries" in prompt
    assert "Do NOT escalate merely because" in prompt


def test_prompt_instructs_against_fabricating_a_cause_when_none_found():
    prompt = _format_evidence_prompt(_EMPTY_EVIDENCE)
    assert "None. No reconciled cause was found for this investigation." in prompt
    assert "Do not fabricate a cause" in prompt


def test_explain_investigation_calls_generate_structured_with_no_schema_and_returns_its_text(monkeypatch):
    captured = {}

    def fake_generate_structured(prompt, response_schema=None):
        captured["prompt"] = prompt
        captured["response_schema"] = response_schema
        return "mock prose explanation"

    monkeypatch.setattr(explainer, "generate_structured", fake_generate_structured)

    result = explain_investigation(_EMPTY_EVIDENCE)

    assert result == "mock prose explanation"
    assert captured["response_schema"] is None
    assert "no discrepancy was found" in captured["prompt"]


def test_explain_investigation_threads_data_quality_checked_through_to_the_prompt(monkeypatch):
    """Build 3, Day 5, Part 4: explain_investigation's own new parameter
    must actually reach _format_evidence_prompt, not just exist on the
    outer function's signature."""
    captured = {}

    def fake_generate_structured(prompt, response_schema=None):
        captured["prompt"] = prompt
        return "mock prose explanation"

    monkeypatch.setattr(explainer, "generate_structured", fake_generate_structured)

    explain_investigation(_EMPTY_EVIDENCE, data_quality_checked=False)

    assert "Not checked" in captured["prompt"]


# --- Build 4, Day 1, Part 1: the confidence self-report step. ---


def test_confidence_prompt_reuses_the_same_evidence_block_the_explainer_sees():
    """The actual, explicit requirement: assess_confidence's own prompt
    must be built from the SAME rendered facts _format_evidence_prompt
    uses, not a re-derived or re-summarized version of them."""
    evidence = _EMPTY_EVIDENCE.model_copy(
        update={
            "reconciliation": [
                ReconciliationLineItem(cause="join_type mismatch", dollar_impact=300.0, computed_by="single_cause_attribution")
            ]
        }
    )
    block = _format_evidence_block(evidence)
    confidence_prompt = _format_confidence_prompt(evidence)

    assert block in confidence_prompt


def test_confidence_prompt_does_not_include_explainer_specific_instructions():
    """Deliberately does NOT reuse _format_evidence_prompt wholesale --
    its own instructions (write an explanation, the escalation check, the
    2-4 paragraph format) would directly contradict this step's own
    "respond with only a JSON object" instruction if both were present."""
    confidence_prompt = _format_confidence_prompt(_EMPTY_EVIDENCE)
    assert "ESCALATION CHECK" not in confidence_prompt
    assert "write 2-4 short paragraphs" not in confidence_prompt
    assert ESCALATION_STATEMENT not in confidence_prompt


def test_confidence_prompt_instructs_a_one_sentence_reason_and_json_only_response():
    confidence_prompt = _format_confidence_prompt(_EMPTY_EVIDENCE)
    assert "ONLY a single JSON object" in confidence_prompt
    assert "must be exactly one sentence" in confidence_prompt


def test_confidence_prompt_never_includes_explanation_text_or_a_ground_truth_label():
    """No parameter path exists for either -- confirmed by signature
    inspection via a direct call: assess_confidence/_format_confidence_prompt
    take only `evidence` (and data_quality_checked), never prior
    explanation text or an ambiguity label, matching decision 31's own
    evidence-only, no-label-access convention."""
    import inspect

    params = list(inspect.signature(_format_confidence_prompt).parameters)
    assert params == ["evidence", "data_quality_checked"]


def test_assess_confidence_parses_clean_json_response(monkeypatch):
    def fake_generate_structured(prompt, response_schema=None):
        assert response_schema is None  # prompt-instructed JSON, not response_format (decision 33)
        return '{"confidence": "high", "reason": "Every cause is declared, high-confidence, and the residual is zero."}'

    monkeypatch.setattr(explainer, "generate_structured", fake_generate_structured)

    result = assess_confidence(_EMPTY_EVIDENCE)

    assert result == ConfidenceAssessment(
        confidence="high", reason="Every cause is declared, high-confidence, and the residual is zero."
    )


def test_assess_confidence_strips_markdown_json_fence(monkeypatch):
    """Real models sometimes wrap JSON in ```json ... ``` despite explicit
    instructions not to -- matching score_scenario_llm_graded's own
    already-proven need for this same guard."""

    def fake_generate_structured(prompt, response_schema=None):
        return '```json\n{"confidence": "low", "reason": "Multiple ambiguous signals with no clear resolution."}\n```'

    monkeypatch.setattr(explainer, "generate_structured", fake_generate_structured)

    result = assess_confidence(_EMPTY_EVIDENCE)

    assert result.confidence == "low"


def test_assess_confidence_threads_data_quality_checked_through_to_the_prompt(monkeypatch):
    captured = {}

    def fake_generate_structured(prompt, response_schema=None):
        captured["prompt"] = prompt
        return '{"confidence": "medium", "reason": "Some uncertainty remains."}'

    monkeypatch.setattr(explainer, "generate_structured", fake_generate_structured)

    assess_confidence(_EMPTY_EVIDENCE, data_quality_checked=False)

    assert "Not checked" in captured["prompt"]
