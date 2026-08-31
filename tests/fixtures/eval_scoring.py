"""Build 3, Day 4, Part 6: first-pass eval-scoring code. Root-cause accuracy
(deterministic presence/value checks against BenchmarkEntry's ground truth)
and unsupported-claim-rate detection for the three named patterns already
recorded in docs/decisions.md (decision 14's sign-dropping and
hedge-then-retract; decision 30's residual self-contradiction) -- pattern-
matching against known, named shapes, not a general hallucination detector.

Escalation recall and false-escalation rate (decision 6) are explicitly NOT
scored here. ScenarioScore.escalation_status is always "not_gradable" --
a real, visible marker, not a silent omission or a misleading default.

Placement: tests/fixtures/, not src/ -- mirrors benchmark_pipeline.py's own
placement reasoning exactly (BenchmarkEntry, this module's core input, is
explicitly "not consumed by any src/ code"; this module is itself
benchmark-scoring infrastructure, paired with tests/test_eval_scoring.py
the same way benchmark_pipeline.py pairs with test_benchmark_pipeline.py).

Design: root-cause and unsupported-claim scoring both need the real,
deterministically-computed InvestigationEvidence/PartialInvestigationEvidence
for a scenario -- not just BenchmarkEntry's own notes -- to know the actual
correct dollar figures, cause descriptions, and which categories are
genuinely empty/populated. This is recomputed internally by score_scenario
via assemble_investigation_evidence_for_benchmark (deterministic, no LLM
call, cheap), the SAME function run_benchmark uses to generate the prose
in the first place -- so score_scenario never has to trust or re-derive
ground truth from BenchmarkEntry.notes' prose alone.

Task 3's own live verification (all 24 scenarios, real API calls) found
and fixed four real scorer bugs before its numbers were trustworthy enough
to report -- documented here, not silently smoothed over:
  1. An empty `reconciliation` list was treated as automatically incorrect,
     but Case 5's own designed shape is a real, nonzero known_gap with NO
     findable cause -- an empty list there is the correct ground truth.
  2. The dollar-figure regex required a literal `$`, but this task's own
     locked spec names '$300.00 vs 300.0' as acceptable formatting
     tolerance -- bare decimal numbers are now matched too.
  3. The same regex then over-corrected by requiring a decimal point even
     when `$` WAS present, so bare '$120' (no '.00', which the model
     writes often despite the prompt's own :+.2f formatting) went
     unmatched -- decimals are now optional whenever `$` is present.
  4. _keyword_present used plain substring checks, which matched 'sum'
     (an aggregation synonym) inside the unrelated word 'summary' ('In
     summary, ...'); switched to word-boundary regex matching, with `_`
     normalized to a space first since a literal snake_case token like
     'excluded_statuses' reads as one unbroken word to \b otherwise."""

import re
from typing import Literal

from pydantic import BaseModel

from src.explainer import explain_investigation
from src.schema import DataQualityIssue, DefinitionDifference, InvestigationEvidence, ReconciliationLineItem
from tests.fixtures.benchmark_entries import BENCHMARK_ENTRIES, BenchmarkEntry
from tests.fixtures.benchmark_pipeline import PartialInvestigationEvidence, assemble_investigation_evidence_for_benchmark

_Evidence = InvestigationEvidence | PartialInvestigationEvidence

_DOLLAR_RE = re.compile(
    r"(?P<neg>-|\()?\s*(?:\$\s*(?P<amt_dollar>\d[\d,]*(?:\.\d+)?)|(?P<amt_decimal>\d[\d,]*\.\d+))\)?"
)
"""Matches a dollar figure, via two alternatives (exactly one of amt_dollar/
amt_decimal is set on any given match):
  1. `$`-prefixed: decimals optional ('$120', '$300.00', '$1,200') -- live
     verification (Build 3, Day 4, Part 6, Task 3) found the model
     frequently drops the trailing '.00' even with the `$` present (e.g.
     'a positive dollar impact of $120'), despite _format_evidence_prompt's
     own `:+.2f` formatting always including two decimal places.
  2. No `$`, decimals REQUIRED ('300.0', '-250.00') -- this task's own
     locked spec names '$300.00 vs 300.0' as an acceptable formatting-
     tolerance pair, so `$` cannot be mandatory; the decimal point is what
     keeps a bare row count ('3 rows', '1 row(s)') from being mistaken for
     a dollar figure once `$` is optional.
Use _dollar_match_amount(match) to read whichever group actually matched."""


