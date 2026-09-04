"""Tests for tests.fixtures.eval_scoring (Build 3, Day 4, Part 6). Every
test here uses fixed, hand-written prose strings against real BenchmarkEntry
fixtures -- score_scenario never calls the LLM, so this file costs zero API
calls, per Task 2's own explicit requirement. Task 3's real, live
run_benchmark() verification is reported by hand in the PR, not automated
here -- matching this project's own standing practice (test_explainer.py's
own docstring states the same split for _format_evidence_prompt)."""

from src.explainer import ConfidenceAssessment
from tests.fixtures import eval_scoring
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES
from tests.fixtures.eval_scoring import LLMClaimGrading, score_scenario, score_scenario_llm_graded

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


def test_flags_residual_self_contradiction_on_a_one_sided_violation():
    """Build 3, Day 4, Part 8, Task 1 fix: a response that only ever
    violates the constraint (never recites it correctly) used to be
    missed entirely, since the original design required co-occurrence.
    Decision 32's own fresh Case 20 re-test found exactly this shape live
    -- a bare violating phrase is now sufficient on its own."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "A join-type difference has a dollar impact of $300.00. A stale extract on "
        "source A accounts for -$200.00, but there may be other causes that are not "
        "yet accounted for."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns


def test_escalation_status_not_gradable_when_no_confidence_passed():
    """The pre-existing, still-default behavior: score_scenario without a
    `confidence` argument never grades escalation, exactly as before this
    field became gradable at all."""
    entry = _ENTRY_BY_ID["case_01_join_type"]
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.")
    assert score.escalation_status == "not_gradable"

    ambiguous_entry = _ENTRY_BY_ID["ambiguous_refund_timing"]
    ambiguous_score = score_scenario(
        ambiguous_entry, "A date-field difference has a dollar impact of $350.00."
    )
    assert ambiguous_score.escalation_status == "not_gradable"


def test_escalation_status_true_escalation_on_ambiguous_low_confidence():
    entry = _ENTRY_BY_ID["ambiguous_refund_timing"]
    confidence = ConfidenceAssessment(confidence="low", reason="Genuine business-rule ambiguity.")
    score = score_scenario(entry, "A date-field difference has a dollar impact of $350.00.", confidence)
    assert score.escalation_status == "true_escalation"


def test_escalation_status_missed_escalation_on_ambiguous_medium_confidence():
    entry = _ENTRY_BY_ID["ambiguous_refund_timing"]
    confidence = ConfidenceAssessment(confidence="medium", reason="Some uncertainty remains.")
    score = score_scenario(entry, "A date-field difference has a dollar impact of $350.00.", confidence)
    assert score.escalation_status == "missed_escalation"


def test_escalation_status_false_escalation_on_non_ambiguous_low_confidence():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    confidence = ConfidenceAssessment(confidence="low", reason="Some caveat unrelated to ambiguity.")
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.", confidence)
    assert score.escalation_status == "false_escalation"


def test_escalation_status_correct_no_escalation_on_non_ambiguous_high_confidence():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    confidence = ConfidenceAssessment(confidence="high", reason="Clean, fully-reconciled finding.")
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.", confidence)
    assert score.escalation_status == "correct_no_escalation"


def test_checks_run_omits_patterns_with_no_applicable_evidence():
    """Case 1 has no negative dollar figure, so sign_dropping is never
    attempted. It DOES have a real empty-vs-populated category split
    (sql_differences and reconciliation are non-empty; definition_differences,
    self_consistency_issues, and data_quality_issues are all empty), so
    hedge_then_retract legitimately does run. fact_doubling always runs
    (Build 3, Day 4, Part 8), the same way root_cause always does.

    Build 3, Day 5, Part 4: residual_self_contradiction and
    data_quality_overclaim BOTH now legitimately run here too --
    Case 1's own unexplained_residual is exactly 0.0 (decision 36's
    finding #2's widened gate) and Case 1 is genuinely undispatched
    (decision 36's finding #3's new check) -- this is the correct,
    intended behavior of both widenings, not a regression to work around.

    Decision 44: existence_overclaim also now runs here. Nothing about
    Case 1's own evidence changed by this addition -- its reconciliation
    was already non-empty before decision 44 existed. What changed is
    score_scenario itself: it gained a new gate (`if any([bool(evidence
    .reconciliation), ...])`) that did not exist before, and that gate's
    condition is evaluated against Case 1's own already-true, unchanged
    fact. The trigger for this list growing is the new code path, not a
    new fact about Case 1 -- the same distinction this project's own
    decision log makes explicit elsewhere between "restated" and
    "recomputed."
    """
    entry = _ENTRY_BY_ID["case_01_join_type"]
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.")
    assert score.checks_run == [
        "root_cause",
        "fact_doubling",
        "hedge_then_retract",
        "residual_self_contradiction",
        "data_quality_overclaim",
        "existence_overclaim",
    ]
    assert "sign_dropping" not in score.checks_run


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


# --- Build 3, Day 4, Part 8, Task 1: regression tests for the three gaps ---
# --- decision 32's fresh re-test diagnosed. ---


def test_case_10_compound_negation_no_longer_false_positives():
    """The exact real false positive decision 32 found: a compound 'not X
    or that Y' construction where the negating word sits well outside the
    old 20-character window. Modeled directly on Case 10's own fresh
    live-verification quote."""
    entry = _ENTRY_BY_ID["case_10_referential_integrity"]
    prose = (
        "This data-quality issue is a confirmed cause of the discrepancy, and it fully "
        "explains the gap. The unexplained residual is +$300.00, but this is not evidence "
        "that additional causes exist or that further investigation is needed. The "
        "data-quality issue is a complete and confirmed finding in its own right."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns


def test_case_10_negation_guard_still_catches_a_real_short_distance_violation():
    """Regression guard: the switch from a fixed window to sentence-scoping
    must not lose the original, already-proven-working short-distance
    case -- a genuine violation with no negation anywhere nearby must
    still be flagged."""
    entry = _ENTRY_BY_ID["case_10_referential_integrity"]
    prose = (
        "This data-quality issue is a confirmed cause, but only accounts for a portion "
        "of the gap. There may be other factors at play."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns


def test_case_20_one_sided_violation_now_detected_from_real_fresh_prose():
    """Modeled directly on Case 20's own fresh live-verification quote
    (decision 32): a pure, one-sided violation with no correcting phrase
    anywhere in the response."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "The unexplained residual is -$200.00. This means that the reconciled causes "
        "and data-quality issues above do not fully explain the gap, and there may be "
        "other underlying causes that have not been identified. The unexplained residual "
        "remains, and further investigation is needed to fully understand the underlying "
        "causes of the gap."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns


