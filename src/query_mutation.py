"""Rule-based SQL corrected-query construction (Build 1, Day 6, Task 1).

Every prior counterfactual "corrected" SQL variant in this project (Case 2 /
Case 3's X-only / Y-only queries hand-fed to shapley_pair_attribution in Day
5 Task 2) was manually authored. This module replaces that manual step with
a mechanical one: given a found difference (DefinitionDifference,
SelfConsistencyIssue, or SQLStructuralDifference) and the original SQL it
applies to, construct the SQL that would result if just that one cause were
corrected.

Explicit scope boundary: this is a narrow, rule-based mutator, not a general
SQL AST-rewriting engine. One targeted mutation function exists per
difference category/field this project's schema (src/schema.py) already
knows about:

    covered:     date_field, excluded_statuses, aggregation, join_type
    NOT covered: filter, distinct, grouping, other
                 (SQLStructuralDifference categories with no mutation rule --
                 see construct_corrected_query's docstring for why)

A category with no mutation rule raises ValueError rather than silently
passing the SQL through unmodified or guessing at a correction -- a gap in
coverage must be visible immediately, not produce a wrong corrected-query
result downstream.

Note on `distinct` specifically: decision 10 (docs/decisions.md) suppresses
a `distinct` SQLStructuralDifference in favor of the corresponding
`aggregation` DefinitionDifference when both trace to the same underlying
COUNT/COUNT DISTINCT fact (assemble_structural_and_definitional_evidence,
src/self_consistency.py). That suppression is conditional, not universal --
it does not make `distinct` unreachable here. The already-committed
over-fire test (tests/test_structural_definitional_precedence.py::
test_does_not_over_fire_on_unrelated_distinct_and_aggregation_findings)
proves a genuinely different-function case (COUNT(DISTINCT customer_id) vs
MAX(customer_id)) is NOT suppressed and does reach downstream evidence with
its `distinct` category intact. Confirmed live: feeding that exact
SQLStructuralDifference into construct_corrected_query raises the expected
ValueError, not a silent mishandling. `distinct` staying on the uncovered
list is therefore still the correct call, but not for the reason "decision
10 already filters it out before it gets here" -- that reasoning only holds
for the same-fact case.

Uses sqlglot for the actual mutation (parse, modify the relevant AST node,
regenerate SQL), the same reliability reasons src/sql_parser.py and
src/sql_diff.py are built on sqlglot rather than string manipulation.
Reuses src.sql_parser.parse_sql (existing date-column extraction) and
src.sql_diff._bare_column (existing table-qualifier stripping) rather than
re-implementing either. Deterministic only -- no LLM calls anywhere.
"""

import sqlglot
from sqlglot import exp

from src.schema import DefinitionDifference, SelfConsistencyIssue, SQLStructuralDifference
from src.sql_diff import _bare_column
from src.sql_parser import parse_sql

_SUPPORTED_JOIN_TARGETS = ("INNER", "LEFT", "RIGHT", "FULL")
_SUPPORTED_AGGREGATION_TARGETS = ("sum", "count", "count_distinct")


def apply_date_field_correction(sql: str, target_field: str) -> str:
    """Swap the date column used in date-based filtering to `target_field`.

    Requires exactly one existing date-like column in `sql` (per
    src.sql_parser's date-column heuristic) -- if zero or more than one is
    found, which column to correct is ambiguous, so this raises rather than
    guessing. The original column's table qualifier (if any, e.g. "o." in
    "o.order_date") is preserved; only the bare field name is replaced.
    """
    parsed = parse_sql(sql)
    existing_bare = sorted({_bare_column(column) for column in parsed.date_columns})
    if len(existing_bare) != 1:
        raise ValueError(
            f"apply_date_field_correction requires exactly one existing date "
            f"column to correct; found {existing_bare or '(none)'} in the given SQL."
        )
    current_bare = existing_bare[0]

    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    if where is None:
        raise ValueError("apply_date_field_correction found no WHERE clause to correct a date column in.")

    matched = False
    for column in where.find_all(exp.Column):
        if _bare_column(column.sql()) == current_bare:
            column.set("this", exp.to_identifier(target_field))
            matched = True
    if not matched:
        raise ValueError(
            f"apply_date_field_correction located date column '{current_bare}' via "
            "parse_sql but could not find a matching Column node to rewrite."
        )
    return tree.sql()


def _split_and_condition_nodes(node: exp.Expression) -> list[exp.Expression]:
    """Flatten a chain of AND-connected predicate nodes into individual leaf
    nodes, returning live references into the tree (not copies or strings),
    so a leaf can be replaced in place via .replace()."""
    if isinstance(node, exp.And):
        return _split_and_condition_nodes(node.this) + _split_and_condition_nodes(node.expression)
    return [node]


def _status_predicate_leaves(where_condition: exp.Expression) -> list[exp.Expression]:
    """WHERE predicate leaves that reference a bare 'status' column."""
    return [
        leaf
        for leaf in _split_and_condition_nodes(where_condition)
        if any(_bare_column(column.sql()) == "status" for column in leaf.find_all(exp.Column))
    ]


