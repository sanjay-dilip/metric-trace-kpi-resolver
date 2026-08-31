"""Tests for tests.fixtures.eval_scoring (Build 3, Day 4, Part 6). Every
test here uses fixed, hand-written prose strings against real BenchmarkEntry
fixtures -- score_scenario never calls the LLM, so this file costs zero API
calls, per Task 2's own explicit requirement. Task 3's real, live
run_benchmark() verification is reported by hand in the PR, not automated
here -- matching this project's own standing practice (test_explainer.py's
own docstring states the same split for _format_evidence_prompt)."""

from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES
from tests.fixtures.eval_scoring import score_scenario

_ENTRY_BY_ID = {e.scenario.scenario_id: e for e in BENCHMARK_ENTRIES}


def test_correct_prose_scores_root_cause_correct_case_1():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = (
        "The gap is explained by a join-type mismatch: source A uses a LEFT JOIN while "
        "source B uses an INNER JOIN on the same condition, which has a dollar impact of "
        "$300.00. No other differences were found, and the unexplained residual is $0.00."
    )
    score = score_scenario(entry, prose)
    assert score.root_cause_correct is True
    assert score.unsupported_claim_patterns == []
    assert "root_cause" in score.checks_run


def test_wrong_dollar_figure_scores_root_cause_incorrect():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = (
        "The gap is explained by a join-type mismatch: source A uses a LEFT JOIN while "
        "source B uses an INNER JOIN on the same condition, which has a dollar impact of "
        "$250.00."
    )
    score = score_scenario(entry, prose)
    assert score.root_cause_correct is False


def test_missing_cause_scores_root_cause_incorrect():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = "No cause could be determined for this discrepancy."
    score = score_scenario(entry, prose)
    assert score.root_cause_correct is False


def test_negative_control_correctly_scored_when_no_cause_asserted():
    entry = _ENTRY_BY_ID["case_06_negative_control"]
    prose = "The two sources agree exactly on this metric. No discrepancy was found."
    score = score_scenario(entry, prose)
    assert score.root_cause_correct is True


def test_negative_control_scores_incorrect_when_a_fabricated_cause_is_asserted():
    entry = _ENTRY_BY_ID["case_06_negative_control"]
    prose = "A join-type mismatch explains the gap, with a dollar impact of $150.00."
    score = score_scenario(entry, prose)
    assert score.root_cause_correct is False


def test_multi_line_item_reconciliation_requires_every_cause_and_figure():
    """Case 2's own BenchmarkEntry notes: the correct answer is split across
    two reconciliation line items that only sum to known_gap together --
    a scorer checking only one is a false positive."""
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    complete_prose = (
        "Two causes explain the gap. First, excluded_statuses differs between the two "
        "sources, contributing +$120.00. Second, the aggregation method differs (count "
        "vs count_distinct), contributing -$100.00."
    )
    assert score_scenario(entry, complete_prose).root_cause_correct is True

    partial_prose = (
        "The excluded_statuses field differs between the two sources, contributing "
        "+$120.00 to the gap."
    )
    assert score_scenario(entry, partial_prose).root_cause_correct is False