def _dollar_match_amount(match: re.Match) -> float:
    raw = match.group("amt_dollar") if match.group("amt_dollar") is not None else match.group("amt_decimal")
    return float(raw.replace(",", ""))

_NEGATIVE_QUALIFIER_RE = re.compile(
    r"\b(reduc\w*|offset\w*|negative|decreas\w*|suppress\w*|lower\w*)\b", re.IGNORECASE
)
"""Words decision 14's own sign-dropping defect entry names as an acceptable
substitute for a literal minus sign -- a magnitude stated near one of these
is NOT sign-dropping, even without a '-' or parens."""

_RESIDUAL_VIOLATING_PHRASES = [
    "portion of the gap",
    "other factors at play",
    "other causes may exist",
    "further investigation is needed",
    "further investigation may be necessary",
    "not yet accounted for",
]
"""Decision 30's own quoted contradicting phrases (Cases 10, 11, 20's live
verification transcript), used verbatim, not re-derived."""

_RESIDUAL_CORRECTING_PHRASES = [
    "not evidence that",
    "does not include or account for",
    "not grounds to say other causes",
    "separate fact",
]
"""Phrases matching the residual-framing instruction's own wording
(src/explainer.py) -- decision 30's finding is specifically the
CO-OCCURRENCE of one of these with one of the violating phrases above in
the same response, not either alone."""

_HEDGE_THEN_RETRACT_RE = re.compile(
    r"(identified|another)[\s\S]{0,150}?cause[\s\S]{0,200}?(does not exist|doesn't exist)", re.IGNORECASE
)
"""Decision 14's defect-2 pattern, matched directly against its own quoted
example: 'We have also identified a reconciled cause that is a result of
definitional differences ... However, this cause does not exist'. Uses
[\\s\\S] rather than [^.] deliberately -- decision 14's own real quote has
a sentence boundary (a literal period) between "cause" and "does not
exist", so excluding periods from the window would fail to match the exact
defect this pattern is named for. Bounded by character count, not sentence
count, so the window doesn't grow unbounded across an entire long
response."""


class ScenarioScore(BaseModel):
    """One scenario's scoring result. root_cause_correct is bool for every
    entry currently in BENCHMARK_ENTRIES (every one has a well-defined
    ground_truth_check_field) -- the Literal["not_gradable"] option exists
    for a future entry whose ground truth genuinely can't be checked this
    way, not exercised by any of the current 24."""

    scenario_id: str
    root_cause_correct: bool | Literal["not_gradable"]
    unsupported_claim_patterns: list[Literal["sign_dropping", "hedge_then_retract", "residual_self_contradiction"]]
    checks_run: list[str]
    escalation_status: Literal["not_gradable"] = "not_gradable"
    """Decision 6's escalation recall / false-escalation rate are explicitly
    NOT scored in this pass (Build 3, Day 4, Part 6's own locked scope).
    Always "not_gradable" -- never silently omitted, never a misleading
    True/False default."""


def _extract_dollar_magnitudes(text: str) -> set[float]:
    """Every distinct dollar magnitude (absolute value, sign discarded)
    mentioned in `text`, rounded to 2 decimals."""
    magnitudes = set()
    for match in _DOLLAR_RE.finditer(text):
        magnitudes.add(round(_dollar_match_amount(match), 2))
    return magnitudes


def _dollar_figure_present(text: str, expected_amount: float) -> bool:
    """Root-cause dollar-figure check: magnitude match only (sign is
    checked separately, by the sign-dropping unsupported-claim pattern
    below) -- deliberately not double-penalizing the same defect in two
    different metrics. 'Reasonable tolerance for formatting' (this task's
    own locked wording) means $300.00 vs 300.0 vs $300 all match; it does
    not mean a materially different number is accepted."""
    return round(abs(expected_amount), 2) in _extract_dollar_magnitudes(text)