def _is_recognized_exclusion_shape(node: exp.Expression) -> bool:
    """Same recognized shapes as src.definition_diff._excluded_values_from_predicate:
    a NOT IN predicate, or a != predicate. Anything else (e.g. a bare '='
    inclusion filter) is not a rewritable exclusion filter."""
    if isinstance(node, exp.Not) and isinstance(node.this, exp.In):
        return True
    return isinstance(node, exp.NEQ)


def apply_excluded_statuses_correction(sql: str, target_statuses: list[str]) -> str:
    """Rewrite the status-exclusion filter to exclude exactly `target_statuses`.

    Requires exactly one WHERE predicate that references a bare 'status'
    column, in a recognized exclusion shape (NOT IN or !=) -- same
    recognized shapes as src.definition_diff's inference. Zero or multiple
    status predicates, or an unrecognized shape (e.g. status = 'active'),
    raise rather than guessing which filter to rewrite or how.
    """
    if not target_statuses:
        raise ValueError("apply_excluded_statuses_correction requires at least one target status to exclude.")

    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    if where is None:
        raise ValueError("apply_excluded_statuses_correction found no WHERE clause to correct a status filter in.")

    leaves = _status_predicate_leaves(where.this)
    if len(leaves) != 1:
        raise ValueError(
            f"apply_excluded_statuses_correction requires exactly one status-filter "
            f"predicate; found {len(leaves)} in the given SQL."
        )
    leaf = leaves[0]
    if not _is_recognized_exclusion_shape(leaf):
        raise ValueError(
            f"apply_excluded_statuses_correction found a status predicate in an "
            f"unrecognized shape ('{leaf.sql()}'); only NOT IN / != exclusion "
            "filters can be mechanically rewritten."
        )

    column_node = leaf.find(exp.Column)
    if column_node is None:
        raise ValueError("apply_excluded_statuses_correction could not find a status column in the matched predicate.")
    column_sql = column_node.sql()

    excluded = sorted(set(target_statuses))
    if len(excluded) == 1:
        new_node: exp.Expression = exp.NEQ(this=exp.to_column(column_sql), expression=exp.Literal.string(excluded[0]))
    else:
        new_node = exp.Not(
            this=exp.In(this=exp.to_column(column_sql), expressions=[exp.Literal.string(v) for v in excluded])
        )
    leaf.replace(new_node)
    return tree.sql()


def apply_aggregation_correction(sql: str, target_aggregation: str) -> str:
    """Swap the aggregation function/DISTINCT usage to match `target_aggregation`
    ("sum", "count", or "count_distinct" -- the same vocabulary as
    DeclaredDefinition.aggregation and infer_definition_from_sql's output).

    Requires exactly one aggregate call in `sql` -- zero or multiple make
    "the" aggregation to correct ambiguous, so this raises rather than
    guessing which call to rewrite. The aggregated column/expression is
    preserved; only the function (and DISTINCT wrapping) changes.
    """
    tree = sqlglot.parse_one(sql)
    agg_nodes = list(tree.find_all(exp.AggFunc))
    if len(agg_nodes) != 1:
        raise ValueError(
            f"apply_aggregation_correction requires exactly one aggregate call; "
            f"found {len(agg_nodes)} in the given SQL."
        )
    agg_node = agg_nodes[0]
    inner = agg_node.this
    column_expr = inner.expressions[0] if isinstance(inner, exp.Distinct) else inner
    column_sql = column_expr.sql()

    target = target_aggregation.lower()
    if target == "count_distinct":
        new_node: exp.Expression = exp.Count(this=exp.Distinct(expressions=[exp.to_column(column_sql)]))
    elif target == "count":
        new_node = exp.Count(this=exp.to_column(column_sql))
    elif target == "sum":
        new_node = exp.Sum(this=exp.to_column(column_sql))
    else:
        raise ValueError(
            f"apply_aggregation_correction received unsupported target_aggregation "
            f"'{target_aggregation}'; supported: {', '.join(_SUPPORTED_AGGREGATION_TARGETS)}."
        )
    agg_node.replace(new_node)
    return tree.sql()


def apply_join_type_correction(sql: str, target_join: str) -> str:
    """Swap the JOIN kind (INNER / LEFT / RIGHT / FULL) to `target_join`.

    Requires exactly one JOIN clause in `sql` -- multiple joins make "the"
    join to correct ambiguous, so this raises rather than guessing which one.
    """
    tree = sqlglot.parse_one(sql)
    joins = list(tree.find_all(exp.Join))
    if len(joins) != 1:
        raise ValueError(
            f"apply_join_type_correction requires exactly one JOIN clause; "
            f"found {len(joins)} in the given SQL."
        )
    join = joins[0]
    target = target_join.strip().upper()
    if target == "INNER":
        join.set("kind", "INNER")
        join.set("side", None)
    elif target in ("LEFT", "RIGHT", "FULL"):
        join.set("side", target)
        join.set("kind", None)
    else:
        raise ValueError(
            f"apply_join_type_correction received unsupported target_join "
            f"'{target_join}'; supported: {', '.join(_SUPPORTED_JOIN_TARGETS)}."
        )
    return tree.sql()