def test_reconciliation_and_data_quality_both_required_for_case_20():
    """Case 20's own new ground_truth_check_field: a scorer must verify
    BOTH the reconciliation finding AND the data-quality finding, since
    both are real and separately correct (decision 17's legibility risk)."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    complete_prose = (
        "Two separate findings explain this investigation. A join-type difference "
        "(source A's LEFT JOIN vs source B's INNER JOIN) has a dollar impact of $300.00. "
        "Separately, a stale extract on source A accounts for -$200.00. These are two "
        "distinct, independently-computed findings, not one combined total."
    )
    assert score_scenario(entry, complete_prose).root_cause_correct is True

    only_join_prose = (
        "A join-type difference (source A's LEFT JOIN vs source B's INNER JOIN) has a "
        "dollar impact of $300.00."
    )
    assert score_scenario(entry, only_join_prose).root_cause_correct is False

    only_stale_prose = "A stale extract on source A accounts for -$200.00."
    assert score_scenario(entry, only_stale_prose).root_cause_correct is False


# --- Unsupported-claim pattern detection: one test per named pattern, ---
# --- against realistic prose, not a synthetic string built to trivially ---
# --- match a regex. ---


def test_detects_sign_dropping_on_realistic_prose():
    """Modeled directly on decision 14's own quoted Case 2 defect: the
    model states a negative dollar_impact's magnitude with no minus sign
    and no qualifying word."""
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    prose = (
        "Two causes explain the gap. First, excluded_statuses differs, contributing "
        "$120.00. Second, the difference in aggregation methods, with source A using "
        "'count_distinct' and source B using 'count', has a dollar impact of $100.00."
    )
    score = score_scenario(entry, prose)
    assert "sign_dropping" in score.unsupported_claim_patterns
    assert "sign_dropping" in score.checks_run


def test_does_not_flag_sign_dropping_when_qualifier_word_present():
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    prose = (
        "Two causes explain the gap. First, excluded_statuses differs, contributing "
        "$120.00. Second, the aggregation method reduces the gap by $100.00."
    )
    score = score_scenario(entry, prose)
    assert "sign_dropping" not in score.unsupported_claim_patterns


def test_does_not_flag_sign_dropping_when_minus_sign_present():
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    prose = (
        "Two causes explain the gap. First, excluded_statuses differs, contributing "
        "$120.00. Second, the aggregation method contributes -$100.00."
    )
    score = score_scenario(entry, prose)
    assert "sign_dropping" not in score.unsupported_claim_patterns


def test_detects_hedge_then_retract_on_realistic_prose():
    """Modeled directly on decision 14's own quoted Case 4 defect text."""
    entry = _ENTRY_BY_ID["case_04_governance_drift"]
    prose = (
        "We have also identified a reconciled cause that is a result of definitional "
        "differences between source A and source B. However, this cause does not exist, "
        "and the dollar impact is zero. The real cause is a self-consistency issue: "
        "source A's own SQL implements excluded_statuses='cancelled', contradicting its "
        "own declared 'cancelled, refunded', with a dollar impact of $200.00."
    )
    score = score_scenario(entry, prose)
    assert "hedge_then_retract" in score.unsupported_claim_patterns
    assert "hedge_then_retract" in score.checks_run


def test_does_not_flag_hedge_then_retract_on_clean_prose():
    entry = _ENTRY_BY_ID["case_04_governance_drift"]
    prose = (
        "Source A's own SQL implements excluded_statuses='cancelled', contradicting its "
        "own declared 'cancelled, refunded', with a dollar impact of $200.00. No other "
        "differences were found."
    )
    score = score_scenario(entry, prose)
    assert "hedge_then_retract" not in score.unsupported_claim_patterns


def test_detects_residual_self_contradiction_on_realistic_prose():
    """Modeled directly on decision 30's own quoted Case 10 defect text:
    the correcting phrase and a violating phrase both present in the same
    response."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "A join-type difference has a dollar impact of $300.00. A stale extract on "
        "source A accounts for -$200.00, and we can say that it accounts for a portion "
        "of the gap. However, the unexplained residual does not include or account for "
        "the data-quality issues listed -- a nonzero residual is not evidence that a "
        "data-quality cause only partially explains the gap."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns
    assert "residual_self_contradiction" in score.checks_run


def test_does_not_flag_residual_self_contradiction_on_clean_prose():
    """Case 8's own clean-pass live output shape (decision 30): only the
    correcting phrase appears, never a violating one."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "A join-type difference has a dollar impact of $300.00. A stale extract on "
        "source A accounts for -$200.00. This is a separate fact from the reconciled "
        "cause and does not include or account for the data-quality issues listed."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns


def test_does_not_flag_residual_self_contradiction_when_only_violating_phrase_present():
    """A response that only ever violates the constraint (never recites it
    correctly) is a different, not-yet-named defect shape -- this pattern
    specifically means co-occurrence, not either phrase alone."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "A join-type difference has a dollar impact of $300.00. A stale extract on "
        "source A accounts for -$200.00, but there may be other causes that are not "
        "yet accounted for."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns


def test_escalation_status_always_not_gradable():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.")
    assert score.escalation_status == "not_gradable"

    ambiguous_entry = _ENTRY_BY_ID["ambiguous_refund_timing"]
    ambiguous_score = score_scenario(
        ambiguous_entry, "A date-field difference has a dollar impact of $350.00."
    )
    assert ambiguous_score.escalation_status == "not_gradable"


def test_checks_run_omits_patterns_with_no_applicable_evidence():
    """Case 1 has no negative dollar figure and no data_quality_issues, so
    sign_dropping and residual_self_contradiction are never attempted --
    but it DOES have a real empty-vs-populated category split (sql_differences
    and reconciliation are non-empty; definition_differences,
    self_consistency_issues, and data_quality_issues are all empty), so
    hedge_then_retract legitimately does run."""
    entry = _ENTRY_BY_ID["case_01_join_type"]
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.")
    assert score.checks_run == ["root_cause", "hedge_then_retract"]
    assert "sign_dropping" not in score.checks_run
    assert "residual_self_contradiction" not in score.checks_run


# --- Regression tests for four real bugs Task 3's live verification found ---
# --- before its numbers were trustworthy enough to report. ---


def test_empty_reconciliation_can_be_the_correct_ground_truth():
    """Bug 1: an empty reconciliation list was treated as automatically
    incorrect, but Case 5's own designed shape -- a real, nonzero known_gap
    with NO findable cause -- makes an empty list the correct answer."""
    entry = _ENTRY_BY_ID["case_05_unexplained_residual"]
    correct_prose = (
        "No reconciled cause was found for this investigation. The unexplained residual "
        "is +$3,800.00, and the entire gap remains unexplained."
    )
    assert score_scenario(entry, correct_prose).root_cause_correct is True

    fabricated_prose = (
        "A join-type mismatch explains the gap, with a dollar impact of $3,800.00."
    )
    assert score_scenario(entry, fabricated_prose).root_cause_correct is False


def test_dollar_figure_matches_without_a_dollar_sign():
    """Bug 2: the regex originally required a literal '$', but this task's
    own locked spec names '$300.00 vs 300.0' as acceptable tolerance."""
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = "A join-type mismatch has a dollar impact of 300.00, with no other causes."
    assert score_scenario(entry, prose).root_cause_correct is True


def test_dollar_figure_matches_without_decimals_when_dollar_sign_present():
    """Bug 3: after fixing Bug 2, decimals became mandatory even with a
    literal '$' present, so bare '$120' (no '.00') went unmatched --
    live verification found the model writes this shape often."""
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = "A join-type mismatch has a dollar impact of $300, with no other causes."
    assert score_scenario(entry, prose).root_cause_correct is True


def test_aggregation_keyword_does_not_false_match_inside_summary():
    """Bug 4: plain substring matching found 'sum' (an aggregation synonym)
    inside the unrelated word 'summary' -- live verification's own Case 5
    prose closed with 'In summary, ...' and was scored incorrect as a
    result, even though it correctly asserted no cause was found."""
    entry = _ENTRY_BY_ID["case_05_unexplained_residual"]
    prose = (
        "No reconciled cause was found for this investigation. The unexplained residual "
        "is +$3,800.00. In summary, the entire gap remains unexplained."
    )
    assert score_scenario(entry, prose).root_cause_correct is True


def test_excluded_statuses_keyword_matches_inside_literal_snake_case_token():
    """Related to Bug 4's fix: '_' is a word character to regex \\b, so
    'status' could never \\b-match inside a literal 'excluded_statuses'
    token without normalizing underscores to spaces first -- live
    verification's own model output writes this literal token often."""
    entry = _ENTRY_BY_ID["case_16_excluded_statuses_declared"]
    prose = (
        "The excluded_statuses field differs: source A declares 'cancelled', source B "
        "declares 'cancelled, refunded', with a dollar impact of $300.00."
    )
    assert score_scenario(entry, prose).root_cause_correct is True


def test_violating_phrase_negation_is_not_flagged_as_self_contradiction():
    """A related false positive found in the same live run: 'no further
    investigation is needed' contains the violating phrase 'further
    investigation is needed' as a literal substring, but the leading 'no'
    reverses it into a correct statement, not a contradiction."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "A join-type difference has a dollar impact of $300.00. A stale extract on "
        "source A accounts for -$200.00. This data-quality issue accounts for the entire "
        "gap; the residual does not include or account for the data-quality issues listed, "
        "and no further investigation is needed to explain the remainder."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns
