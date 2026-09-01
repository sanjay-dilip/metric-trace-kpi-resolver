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
     'excluded_statuses' reads as one unbroken word to \b otherwise.

Build 3, Day 4, Part 7 (decision 32): a fresh, independent 5-scenario
re-test (not reused prose) found the original 0/24 unsupported-claim
result did not hold up under a second sample -- 1 brand-new, previously
undetected defect shape (Case 4), 1 detector false positive (Case 10),
1 detector false negative (Case 20). Reported, not fixed, in that entry.

Build 3, Day 4, Part 8, Task 1: fixed exactly those three diagnosed gaps,
no broader rewrite:
  1. Case 10's false positive: _violating_phrase_present's negation guard
     used a fixed 20-character lookback window; the real sentence
     ('this is not evidence that additional causes exist or that further
     investigation is needed') has the negating "not" ~50 characters
     before the violating phrase. Replaced the fixed window with a
     sentence-scoped one (scans back to the nearest preceding
     '.'/'!'/'?', not a guessed character count).
  2. Case 20's false negative: _detect_residual_self_contradiction
     required BOTH a correcting phrase and a violating phrase to
     co-occur; Case 20's fresh response only ever violated the
     constraint, never stated it correctly, so the co-occurrence
     requirement never fired. A bare (negation-guarded) violating
     phrase is now sufficient on its own.
  3. Case 4's undetected defect: added a fourth named pattern,
     "fact_doubling" (_detect_fact_doubling, _TOTAL_CLAIM_RE) -- an
     explicit claimed "total X impact" that mismatches the scenario's
     real known_gap, matched directly against Case 4's own quoted
     output ('contribute a total dollar impact of +400.00' against a
     real known_gap of 200.0).

Build 3, Day 4, Part 8, Task 2 also built score_scenario_llm_graded (below)
as a real, head-to-head alternative to the hand-written detector above.
Decision 33 (Build 3, Day 4, Part 9): the hand-written detector, with the
three fixes above, ships as the sole, authoritative unsupported-claim-rate
check -- the LLM grader is dropped from the scoring pipeline (it was never
wired into run_benchmark to begin with; see score_scenario_llm_graded's own
docstring for the reasoning) and kept only as a documented, working,
tested experiment, not deleted."""

import re
from typing import Literal

from pydantic import BaseModel

from src.explainer import explain_investigation
from src.llm_client import generate_structured
from src.reconciliation_assembly import is_data_quality_dispatched
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
    "not accounted for",
    "gap remains unexplained",
    "additional factors",
]
"""Decision 30's own quoted contradicting phrases (Cases 10, 11, 20's live
verification transcript), used verbatim, not re-derived.

Build 3, Day 5, Part 1 (decision 33's own named follow-up): the original
six phrases above are a fixed list, and a fixed list cannot catch every
paraphrase -- PR #136's fresh 6-scenario comparison run (Build 3, Day 4,
Part 8, Task 3) found the LLM grader correctly flagged a Case 20 response
this list missed entirely: "there may be other underlying causes that are
not accounted for in this investigation" (no "yet" -- doesn't match "not
yet accounted for") and "the entire gap remains unexplained" (not on the
list at all). Two new phrases added, each grounded directly in a real,
already-quoted transcript, not invented: "not accounted for" (Case 20's
own fresh quote above) and "gap remains unexplained" (also present
verbatim in Case 11's original decision-30 quote -- "the entire gap
remains unexplained" -- which was already correctly flagged via a
different phrase, so this addition doesn't change its verdict, only adds
a second, independent match). Deliberately NOT a bare "remains
unexplained": Case 9's own original decision-30 transcript says "there is
still a gap that remains unexplained" -- worded about the RESIDUAL itself
remaining unexplained (correct, expected framing) rather than claiming
other CAUSES remain unaccounted for -- and Case 9 is a documented true
negative (decision 30: "the model does not claim other causes exist").
"gap remains unexplained" requires exact adjacency, so it does not match
Case 9's "gap that remains unexplained" (a "that" sits between the two
words), keeping Case 9 negative as it must remain.

