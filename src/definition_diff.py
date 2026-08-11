"""Deterministic metric-definition diff. Compares two DeclaredDefinition
instances field by field (diff_declared_definitions, Day 3 Part 1), and
infers the same three fields from parsed SQL when no declared config exists
(infer_definition_from_sql, Day 3 Part 2). No LLM calls anywhere: inference
means rule-based extraction from parsed SQL structure, not an LLM guessing
at business intent. No self-consistency checks (that is Day 4's job).

This is also where the value-level comparison Day 2's sql_diff._diff_filters
deliberately stayed out of actually happens: excluded_statuses is compared as
a set of values, not merely flagged as "filtered vs not filtered".

Confidence rule for inferred fields:
  - "high" is reserved exclusively for declared values — never assigned to an
    inferred one, no matter how mechanical the extraction was.
  - "medium" when extraction was mechanical and unambiguous: exactly one
    date-like column found, exactly one status filter in a recognized
    NOT IN/!= exclusion form, exactly one aggregate call.
  - "low" when extraction required guessing: zero or multiple date columns,
    zero or multiple status filters (or a filter in an unrecognized/inclusion
    form, e.g. status = 'active'), zero or multiple aggregate calls.

source/confidence combination rule, when a DefinitionDifference spans one
declared side and one inferred side ("weakest link" — a single field has one
source and one confidence, not one per side, so the less-trustworthy
contributor determines both):
  - source is "declared" only when BOTH sides are declared; if either side is
    inferred, source is "inferred".
  - confidence is the lower of the two sides' confidences (high > medium >
    low), so a declared/inferred pairing can never read as "high" overall.
"""

from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp

from src.schema import DefinitionDifference
from src.scenario import DashboardSource, DeclaredDefinition
from src.sql_parser import ParsedSQL, parse_sql
from src.sql_diff import _bare_column