# --- Build 3, Day 5, Part 1: regression tests for decision 33's own named ---
# --- follow-up -- the hand-written phrase list's paraphrase-sensitivity gap. ---
# --- Every prose string below is quoted verbatim from an already-recorded ---
# --- live transcript (docs/decisions.md decision 30, or PR #136's 6-scenario ---
# --- comparison run), not newly generated or synthetic. ---


def test_case_20_paraphrase_violation_from_comparison_run_now_detected():
    """The actual gap decision 33 named: PR #136's fresh Case 20 comparison-
    run transcript (Build 3, Day 4, Part 8, Task 3) violates the residual-
    framing constraint using wording the fixed phrase list did not cover --
    'not accounted for' (no 'yet', so it missed 'not yet accounted for')
    and 'the entire gap remains unexplained' (not on the list at all).
    This is a fixed-phrase-list coverage gap, not the co-occurrence gap --
    that was already fixed in Build 3, Day 4, Part 8, Task 1 (decision 32),
    see test_case_20_one_sided_violation_now_detected_from_real_fresh_prose
    above, which already passes against the OLDER Case 20 quote."""
    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    prose = (
        "The investigation into the discrepancy between the two sources found that the "
        "main cause of the gap is the difference in join types used by the two sources. "
        "Source A uses a LEFT JOIN, while source B uses an INNER JOIN on the same join "
        "condition (o.customer_id = c.customer_id), resulting in a dollar impact of "
        "+$300.00.\n\n"
        "In addition to the reconciled causes, the investigation also identified a "
        "data-quality issue. Source A's as-delivered snapshot has 3 rows in 'orders', "
        "but the complete counterfactual snapshot has 4 rows, resulting in a +1 row(s) "
        "difference. This data-quality issue has a confidence level of high and a "
        "dollar impact of -$200.00.\n\n"
        "The unexplained residual of -$200.00 indicates that the gap is not fully "
        "explained by the causes found. This means that there may be other underlying "
        "causes that are not accounted for in this investigation.\n\n"
        "No cause was identified for the remaining gap, and the entire gap remains "
        "unexplained."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns


def test_case_8_clean_pass_still_not_flagged_after_paraphrase_fix():
    """Regression guard: Case 8's original decision-30 clean-pass transcript
    (Build 3, Day 4, Part 1's live verification) must still score zero false
    positives after adding the two new phrases -- it never claims a cause is
    unaccounted for or that a gap remains unexplained."""
    entry = _ENTRY_BY_ID["case_08_stale_extract"]
    prose = (
        "This data-quality issue accounts for the entire gap, and we have a confirmed "
        "cause for the discrepancy. The unexplained residual is -$150.00, which is a "
        "separate fact from the reconciled cause. It does not indicate the existence "
        "of other causes or the need for further investigation. The residual is simply "
        "a remaining amount that is not explained by the data-quality issue."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns


def test_case_9_borderline_transcript_still_not_flagged_after_paraphrase_fix():
    """Regression guard, the one this fix's own scoping had to be most
    careful about: Case 9's original decision-30 transcript ('there is
    still a gap that remains unexplained') is worded about the RESIDUAL
    remaining unexplained, not about un-accounted-for CAUSES -- decision 30
    itself calls this borderline wording, not a real violation. The new
    'gap remains unexplained' phrase requires exact adjacency and must NOT
    match Case 9's 'gap that remains unexplained' (a 'that' sits between
    the two words) -- if it did, this would be a brand-new false positive
    this fix introduced, not a fix."""
    entry = _ENTRY_BY_ID["case_09_missing_partition"]
    prose = (
        "The unexplained residual figure is -$250.00. This means that even after "
        "accounting for the data-quality issue, there is still a gap that remains "
        "unexplained. However, it's essential to note that the residual does not "
        "necessarily imply the existence of other causes or the need for further "
        "investigation."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns


def test_case_10_still_flagged_after_paraphrase_fix():
    """Regression guard: Case 10's original decision-30 transcript was
    already correctly flagged (via the pre-existing 'other factors at
    play'/'other causes may exist' phrases) -- must remain flagged, not
    lose the detection or double-count differently, after adding the two
    new phrases."""
    entry = _ENTRY_BY_ID["case_10_referential_integrity"]
    prose = (
        "This data-quality issue is a complete and confirmed finding, and we can say "
        "that it accounts for a portion of the gap. However, we must also consider the "
        "unexplained residual, which is +$300.00. This means that the reconciled "
        "causes we found do not fully explain the discrepancy, and there may be other "
        "factors at play. It's worth noting that the unexplained residual does not "
        "include or account for the data-quality issue we identified. By the "
        "project's own accounting convention, a data-quality issue's dollar impact is "
        "never subtracted out of the residual. Therefore, the nonzero residual is not "
        "evidence that the data-quality cause only partially explains the gap, and it "
        "is not grounds to say other causes may exist or that further investigation "
        "is needed."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns


def test_case_11_still_flagged_after_paraphrase_fix():
    """Regression guard: Case 11's original decision-30 transcript was
    already correctly flagged (via the pre-existing 'other causes may
    exist'/'further investigation is needed' phrases), and it happens to
    ALSO contain 'the entire gap remains unexplained' verbatim -- the exact
    new phrase this fix adds. Confirms the addition doesn't change Case
    11's already-correct verdict, only gives it a second, independent
    matching reason."""
    entry = _ENTRY_BY_ID["case_11_referential_integrity_source_b"]
    prose = (
        "The data-quality issue is a complete and confirmed finding in its own right, "
        "and we should not assume that other causes may exist or that further "
        "investigation is needed. In summary, while we have identified a data-quality "
        "issue that contributes to the discrepancy, the entire gap remains "
        "unexplained, and the residual figure of -$300.00 indicates that further "
        "investigation is needed to fully understand the causes of the discrepancy."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns


# --- Build 3, Day 5, Part 4: decision 36's findings #2 and #3, both real, ---
# --- already-quoted transcripts (PR #128's Case 2, PR #136's Case 3), not ---
# --- synthetic strings. ---


def test_detects_data_quality_overclaim_on_case_3_real_transcript():
    """The actual finding #3 target (decision 36, PR #136's fresh Case 3
    transcript): Case 3 has no _DATA_QUALITY_DISPATCH entry at all (never
    checked), yet the response claims confirmed freshness. Must now be
    caught by the new data_quality_overclaim pattern."""
    entry = _ENTRY_BY_ID["case_03_hybrid_fallback"]
    prose = (
        "The investigation found two reconciled causes that contribute to the discrepancy "
        "between the two sources. The first cause is related to the date field, where "
        "source A implies 'order_date' and source B implies 'created_at'. This discrepancy "
        "has a dollar impact of -$400.00. The second cause is related to the excluded "
        "statuses, where source A implies 'cancelled' and source B implies 'cancelled, "
        "refunded'. This discrepancy has a dollar impact of $500.00.\n\n"
        "Additionally, the investigation found definitional differences between the two "
        "sources. The date field and excluded statuses have different values in source A "
        "and source B, which could be due to a mismatch in the metric definitions. These "
        "differences were identified as 'medium' confidence, indicating that they are "
        "plausible but not certain.\n\n"
        "The investigation did not find any self-consistency issues or SQL structural "
        "differences between the two sources. The data-quality issues were also found to "
        "be zero, indicating that the data is fresh and complete.\n\n"
        "The unexplained residual is $0.00, indicating that the gap is fully explained by "
        "the causes found. Therefore, the investigation concludes that the discrepancy "
        "between the two sources is fully explained by the reconciled causes and "
        "definitional differences identified."
    )
    score = score_scenario(entry, prose)
    assert "data_quality_overclaim" in score.unsupported_claim_patterns
    assert "data_quality_overclaim" in score.checks_run


def test_does_not_flag_data_quality_overclaim_when_scenario_was_genuinely_dispatched():
    """Over-fire guard: Case 8 (case_08_stale_extract) IS in
    _DATA_QUALITY_DISPATCH -- its own real data_quality_issues entry means
    the data_quality_overclaim check never even runs for it (see
    checks_run), the same way it must never flag a genuinely
    checked-and-clean dispatched scenario if one existed."""
    entry = _ENTRY_BY_ID["case_08_stale_extract"]
    prose = (
        "This data-quality issue accounts for the entire gap, and we have a confirmed "
        "cause for the discrepancy. The unexplained residual is -$150.00, which is a "
        "separate fact from the reconciled cause. It does not indicate the existence of "
        "other causes or the need for further investigation. The residual is simply a "
        "remaining amount that is not explained by the data-quality issue."
    )
    score = score_scenario(entry, prose)
    assert "data_quality_overclaim" not in score.unsupported_claim_patterns
    assert "data_quality_overclaim" not in score.checks_run


def test_data_quality_overclaim_check_runs_but_stays_silent_on_honest_undispatched_prose():
    """The correct, fixed behavior (Build 3, Day 5, Part 4, Task 1's own
    live-verified prompt fix): an undispatched scenario whose response
    honestly states uncertainty, rather than claiming confirmed
    cleanliness, must not be flagged -- the check runs (dispatch status
    makes it applicable) but finds nothing to flag."""
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = (
        "A join-type mismatch has a dollar impact of $300.00. No data-quality issues were "
        "checked for this investigation, so it's unclear whether the data is fresh, "
        "complete, or clean. The unexplained residual is $0.00."
    )
    score = score_scenario(entry, prose)
    assert "data_quality_overclaim" not in score.unsupported_claim_patterns
    assert "data_quality_overclaim" in score.checks_run


def test_residual_self_contradiction_widened_gate_detects_case_2_real_transcript():
    """The actual finding #2 target (decision 36, PR #128's fresh Case 2
    transcript, the false-escalation probe): Case 2 has
    data_quality_issues=[] and unexplained_residual=0.0 -- before this
    widening, residual_self_contradiction was never even attempted here
    (gated on data_quality_issues alone). The hedge ('additional factors
    at play') is the same class of unsupported claim this check already
    catches elsewhere, now reachable on a definitional-only, zero-residual
    scenario too. This transcript's own real negation ('did not identify
    any other causes...') sits in a DIFFERENT clause, joined by ', and' --
    proving the clause-boundary widening (not just the gate widening) is
    what makes this detectable; a plain sentence-boundary guard falsely
    treated the earlier, unrelated 'not' as covering this violation."""
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    prose = (
        "The investigation into the discrepancy between the two sources has identified "
        "several reconciled causes and definitional differences that contribute to the "
        "gap. The unexplained residual is $0.00, which means that the gap is fully "
        "explained by the causes found. However, it is essential to acknowledge that the "
        "investigation did not identify any other causes beyond those mentioned, and it "
        "is possible that there may be additional factors at play that were not captured "
        "by this analysis."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" in score.unsupported_claim_patterns
    assert "residual_self_contradiction" in score.checks_run


def test_residual_self_contradiction_widened_gate_stays_silent_on_clean_zero_residual_prose():
    """Over-fire guard: a genuinely clean, zero-residual, fully-explained
    response (no data-quality issues either) must not be flagged just
    because the widened gate now runs this check for it."""
    entry = _ENTRY_BY_ID["case_01_join_type"]
    prose = (
        "A join-type mismatch has a dollar impact of $300.00. No other differences were "
        "found, and the unexplained residual is $0.00 -- the gap is fully explained by "
        "this cause."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns
    assert "residual_self_contradiction" in score.checks_run


def test_clause_boundary_widening_does_not_break_case_10_compound_negation_fix():
    """Regression guard: Build 3, Day 5, Part 4 widened the negation-guard's
    boundary regex (comma + coordinating conjunction, not just sentence-
    ending punctuation) to fix Case 2's false negative above. Case 10's
    own compound-negation fix (Build 3, Day 4, Part 8, Task 1, decision 32)
    must still hold -- its 'not' sits before its own ', but' boundary
    marker, so the window from that boundary to the violating phrase still
    contains the real negation."""
    entry = _ENTRY_BY_ID["case_10_referential_integrity"]
    prose = (
        "This data-quality issue is a confirmed cause of the discrepancy, and it fully "
        "explains the gap. The unexplained residual is +$300.00, but this is not evidence "
        "that additional causes exist or that further investigation is needed. The "
        "data-quality issue is a complete and confirmed finding in its own right."
    )
    score = score_scenario(entry, prose)
    assert "residual_self_contradiction" not in score.unsupported_claim_patterns


def test_detects_fact_doubling_on_realistic_prose():
    """Modeled directly on Case 4's own fresh live-verification quote
    (decision 32): one real $200 cause narrated as two, summed to a
    fabricated $400 total against a real known_gap of $200."""
    entry = _ENTRY_BY_ID["case_04_governance_drift"]
    prose = (
        "The first cause is a self-consistency issue in source A's own SQL, where the "
        "implemented value for 'excluded_statuses' contradicts its declared definition. "
        "This discrepancy results in a dollar impact of +200.00. Additionally, there is "
        "a reconciled cause where source A's SQL implements excluded_statuses='cancelled', "
        "which contradicts its own declared 'cancelled, refunded'. This also results in a "
        "dollar impact of +200.00. These causes contribute a total dollar impact of "
        "+400.00, which fully explains the gap."
    )
    score = score_scenario(entry, prose)
    assert "fact_doubling" in score.unsupported_claim_patterns


def test_does_not_flag_fact_doubling_when_stated_total_matches_known_gap():
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    prose = (
        "Two causes explain the gap: excluded_statuses contributes +$120.00 and "
        "aggregation contributes -$100.00. Together, these causes contribute a total "
        "dollar impact of +20.00, which fully explains the gap."
    )
    score = score_scenario(entry, prose)
    assert "fact_doubling" not in score.unsupported_claim_patterns


def test_does_not_flag_fact_doubling_when_no_total_is_claimed():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.")
    assert "fact_doubling" not in score.unsupported_claim_patterns
    assert "fact_doubling" in score.checks_run


# --- Decision 43's own sixth pattern (existence_overclaim): both real ---
# --- quotes are from the confidence-step's `reason` field (decision 42's ---
# --- Part 3 addendum, the omission variant), not explainer prose -- the ---
# --- first committed proof that score_scenario scores either input shape. ---


def test_detects_existence_overclaim_on_case_16_real_transcript():
    """The actual case_16 target (decision 42/43): a real, single reconciled
    cause (excluded_statuses, dollar_impact=300.0, matching known_gap
    exactly) narrated as absent. Quoted verbatim from the confidence step's
    own real `reason` field, not the explainer -- this scenario's
    reconciliation list is what makes the claim false."""
    entry = _ENTRY_BY_ID["case_16_excluded_statuses_declared"]
    prose = (
        "The investigation did not identify any reconciled causes with a significant "
        "dollar impact, and the unexplained residual is zero, which may indicate the "
        "presence of other uninvestigated causes."
    )
    score = score_scenario(entry, prose)
    assert "existence_overclaim" in score.unsupported_claim_patterns
    assert "existence_overclaim" in score.checks_run


def test_detects_existence_overclaim_on_ambiguous_revenue_recognition_real_transcript():
    """The second real target: two real, declared, high-confidence
    DefinitionDifference entries (date_field, excluded_statuses) narrated
    as absent, in the same sentence that correctly describes
    self_consistency_issues as empty (which IS genuinely true for this
    scenario) -- proving the detector attributes the claim to the right
    category rather than flagging on a bare 'did not identify any' match."""
    entry = _ENTRY_BY_ID["ambiguous_revenue_recognition"]
    prose = (
        "The investigation found multiple reconciled causes with a small unexplained "
        "residual, but it did not identify any definitional differences or "
        "self-consistency issues, which suggests that the discrepancy is largely "
        "accounted for, but further investigation may still be necessary to confirm "
        "its completeness."
    )
    score = score_scenario(entry, prose)
    assert "existence_overclaim" in score.unsupported_claim_patterns
    assert "existence_overclaim" in score.checks_run


def test_does_not_flag_existence_overclaim_when_the_named_category_is_genuinely_empty():
    """Over-fire guard, same standard as decision 37's own false-positive
    sweep before data_quality_overclaim shipped: case_16's own real
    self_consistency_issues IS genuinely empty (confirmed by direct
    execution). A correct 'did not identify any self-consistency issues'
    claim, appearing in the SAME response as a correct, affirmative claim
    about the (real, populated) reconciled cause, must not misfire --
    proving category attribution, not mere co-occurrence of the phrase
    'reconciled cause' anywhere in the response."""
    entry = _ENTRY_BY_ID["case_16_excluded_statuses_declared"]
    prose = (
        "The investigation did not identify any self-consistency issues, but did "
        "identify a reconciled cause that explains the entire dollar gap."
    )
    score = score_scenario(entry, prose)
    assert "existence_overclaim" not in score.unsupported_claim_patterns
    assert "existence_overclaim" in score.checks_run


def test_does_not_flag_existence_overclaim_when_no_category_is_referenced():
    entry = _ENTRY_BY_ID["case_01_join_type"]
    score = score_scenario(entry, "A join-type mismatch has a dollar impact of $300.00.")
    assert "existence_overclaim" not in score.unsupported_claim_patterns


def test_existence_overclaim_check_does_not_run_when_all_three_categories_are_genuinely_empty():
    """Case 5: a real, nonzero known_gap with no findable cause at all --
    reconciliation, definition_differences, and self_consistency_issues are
    all genuinely empty. The check must not even attempt to run here (see
    checks_run), matching hedge_then_retract's own any(...)-gated
    convention just above it in score_scenario."""
    entry = _ENTRY_BY_ID["case_05_unexplained_residual"]
    score = score_scenario(
        entry, "No cause was identified for this investigation, and the entire gap remains unexplained."
    )
    assert "existence_overclaim" not in score.checks_run


def test_case_2_real_transcript_did_not_identify_any_other_causes_does_not_misfire():
    """Cross-check against a real, already-committed transcript that also
    contains the literal lead-in 'did not identify any' but pairs it with
    'other causes' -- not one of the three named category phrases --
    confirming this new pattern stays silent on decision 36 finding #2's
    own real quote rather than accidentally re-flagging it under a new
    name."""
    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    prose = (
        "The unexplained residual is $0.00, which means that the gap is fully explained by the causes "
        "found. However, it's essential to acknowledge that the investigation did not identify any other "
        "causes beyond those mentioned, and it's possible that there may be additional factors at play "
        "that were not captured by this analysis."
    )
    score = score_scenario(entry, prose)
    assert "existence_overclaim" not in score.unsupported_claim_patterns


# --- Build 3, Day 4, Part 8, Task 2: mocked test for the LLM-graded ---
# --- checker's JSON-parsing logic -- the LLM call itself is mocked here ---
# --- (Level 1 of the workflow guide's live-API-test policy), matching ---
# --- test_explainer.py's own convention. Real API verification is Task 3, ---
# --- reported by hand in the PR, not automated here. ---


def test_llm_graded_parses_clean_json_response(monkeypatch):
    def fake_generate_structured(prompt, response_schema=None):
        assert response_schema is None  # prompt-instructed JSON, not response_format
        return '{"sign_dropping": false, "hedge_then_retract": false, "residual_self_contradiction": true, "fact_doubling": false}'

    monkeypatch.setattr(eval_scoring, "generate_structured", fake_generate_structured)

    entry = _ENTRY_BY_ID["case_20_stale_extract_join_collision"]
    result = score_scenario_llm_graded(entry, "some prose")

    assert result == LLMClaimGrading(
        sign_dropping=False, hedge_then_retract=False, residual_self_contradiction=True, fact_doubling=False
    )


def test_llm_graded_strips_markdown_json_fence(monkeypatch):
    """Real models sometimes wrap JSON in ```json ... ``` despite explicit
    instructions not to -- the fence-stripping regex must handle this."""

    def fake_generate_structured(prompt, response_schema=None):
        return '```json\n{"sign_dropping": true, "hedge_then_retract": false, "residual_self_contradiction": false, "fact_doubling": false}\n```'

    monkeypatch.setattr(eval_scoring, "generate_structured", fake_generate_structured)

    entry = _ENTRY_BY_ID["case_02_multi_cause"]
    result = score_scenario_llm_graded(entry, "some prose")

    assert result.sign_dropping is True
