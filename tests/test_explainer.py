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