_InferredConfidence = Literal["medium", "low"]
_Confidence = Literal["high", "medium", "low"]
_CONFIDENCE_RANK: dict[_Confidence, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass
class InferredField:
    """One inferred definition field: its best-guess value and the honest
    confidence assigned to that guess. Never "high" — see module docstring."""

    value: str
    confidence: _InferredConfidence


@dataclass
class InferredDefinition:
    """The three DeclaredDefinition-equivalent fields, inferred from parsed
    SQL structure alone, each with its own confidence. excluded_statuses is
    carried as a comma-joined string (via InferredField.value) so both
    declared and inferred sides resolve to the same comparable string shape."""

    date_field: InferredField
    excluded_statuses: InferredField
    aggregation: InferredField


def diff_declared_definitions(
    def_a: DeclaredDefinition, def_b: DeclaredDefinition
) -> list[DefinitionDifference]:
    """Compare two declared metric definitions and return only the fields
    that actually differ — an empty list means the two declarations match on
    every field this tool checks.

    Both arguments must be present DeclaredDefinition instances. Callers must
    not pass None on either side: a source with no declared_definition is not
    this function's job (that is Day 3 Part 2's inferred-fallback path) and
    silently returning an empty list for a missing declaration would be
    indistinguishable from "the declarations matched," which is a dangerous
    ambiguity to leave unresolved. This function raises a clear TypeError
    instead of guessing.
    """
    if def_a is None or def_b is None:
        raise TypeError(
            "diff_declared_definitions requires both sides to have a declared "
            "definition; a None side must be routed to the Day 3 Part 2 "
            "inferred-fallback path instead of being passed here."
        )
    diffs = []
    if def_a.date_field != def_b.date_field:
        diffs.append(
            DefinitionDifference(
                field="date_field",
                source_a_value=def_a.date_field,
                source_b_value=def_b.date_field,
                source="declared",
                confidence="high",
            )
        )
    set_a, set_b = set(def_a.excluded_statuses), set(def_b.excluded_statuses)
    if set_a != set_b:
        diffs.append(
            DefinitionDifference(
                field="excluded_statuses",
                source_a_value=", ".join(sorted(set_a)) or "(none)",
                source_b_value=", ".join(sorted(set_b)) or "(none)",
                source="declared",
                confidence="high",
            )
        )
    if def_a.aggregation != def_b.aggregation:
        diffs.append(
            DefinitionDifference(
                field="aggregation",
                source_a_value=def_a.aggregation,
                source_b_value=def_b.aggregation,
                source="declared",
                confidence="high",
            )
        )
    return diffs


def infer_definition_from_sql(parsed: ParsedSQL) -> InferredDefinition:
    """Extract a best-guess definition from already-parsed SQL structure.
    Calls into src.sql_parser's output rather than re-parsing; this function
    only maps ParsedSQL's raw structural facts onto the three
    DeclaredDefinition-equivalent fields, per the confidence rule documented
    in this module's docstring."""
    return InferredDefinition(
        date_field=_infer_date_field(parsed),
        excluded_statuses=_infer_excluded_statuses(parsed),
        aggregation=_infer_aggregation(parsed),
    )


def _infer_date_field(parsed: ParsedSQL) -> InferredField:
    """Exactly one date-like column -> medium confidence. Zero (nothing to
    point to) or multiple (which one governs the metric is ambiguous) ->
    low confidence, best guess is the alphabetically-first candidate."""
    columns = sorted({_bare_column(c) for c in parsed.date_columns})
    if len(columns) == 1:
        return InferredField(value=columns[0], confidence="medium")
    if not columns:
        return InferredField(value="(none found)", confidence="low")
    return InferredField(value=columns[0], confidence="low")


def _status_filter_texts(parsed: ParsedSQL) -> list[str]:
    """WHERE predicates that reference a bare 'status' column."""
    matches = []
    for filter_text in parsed.where_filters:
        columns = list(sqlglot.condition(filter_text).find_all(exp.Column))
        if any(_bare_column(c.sql()) == "status" for c in columns):
            matches.append(filter_text)
    return matches


def _excluded_values_from_predicate(node: exp.Expression) -> list[str] | None:
    """Recognize a NOT IN or != exclusion predicate and return the excluded
    literal values. Returns None for any other predicate shape (e.g. a bare
    '=' inclusion filter), since that shape doesn't express an excluded set."""
    if isinstance(node, exp.Not) and isinstance(node.this, exp.In):
        return [lit.this for lit in node.this.expressions if isinstance(lit, exp.Literal)]
    if isinstance(node, exp.NEQ):
        literal = node.expression if isinstance(node.expression, exp.Literal) else node.this
        if isinstance(literal, exp.Literal):
            return [literal.this]
    return None


def _infer_excluded_statuses(parsed: ParsedSQL) -> InferredField:
    """Exactly one recognized NOT IN/!= status-exclusion predicate -> medium
    confidence. Zero status filters, more than one, or a predicate shape that
    isn't a recognized exclusion form (e.g. status = 'active') -> low
    confidence, best guess is an empty set."""
    status_filters = _status_filter_texts(parsed)
    if len(status_filters) == 1:
        excluded = _excluded_values_from_predicate(sqlglot.condition(status_filters[0]))
        if excluded is not None:
            return InferredField(value=", ".join(sorted(set(excluded))) or "(none)", confidence="medium")
    return InferredField(value="(none)", confidence="low")


def _infer_aggregation(parsed: ParsedSQL) -> InferredField:
    """Exactly one aggregate call -> medium confidence (COUNT + query-level
    DISTINCT maps to 'count_distinct', matching DeclaredDefinition's
    vocabulary). Zero or multiple aggregate calls -> low confidence."""
    if len(parsed.aggregations) == 1:
        call = parsed.aggregations[0]
        function = call.function.lower()
        if function == "count" and parsed.has_distinct:
            function = "count_distinct"
        return InferredField(value=function, confidence="medium")
    return InferredField(value="(ambiguous)", confidence="low")


def _resolve_side(
    field: str, declared: DeclaredDefinition | None, inferred: InferredDefinition | None
) -> tuple[str, _Confidence, Literal["declared", "inferred"]]:
    """Resolve one side's (value, confidence, source) for a given field, from
    whichever of declared/inferred is actually present for that side."""
    if declared is not None:
        raw_value = getattr(declared, field)
        if field == "excluded_statuses":
            raw_value = ", ".join(sorted(set(raw_value))) or "(none)"
        return raw_value, "high", "declared"
    inferred_field: InferredField = getattr(inferred, field)
    return inferred_field.value, inferred_field.confidence, "inferred"


def _values_equal(field: str, value_a: str, value_b: str) -> bool:
    """excluded_statuses is compared as a set (comma-joined string parsed back
    apart) so ordering never produces a spurious difference; other fields
    compare as exact strings."""
    if field == "excluded_statuses":
        set_a = {v.strip() for v in value_a.split(",") if v.strip() and v.strip() != "(none)"}
        set_b = {v.strip() for v in value_b.split(",") if v.strip() and v.strip() != "(none)"}
        return set_a == set_b
    return value_a == value_b


def diff_definitions(source_a: DashboardSource, source_b: DashboardSource) -> list[DefinitionDifference]:
    """Top-level entry point: routes to declared-vs-declared comparison when
    both sides have a declared definition, otherwise infers the missing
    side(s) from SQL and compares declared-vs-inferred or inferred-vs-inferred.
    See module docstring for the source/confidence combination rule."""
    declared_a = source_a.declared_definition
    declared_b = source_b.declared_definition

    if declared_a is not None and declared_b is not None:
        return diff_declared_definitions(declared_a, declared_b)

    inferred_a = None if declared_a is not None else infer_definition_from_sql(parse_sql(source_a.sql))
    inferred_b = None if declared_b is not None else infer_definition_from_sql(parse_sql(source_b.sql))

    diffs = []
    for field in ("date_field", "excluded_statuses", "aggregation"):
        value_a, confidence_a, source_side_a = _resolve_side(field, declared_a, inferred_a)
        value_b, confidence_b, source_side_b = _resolve_side(field, declared_b, inferred_b)

        if _values_equal(field, value_a, value_b):
            continue

        overall_source = "declared" if source_side_a == "declared" and source_side_b == "declared" else "inferred"
        overall_confidence = min((confidence_a, confidence_b), key=lambda c: _CONFIDENCE_RANK[c])

        diffs.append(
            DefinitionDifference(
                field=field,
                source_a_value=value_a,
                source_b_value=value_b,
                source=overall_source,
                confidence=overall_confidence,
            )
        )
    return diffs