_CATEGORY_PROSE_SYNONYMS: dict[str, list[str]] = {
    "join_type": ["join"],
    "date_field": ["date"],
    "excluded_statuses": ["status", "statuses", "exclude", "excluded", "excludes"],
    "aggregation": ["aggregation", "aggregate", "aggregated", "aggregating", "count", "distinct", "sum"],
    "filter": ["filter", "filters", "filtered", "filtering"],
    "distinct": ["distinct"],
    "grouping": ["group", "groups", "grouped", "grouping"],
    "stale_extract": ["stale", "snapshot", "snapshots"],
    "missing_partition": ["partition", "partitions", "snapshot", "snapshots"],
    "late_arriving_data": ["late", "arrive", "arrived", "arriving", "arrival"],
    "referential_integrity": ["referential", "orphan", "foreign", "does not match", "doesn't match"],
}
"""Natural-language substrings a model realistically uses when paraphrasing
a field/category name -- checking for the literal snake_case token itself
(e.g. 'join_type') would false-negative on almost all real prose, which
says 'join' or 'JOIN', never the internal identifier. 'snapshot' covers
both stale_extract and missing_partition deliberately -- they are the
literal same detection function under two labels (_DATA_QUALITY_DISPATCH's
own docstring, src/reconciliation_assembly.py), and live verification
(Build 3, Day 4, Part 6, Task 3) found the model consistently paraphrases
DataQualityIssue.description's own row-count language ('the as-delivered
snapshot has N rows... the complete counterfactual snapshot has M') without
ever using the literal words 'stale' or 'partition'. Similarly
'does not match' covers referential_integrity's own description text
('has N row(s) whose X value does not match any X in Y'), which the model
was found to paraphrase closely without using 'referential'/'orphan'.
Entries list full word forms, not truncated stems -- live verification
found a real false positive with a stem-based 'sum' matching the unrelated
word 'summary' ('In summary, ...'); every entry here is matched with a
strict word boundary (see _keyword_present), which only works correctly
against complete words. Falls back to the literal name (space- and
underscore-tolerant) for any category/field not listed here, so a new
field/category added later degrades to a stricter but still-functional
check rather than crashing."""


def _keyword_present(text: str, keyword: str) -> bool:
    # '_' is a word character to regex \b, so a literal snake_case token like
    # 'excluded_statuses' (which the model sometimes writes verbatim, e.g.
    # live verification's Case 16/17 prose) reads as ONE unbroken word --
    # 'status' could never \b-match inside it without this normalization.
    lowered = text.lower().replace("_", " ")
    synonyms = _CATEGORY_PROSE_SYNONYMS.get(keyword, [keyword.replace("_", " "), keyword])
    return any(re.search(r"\b" + re.escape(syn.lower()) + r"\b", lowered) for syn in synonyms)


def _cause_keyword(cause: str, sql_differences: list) -> str:
    """Resolve a ReconciliationLineItem.cause string back to the
    field/category keyword _keyword_present understands. Three shapes
    exist in this project's committed cause text (src/reconciliation_assembly.py's
    _describe and _self_consistency_line_item):
      1. A SQLStructuralDifference's own description, verbatim -- matched
         back to its category via the sql_differences list.
      2. A DefinitionDifference-composed cause ("field: source_a declares
         ..."), where the keyword is the text before the first ':'.
      3. A self-consistency cause ("source_a's own SQL implements
         field=...'"), where the keyword follows "implements "."""
    for sd in sql_differences:
        if sd.description == cause:
            return sd.category
    if ":" in cause:
        return cause.split(":")[0].strip()
    match = re.search(r"implements (\w+)=", cause)
    if match:
        return match.group(1)
    return cause