def _parse_excluded_statuses_value(value: str) -> list[str]:
    """Parse a DefinitionDifference/SelfConsistencyIssue excluded_statuses
    value (comma-joined, e.g. 'churned, trial', or '(none)') back into a
    list of status strings."""
    statuses = [status.strip() for status in value.split(",") if status.strip() and status.strip() != "(none)"]
    if not statuses:
        raise ValueError(f"excluded_statuses target value '{value}' does not name any statuses to exclude.")
    return statuses


def _extract_join_target_from_snippet(snippet: str) -> str:
    """Extract a join-kind keyword from a SQLStructuralDifference join_type
    snippet, which src.sql_diff._diff_joins formats as e.g.
    '{kind} JOIN ON {condition}'."""
    token = snippet.strip().split(" ", 1)[0] if snippet.strip() else ""
    if token.upper() not in _SUPPORTED_JOIN_TARGETS:
        raise ValueError(f"could not extract a recognized join-type target from snippet '{snippet}'.")
    return token.upper()


def _dispatch_field(original_sql: str, field: str, target: str) -> str:
    """Route a resolved (field, target) pair to the matching mutation
    function. Shared by DefinitionDifference/SelfConsistencyIssue routing
    and the date_field/aggregation branch of SQLStructuralDifference
    routing in construct_corrected_query."""
    if field == "date_field":
        return apply_date_field_correction(original_sql, target)
    if field == "excluded_statuses":
        return apply_excluded_statuses_correction(original_sql, _parse_excluded_statuses_value(target))
    if field == "aggregation":
        return apply_aggregation_correction(original_sql, target)
    raise ValueError(
        f"No corrected-query mutation rule exists for field '{field}'. "
        "Covered fields: date_field, excluded_statuses, aggregation."
    )


def construct_corrected_query(
    original_sql: str,
    difference: DefinitionDifference | SelfConsistencyIssue | SQLStructuralDifference,
    target_value: str | None = None,
) -> str:
    """Route `difference` to the correct mutation function based on its
    field/category and apply it to `original_sql`, returning the corrected
    SQL. Raises clearly (ValueError/TypeError) if no rule exists for the
    given difference's field/category, or if `difference`'s type is not one
    of the three evidence types this dispatcher understands.

    target_value resolution:
      - SelfConsistencyIssue: defaults to `difference.declared_value` -- the
        self-consistency case has one unambiguous correction target, namely
        the source's own declared definition.
      - DefinitionDifference: defaults to `difference.source_b_value` -- in
        this project's counterfactuals (Case 2/Case 3, Task 2), "corrected"
        means "source A's query, with this one field adopting source B's
        value." Pass target_value explicitly for the opposite direction.
      - SQLStructuralDifference, category "join_type": defaults to a value
        extracted from `difference.query_b_snippet` (same "adopt B's value"
        convention as DefinitionDifference).
      - SQLStructuralDifference, category "date_field" or "aggregation":
        no structured target value exists on this schema (only descriptive
        text) -- target_value MUST be passed explicitly, or this raises.
      - SQLStructuralDifference, any other category ("filter", "distinct",
        "grouping", "other"): no mutation rule exists at all -- raises
        unconditionally, regardless of target_value.

    In every case an explicit `target_value` argument overrides the default.
    """
    if isinstance(difference, SelfConsistencyIssue):
        target = target_value if target_value is not None else difference.declared_value
        return _dispatch_field(original_sql, difference.declared_field, target)

    if isinstance(difference, DefinitionDifference):
        target = target_value if target_value is not None else difference.source_b_value
        return _dispatch_field(original_sql, difference.field, target)

    if isinstance(difference, SQLStructuralDifference):
        category = difference.category
        if category == "join_type":
            target = target_value if target_value is not None else _extract_join_target_from_snippet(difference.query_b_snippet)
            return apply_join_type_correction(original_sql, target)
        if category in ("date_field", "aggregation"):
            if target_value is None:
                raise ValueError(
                    f"SQLStructuralDifference category '{category}' does not carry a "
                    "structured target value (query_a_snippet/query_b_snippet are "
                    "descriptive text, not a bare field/function name) -- target_value "
                    "must be passed explicitly for this category."
                )
            return _dispatch_field(original_sql, category, target_value)
        raise ValueError(
            f"No corrected-query mutation rule exists for SQLStructuralDifference "
            f"category '{category}'. Covered categories: join_type, date_field, "
            "aggregation. Not covered: filter, distinct, grouping, other."
        )

    raise TypeError(f"construct_corrected_query does not support difference type {type(difference).__name__}.")
