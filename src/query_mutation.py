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

Uses sqlglot for the actual mutation (parse, modify the relevant AST node,
regenerate SQL), the same reliability reasons src/sql_parser.py and
src/sql_diff.py are built on sqlglot rather than string manipulation.
Reuses src.sql_parser.parse_sql (existing date-column extraction) and
src.sql_diff._bare_column (existing table-qualifier stripping) rather than
re-implementing either. Deterministic only -- no LLM calls anywhere.

This first slice covers the two WHERE-clause-level mutations: date_field and
excluded_statuses. Aggregation, join_type, and the top-level dispatcher
follow in a separate commit.
"""

import sqlglot
from sqlglot import exp

from src.sql_diff import _bare_column
from src.sql_parser import parse_sql


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