def _score_root_cause(entry: BenchmarkEntry, prose: str, evidence: _Evidence) -> bool:
    """Deterministic presence/value check against BenchmarkEntry's own
    ground_truth_check_field -- never a second LLM call. Every branch below
    re-derives its own ground truth directly from the real, deterministically-
    computed `evidence` object (assemble_investigation_evidence_for_benchmark's
    output), never from BenchmarkEntry.notes' prose, which is documentation
    for a human reader, not a machine-checkable ground-truth source."""
    field = entry.ground_truth_check_field

    if field == "none":
        # The correct answer is that nothing was found. A nonzero dollar
        # figure anywhere in the prose means a specific cause was asserted
        # that should not exist.
        return not any(m != 0.0 for m in _extract_dollar_magnitudes(prose))

    if field in ("reconciliation", "reconciliation_and_data_quality"):
        items: list[ReconciliationLineItem] = evidence.reconciliation
        if not items:
            # An empty reconciliation list CAN be the correct ground truth
            # (Case 5's own documented shape: a real, nonzero known_gap with
            # no findable cause) -- not automatically wrong, unlike "none"
            # (Case 6, where the correct residual is exactly zero). Correct
            # behavior here is that no specific mechanism/category is
            # asserted as an explanatory cause; the (real, expected) residual
            # dollar figure itself is not penalized, since checking dollar
            # figures without cause language would misclassify Case 5's own
            # correct "no cause found" prose (Build 3, Day 4, Part 6, Task 3
            # live verification: found this exact false negative and fixed
            # it here, not worked around by discarding the finding).
            return not any(_keyword_present(prose, kw) for kw in _CATEGORY_PROSE_SYNONYMS)
        for item in items:
            keyword = _cause_keyword(item.cause, evidence.sql_differences)
            if not _keyword_present(prose, keyword):
                return False
            if not _dollar_figure_present(prose, item.dollar_impact):
                return False
        if field == "reconciliation":
            return True
        # fall through to also check data_quality_issues below

    if field in ("data_quality_issues", "reconciliation_and_data_quality"):
        issues: list[DataQualityIssue] = evidence.data_quality_issues
        if not issues:
            return False
        for issue in issues:
            if not _keyword_present(prose, issue.category):
                return False
            if not _dollar_figure_present(prose, issue.dollar_impact):
                return False
        return True

    if field == "definition_differences":
        diffs: list[DefinitionDifference] = evidence.definition_differences
        if not diffs:
            return False
        return all(_keyword_present(prose, d.field) for d in diffs)

    raise ValueError(f"Unhandled ground_truth_check_field: {field!r}")


def _detect_sign_dropping(prose: str, evidence: _Evidence) -> bool:
    """Decision 14's defect 1: a negative dollar_impact stated in prose as
    a bare positive magnitude, with no negative sign, parens, or qualifier
    word nearby. Checked against every negative-signed figure across
    reconciliation, self_consistency_issues, and data_quality_issues --
    every field this project signs the same way (src/schema.py's shared
    convention)."""
    negative_amounts = [
        abs(item.dollar_impact)
        for item in [*evidence.reconciliation, *evidence.self_consistency_issues, *evidence.data_quality_issues]
        if item.dollar_impact < 0
    ]
    for amount in negative_amounts:
        for match in _DOLLAR_RE.finditer(prose):
            if round(_dollar_match_amount(match), 2) != round(amount, 2):
                continue
            if match.group("neg"):
                continue  # a literal '-' or '(' was present -- not dropped
            window_start = max(0, match.start() - 60)
            if _NEGATIVE_QUALIFIER_RE.search(prose[window_start : match.start()]):
                continue  # a qualifier word ("reduces", "offsets", ...) covers it
            return True
    return False


def _detect_hedge_then_retract(prose: str) -> bool:
    """Decision 14's defect 2: language implying a cause exists in an
    empty category before negating it. Takes no evidence argument -- the
    regex fires purely on phrasing; the caller (score_scenario) only
    invokes this check when the evidence shape actually has at least one
    empty and one populated category, the exact shape Case 4's original
    defect was found on."""
    return bool(_HEDGE_THEN_RETRACT_RE.search(prose))