Build 3, Day 5, Part 4 (decision 36's finding #2): "additional factors"
added, grounded in PR #128's real fresh Case 2 transcript -- "it's
possible that there may be additional factors at play that were not
captured by this analysis" -- said about a scenario with
data_quality_issues=[] and unexplained_residual=0.0, a shape the
_detect_residual_self_contradiction check was never even RUN against
before this addition (see score_scenario's own widened gate, below,
which is the actual fix -- this phrase alone would do nothing without
it). Negation-guarded like every other phrase here, so a genuine "no
additional factors" statement is unaffected."""

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

_TOTAL_CLAIM_RE = re.compile(
    r"total\s+(?:dollar\s+)?impact\s+of\s*[\+\-]?\$?\s*(?P<amt1>\d[\d,]*(?:\.\d+)?)"
    r"|contribute[sd]?\s+a\s+total\s+(?:dollar\s+impact\s+)?of\s*[\+\-]?\$?\s*(?P<amt2>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
"""Build 3, Day 4, Part 8, Task 1: the fourth named pattern, decision 32's
own fact-doubling defect (Case 4) -- matched directly against its actual
quoted output: 'These causes contribute a total dollar impact of +400.00,
which fully explains the gap', where the real known_gap is 200.0. Case 4's
real evidence has exactly ONE ReconciliationLineItem (a single
self-consistency correction, dollar_impact=200.0); the fresh response
narrated it as two separate causes, each independently worth +200.00, and
then explicitly summed them. Detected precisely as an EXPLICIT claimed
total that mismatches the real known_gap -- not a fragile "the same dollar
figure appears twice" heuristic, which would false-positive on any
legitimate two-cause scenario (Case 2, Case 3, Case 19, ...) whose two
real, distinct causes happen to share a magnitude by coincidence."""


class ScenarioScore(BaseModel):
    """One scenario's scoring result. root_cause_correct is bool for every
    entry currently in BENCHMARK_ENTRIES (every one has a well-defined
    ground_truth_check_field) -- the Literal["not_gradable"] option exists
    for a future entry whose ground truth genuinely can't be checked this
    way, not exercised by any of the current 24."""

    scenario_id: str
    root_cause_correct: bool | Literal["not_gradable"]
    unsupported_claim_patterns: list[
        Literal[
            "sign_dropping",
            "hedge_then_retract",
            "residual_self_contradiction",
            "fact_doubling",
            "data_quality_overclaim",
        ]
    ]
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

_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]|,\s+(?:and|but|or|yet|so)\b", re.IGNORECASE)
"""Build 3, Day 4, Part 8, Task 1 fix: a real Case 10 false positive
(decision 32) traced to a fixed 20-character negation-lookback window --
that scenario's actual sentence, 'this is not evidence that additional
causes exist or that further investigation is needed', has ~50 characters
between 'not' and the violating phrase 'further investigation is needed'
(the compound 'not X or that Y' construction), well outside that window.
Rather than guess a wider fixed number (a speculative rewrite the next
long sentence could just as easily defeat), the negation search now scans
back to the start of the CONTAINING SENTENCE instead -- correctly reaches
the real 'not' in Case 10's exact sentence without reaching into an
unrelated prior sentence, which a much larger fixed window risked doing.

Build 3, Day 5, Part 4 (decision 36's finding #2 -- widening
residual_self_contradiction's gate uncovered a real false negative in
THIS boundary definition, not just the gate itself): a bare period-only
sentence boundary is too coarse for a compound sentence joining two
independent clauses with a coordinating conjunction. PR #128's real Case
2 quote -- "...the investigation did not identify any other causes
beyond those mentioned, and it's possible that there may be additional
factors at play..." -- is ONE sentence (no period until the very end),
so the old period-only boundary put "did not identify" (an unrelated
negation on the FIRST clause) inside the same search window as
"additional factors" (in the SECOND clause, joined only by ", and"),
producing a false negation-guard match on a real violation. A comma
immediately followed by a coordinating conjunction ("and"/"but"/"or"/
"yet"/"so") is now ALSO a boundary -- this correctly separates Case 2's
two independent clauses, while leaving Case 10's own fix untouched:
Case 10's sentence has exactly one such marker (", but"), which sits
BEFORE its own "not", so the window from that boundary to the violating
phrase still includes the real "not" and the negation-guard still fires
correctly there (verified directly, not assumed -- see the regression
test for Case 10's compound negation, still passing after this change)."""


def _violating_phrase_present(prose: str, phrase: str) -> bool:
    for match in re.finditer(re.escape(phrase), prose, re.IGNORECASE):
        preceding = prose[: match.start()]
        boundaries = [m.end() for m in _SENTENCE_BOUNDARY_RE.finditer(preceding)]
        sentence_start = boundaries[-1] if boundaries else 0
        if _NEGATION_RE.search(prose[sentence_start : match.start()]):
            continue
        return True
    return False


def _detect_residual_self_contradiction(prose: str) -> bool:
    """Decision 30's original pattern: the residual-framing instruction's
    own constraint stated correctly in one part of the response, then
    violated in another (Cases 10/11/20's original defect). Build 3, Day
    4, Part 8, Task 1 fix: the original co-occurrence-only design was
    proven, by decision 32's fresh re-test, to MISS a related, one-sided
    variant -- Case 20's fresh response violated the constraint ('there
    may be other underlying causes that have not been identified...
    further investigation is needed') without ever stating the correct
    constraint anywhere in the response, so the old
    `has_correcting and has_violating` check never fired at all. A bare
    violating phrase (correctly negation-guarded, see
    _violating_phrase_present) is now sufficient on its own -- the
    correcting phrase's presence is no longer required, since a response
    that never engages with the constraint at all is not a lesser
    violation than one that recites it and then contradicts it; if
    anything it is a more complete failure to follow the instruction."""
    return any(_violating_phrase_present(prose, phrase) for phrase in _RESIDUAL_VIOLATING_PHRASES)


def _detect_fact_doubling(prose: str, known_gap: float) -> bool:
    """Decision 32's fourth named pattern (Case 4, Build 3 Day 4 Part 7):
    the model narrates a single real cause as two separate causes and
    states an explicit summed total that does not match the scenario's
    real known_gap. Runs unconditionally (like root-cause scoring) --
    an explicit 'total X' claim is checkable the same way for any
    scenario, not just ones where the underlying evidence shape makes the
    risk obvious in advance; a scenario with no such claim in its prose
    simply produces no _TOTAL_CLAIM_RE match and this returns False."""
    for match in _TOTAL_CLAIM_RE.finditer(prose):
        raw = match.group("amt1") or match.group("amt2")
        claimed_total = float(raw.replace(",", ""))
        if round(claimed_total, 2) != round(abs(known_gap), 2):
            return True
    return False


_DATA_QUALITY_OVERCLAIM_PHRASES = [
    "fresh and complete",
    "confirmed clean",
]
"""Build 3, Day 5, Part 4 (decision 36's finding #3, Case 3's own quoted
defect): confident language claiming data quality was checked and found
clean, matched against Case 3's own real transcript verbatim -- 'The
data-quality issues were also found to be zero, indicating that the data
is fresh and complete.' Deliberately gated (see score_scenario, below) to
only ever run when the scenario's real dispatch status
(src.reconciliation_assembly.is_data_quality_dispatched) is False -- this
same language is entirely correct and expected on a scenario that WAS
genuinely checked and found clean (Case 8's own clean pass, decision 30),
so the phrase list alone is not the safety condition here; the gate is.

Deliberately NOT a bare 'data is fresh' or 'confirmed fresh': a real
false positive was found (and fixed) constructing this project's own
honest, correct undispatched response ('...it's unclear whether the
data is fresh, complete, or clean...', Task 1's own live-verified
fix text) -- 'data is fresh' is a literal substring of that entirely
correct, uncertainty-expressing sentence. Both short phrases were
dropped rather than negation-guarded, keeping only the two complete,
specifically-affirmative constructions that don't collide with the
fix's own honest phrasing."""


def _detect_data_quality_overclaim(prose: str) -> bool:
    """Confident "checked and clean" language about data quality, for a
    scenario score_scenario has already confirmed was never dispatched to
    any check at all -- see _DATA_QUALITY_OVERCLAIM_PHRASES for the real
    quote this is matched against and why gating (not phrase wording
    alone) is what keeps this safe. No negation guard needed the way
    _violating_phrase_present has one: every phrase here is itself an
    affirmative claim ('data is fresh'), not one that a preceding "not"
    could flip into the correct statement this check exists to require
    ('data quality was NOT checked')."""
    lowered = prose.lower()
    return any(phrase in lowered for phrase in _DATA_QUALITY_OVERCLAIM_PHRASES)


def score_scenario(entry: BenchmarkEntry, prose: str) -> ScenarioScore:
    """Score one already-generated explainer response against `entry`'s
    ground truth. Deliberately does NOT call the LLM -- `prose` must
    already exist, so this function can be tested against fixed strings
    with zero API cost (Task 2's own requirement)."""
    evidence = assemble_investigation_evidence_for_benchmark(entry)

    checks_run = ["root_cause"]
    root_cause_correct = _score_root_cause(entry, prose, evidence)

    unsupported_claim_patterns: list[
        Literal[
            "sign_dropping",
            "hedge_then_retract",
            "residual_self_contradiction",
            "fact_doubling",
            "data_quality_overclaim",
        ]
    ] = []

    checks_run.append("fact_doubling")
    if _detect_fact_doubling(prose, entry.scenario.known_gap):
        unsupported_claim_patterns.append("fact_doubling")

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

    # Build 3, Day 5, Part 4 (decision 36's finding #2): gate widened beyond
    # data_quality_issues non-empty. A residual that is exactly 0.0 means the
    # causes found ALREADY fully explain the gap, algebraically -- hedging
    # language implying other/uncaptured causes might still exist is exactly
    # as unsupported there as it is in the data-quality-adjacent case this
    # check originally covered, regardless of whether any data-quality
    # finding is even in play. `is not None` guards PartialInvestigationEvidence's
    # unexplained_residual=None (never computed, not "clean") from matching.
    residual_is_clean = evidence.unexplained_residual is not None and evidence.unexplained_residual == 0.0
    if evidence.data_quality_issues or residual_is_clean:
        checks_run.append("residual_self_contradiction")
        if _detect_residual_self_contradiction(prose):
            unsupported_claim_patterns.append("residual_self_contradiction")

    # Build 3, Day 5, Part 4 (decision 36's finding #3): only ever runs when
    # data_quality_issues is empty AND the scenario was never dispatched to
    # any check at all -- a genuinely checked-and-clean scenario (Case 8)
    # legitimately says "no issues found," which must never be flagged here.
    if not evidence.data_quality_issues and not is_data_quality_dispatched(entry.scenario.scenario_id):
        checks_run.append("data_quality_overclaim")
        if _detect_data_quality_overclaim(prose):
            unsupported_claim_patterns.append("data_quality_overclaim")

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
        dispatched = is_data_quality_dispatched(entry.scenario.scenario_id)
        prose = explain_investigation(evidence, data_quality_checked=dispatched)
        scores.append(score_scenario(entry, prose))
    return scores


# --- Build 3, Day 4, Part 8, Task 2: an LLM-graded alternative to the ---
# --- hand-written pattern detector above, for head-to-head comparison. ---


class LLMClaimGrading(BaseModel):
    """Structured yes/no verdict for each of the four named unsupported-
    claim patterns (decisions 14, 30, 32, docs/decisions.md) -- scoped
    exactly to those four, not a general "find hallucinations" grader.
    Field names deliberately match ScenarioScore.unsupported_claim_patterns'
    own Literal values so a caller can compare the two detectors' outputs
    directly, field for field."""

    sign_dropping: bool
    hedge_then_retract: bool
    residual_self_contradiction: bool
    fact_doubling: bool


def _format_grading_prompt(prose: str, evidence: _Evidence, known_gap: float) -> str:
    """Builds a compact ground-truth summary (deliberately NOT a reuse of
    src/explainer.py's own _format_evidence_prompt, which is a private
    function building a DIFFERENT prompt for a DIFFERENT purpose --
    narrating evidence to a stakeholder, not grading a narration against
    it) plus the exact, precisely-worded definition of all four patterns,
    matching decisions 14/30/32's own descriptions."""
    lines: list[str] = ["## Real evidence (ground truth, computed deterministically)"]

    lines.append("Reconciled causes:")
    if evidence.reconciliation:
        for item in evidence.reconciliation:
            lines.append(f"- {item.cause} | dollar_impact={item.dollar_impact:+.2f}")
    else:
        lines.append("- None.")

    lines.append("Self-consistency issues:")
    if evidence.self_consistency_issues:
        for issue in evidence.self_consistency_issues:
            lines.append(
                f"- source={issue.source} declared_field={issue.declared_field} "
                f"dollar_impact={issue.dollar_impact:+.2f}"
            )
    else:
        lines.append("- None.")

    lines.append("Definitional differences:")
    if evidence.definition_differences:
        for d in evidence.definition_differences:
            lines.append(f"- field={d.field} source_a={d.source_a_value!r} source_b={d.source_b_value!r}")
    else:
        lines.append("- None.")

    lines.append("SQL structural differences:")
    if evidence.sql_differences:
        for s in evidence.sql_differences:
            lines.append(f"- category={s.category} | {s.description}")
    else:
        lines.append("- None.")

    lines.append("Data-quality issues:")
    if evidence.data_quality_issues:
        for q in evidence.data_quality_issues:
            lines.append(f"- category={q.category} | dollar_impact={q.dollar_impact:+.2f}")
    else:
        lines.append("- None.")

    lines.append(f"Real known_gap (the true total dollar difference): {known_gap:+.2f}")

    evidence_block = "\n".join(lines)

    return (
        "You are a precise, narrow QA checker. You are given the REAL, deterministically-"
        "computed evidence for one investigation, and a RESPONSE an AI system wrote "
        "narrating that evidence to a business stakeholder. Your ONLY job is to check whether "
        "the RESPONSE exhibits any of four SPECIFIC, precisely-defined defect patterns -- do "
        "not flag anything else, and do not act as a general hallucination detector.\n\n"
        f"{evidence_block}\n\n"
        "## Response under review\n"
        f"{prose}\n\n"
        "Check for exactly these four patterns, each against the real evidence above:\n\n"
        "1. sign_dropping: a NEGATIVE dollar_impact figure from the real evidence is stated "
        "in the response as a bare POSITIVE magnitude, with no minus sign, no parentheses, "
        "and no qualifying word ('reduces', 'offsets', 'negative', 'decreases', or similar) "
        "anywhere near it.\n"
        "2. hedge_then_retract: the response introduces language implying a cause exists in "
        "an evidence category that is actually EMPTY above (e.g. 'we identified another "
        "cause... however, this cause does not exist'), rather than simply omitting or "
        "stating 'none' for that empty category.\n"
        "3. residual_self_contradiction: the response states language like 'other causes may "
        "exist', 'further investigation is needed', or 'only a portion of the gap' about a "
        "data-quality cause that IS present in the real evidence above -- whether or not the "
        "response ALSO correctly states elsewhere that a nonzero residual does not diminish "
        "that cause's completeness. Flag this whenever the violating language appears for a "
        "data-quality cause that is genuinely present, regardless of whether it is also "
        "correctly stated elsewhere.\n"
        "4. fact_doubling: the response describes ONE real cause from the evidence above as "
        "if it were TWO separate causes, and/or states an explicit total dollar figure that "
        "does not match the real known_gap value given above.\n\n"
        "Respond with ONLY a single JSON object, no other text before or after it, no "
        "markdown code fences, with EXACTLY these four boolean fields: "
        '{"sign_dropping": true/false, "hedge_then_retract": true/false, '
        '"residual_self_contradiction": true/false, "fact_doubling": true/false}'
    )


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def score_scenario_llm_graded(entry: BenchmarkEntry, prose: str) -> LLMClaimGrading:
    """Real API call -- a separate, narrowly-scoped LLM grader, given the
    prose plus the real underlying evidence, asked specifically whether
    the prose exhibits any of the four named unsupported-claim patterns.

    DECISION 33 (docs/decisions.md, Build 3, Day 4, Part 9): NOT called
    from run_benchmark's default path, and never will be without a new
    decision overriding this one. A 6-scenario head-to-head comparison
    against the hand-written detector (PR #136) found 4 false positives
    across 6 calls -- hallucinated verdicts against evidence this function
    was directly given -- against 0 for the hand-written detector on the
    identical scenarios. This function, its prompt, and its tests remain
    committed and working, kept as a documented experiment per this
    project's own practice of naming rather than deleting a real,
    tested-but-not-shipped result. Callable directly for ad hoc
    comparison; not wired into any scored pipeline.

    Structured output (LLMClaimGrading), not free text -- the same
    discipline this project has held for every other structured-output
    requirement. Achieved via prompt-instructed JSON + manual pydantic
    validation, NOT generate_structured's own response_schema/
    response_format parameter: a real, live finding from this task's own
    first attempt -- the configured free-tier model
    (meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo, routed through the
    provider's chat_completion endpoint) rejects the request outright
    ('json_schema response format is not supported for model...'), a
    provider/model limitation, not a bug in llm_client.py. This function
    still gets fully validated, typed output -- it just gets there by
    asking for JSON in the prompt and validating the returned text,
    rather than relying on the provider's native structured-output
    feature, which is unavailable for this specific model."""
    evidence = assemble_investigation_evidence_for_benchmark(entry)
    prompt = _format_grading_prompt(prose, evidence, entry.scenario.known_gap)
    raw = generate_structured(prompt)
    assert isinstance(raw, str)
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()
    return LLMClaimGrading.model_validate_json(cleaned)
