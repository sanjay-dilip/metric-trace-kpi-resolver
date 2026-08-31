"""Tests for src.explainer (Build 1, Week 2 Day 1): the LLM explainer.
The Gemini call itself is mocked here (Level 1 of the workflow guide's
live-API-test policy) -- these tests prove the prompt is built from
evidence's actual field values, not that Gemini's real output is grounded.
The manual, per-fixture grounding check against real Gemini output is Part
3 of the Day 8 task and is not automated here (documented, not skipped)."""

from unittest.mock import MagicMock

from src import explainer
from src.explainer import _format_evidence_prompt, explain_investigation
from src.schema import (
    DataQualityIssue,
    DefinitionDifference,
    InvestigationEvidence,
    ReconciliationLineItem,
    SelfConsistencyIssue,
    SQLStructuralDifference,
)

_EMPTY_EVIDENCE = InvestigationEvidence(
    definition_differences=[],
    self_consistency_issues=[],
    sql_differences=[],
    data_quality_issues=[],
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