_NEGATION_RE = re.compile(r"\b(no|not|n't|never)\b", re.IGNORECASE)
"""Guards a violating-phrase match against its own negation -- live
verification (Build 3, Day 4, Part 6, Task 3) found a real false positive:
'no further investigation is needed' contains the violating phrase 'further
investigation is needed' as a literal substring, but the leading 'no'
reverses its meaning entirely into a CORRECT statement, not a contradiction."""


def _violating_phrase_present(prose: str, phrase: str) -> bool:
    for match in re.finditer(re.escape(phrase), prose, re.IGNORECASE):
        window_start = max(0, match.start() - 20)
        if _NEGATION_RE.search(prose[window_start : match.start()]):
            continue
        return True
    return False


def _detect_residual_self_contradiction(prose: str) -> bool:
    """Decision 30's pattern: the residual-framing instruction's own
    constraint stated correctly in one part of the response, violated in
    another. Detected as co-occurrence, not either phrase alone -- a
    response that only ever states the constraint (Case 8's clean pass) or
    only ever violates it should not be flagged the same as one that does
    both (Cases 10/11/20's actual defect)."""
    has_correcting = any(phrase in prose for phrase in _RESIDUAL_CORRECTING_PHRASES)
    has_violating = any(_violating_phrase_present(prose, phrase) for phrase in _RESIDUAL_VIOLATING_PHRASES)
    return has_correcting and has_violating


def score_scenario(entry: BenchmarkEntry, prose: str) -> ScenarioScore:
    """Score one already-generated explainer response against `entry`'s
    ground truth. Deliberately does NOT call the LLM -- `prose` must
    already exist, so this function can be tested against fixed strings
    with zero API cost (Task 2's own requirement)."""
    evidence = assemble_investigation_evidence_for_benchmark(entry)

    checks_run = ["root_cause"]
    root_cause_correct = _score_root_cause(entry, prose, evidence)

    unsupported_claim_patterns: list[Literal["sign_dropping", "hedge_then_retract", "residual_self_contradiction"]] = []

    has_any_negative = any(
        item.dollar_impact < 0
        for item in [*evidence.reconciliation, *evidence.self_consistency_issues, *evidence.data_quality_issues]
    )
    if has_any_negative:
        checks_run.append("sign_dropping")
        if _detect_sign_dropping(prose, evidence):
            unsupported_claim_patterns.append("sign_dropping")

    categories_populated = [
        bool(evidence.reconciliation),
        bool(evidence.definition_differences),
        bool(evidence.self_consistency_issues),
        bool(evidence.sql_differences),
        bool(evidence.data_quality_issues),
    ]
    if any(categories_populated) and not all(categories_populated):
        checks_run.append("hedge_then_retract")
        if _detect_hedge_then_retract(prose):
            unsupported_claim_patterns.append("hedge_then_retract")

    if evidence.data_quality_issues:
        checks_run.append("residual_self_contradiction")
        if _detect_residual_self_contradiction(prose):
            unsupported_claim_patterns.append("residual_self_contradiction")

    return ScenarioScore(
        scenario_id=entry.scenario.scenario_id,
        root_cause_correct=root_cause_correct,
        unsupported_claim_patterns=unsupported_claim_patterns,
        checks_run=checks_run,
    )


def run_benchmark() -> list[ScenarioScore]:
    """The one function in this module that costs real API calls: for
    every entry in BENCHMARK_ENTRIES, generate real explainer prose via
    assemble_investigation_evidence_for_benchmark + explain_investigation,
    then score it. Not called by any committed test -- Task 3's own live
    verification run is a manual, reported-by-hand invocation, matching
    this project's standing practice for every prior live-API-call
    verification (Build 1 Week 2 Day 1 onward)."""
    scores = []
    for entry in BENCHMARK_ENTRIES:
        evidence = assemble_investigation_evidence_for_benchmark(entry)
        prose = explain_investigation(evidence)
        scores.append(score_scenario(entry, prose))
    return scores
