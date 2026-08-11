"""Deterministic structural diff over two parsed SQL queries. Compares joins,
filters, date fields, aggregation, DISTINCT usage, and grouping, producing
list[SQLStructuralDifference] per src/schema.py. No LLM calls, no metric-
definition or self-consistency logic — this is the trust boundary that has
to be right before any explainer ever sees its output.

Filter-value differences (e.g. which statuses a status filter excludes) are
deliberately NOT compared here: only whether a column is filtered on at all
differs between sides. Comparing the values inside a shared-column filter is
Day 3's declared-definition-diff job, not this module's."""

import sqlglot
from sqlglot import exp

from src.schema import SQLStructuralDifference
from src.sql_parser import ParsedSQL


def diff_sql(parsed_a: ParsedSQL, parsed_b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Compare two parsed SQL structures and return only the categories that
    actually differ — an empty list means the queries match on every
    structural dimension this tool checks."""
    return [
        *_diff_joins(parsed_a, parsed_b),
        *_diff_filters(parsed_a, parsed_b),
        *_diff_date_fields(parsed_a, parsed_b),
        *_diff_aggregations(parsed_a, parsed_b),
        *_diff_distinct(parsed_a, parsed_b),
        *_diff_grouping(parsed_a, parsed_b),
    ]


def _bare_column(column: str) -> str:
    """Strip a table alias/qualifier so 'o.order_date' and 'order_date' compare equal."""
    return column.rsplit(".", 1)[-1].lower()


def _diff_joins(a: ParsedSQL, b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Flag a join-kind mismatch only where both sides join on the same condition."""
    joins_a = {join.condition: join.kind for join in a.joins}
    joins_b = {join.condition: join.kind for join in b.joins}
    diffs = []
    for condition, kind_a in joins_a.items():
        kind_b = joins_b.get(condition)
        if kind_b is not None and kind_a != kind_b:
            diffs.append(
                SQLStructuralDifference(
                    category="join_type",
                    description=(
                        f"source_a uses {kind_a} JOIN, source_b uses {kind_b} JOIN "
                        f"on the same join condition ({condition})"
                    ),
                    query_a_snippet=f"{kind_a} JOIN ON {condition}",
                    query_b_snippet=f"{kind_b} JOIN ON {condition}",
                )
            )
    return diffs


def _column_filter_map(parsed: ParsedSQL) -> dict[str, str]:
    """Map bare column name -> the raw filter text that references it, for
    each top-level AND-ed WHERE predicate. Predicates with no column
    (e.g. constant expressions) are skipped."""
    mapping: dict[str, str] = {}
    for filter_text in parsed.where_filters:
        columns = list(sqlglot.condition(filter_text).find_all(exp.Column))
        if not columns:
            continue
        mapping[_bare_column(columns[0].sql())] = filter_text
    return mapping


def _diff_filters(a: ParsedSQL, b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Flag a column that is filtered on one side and not filtered at all on
    the other. Date columns are excluded here since date_field already
    covers them; a shared column with differing filter values (e.g. a
    different excluded-status list) is not flagged — that's Day 3's job."""
    date_bare_a = {_bare_column(c) for c in a.date_columns}
    date_bare_b = {_bare_column(c) for c in b.date_columns}
    map_a = {col: text for col, text in _column_filter_map(a).items() if col not in date_bare_a}
    map_b = {col: text for col, text in _column_filter_map(b).items() if col not in date_bare_b}

    diffs = []
    for col in sorted(set(map_a) - set(map_b)):
        diffs.append(
            SQLStructuralDifference(
                category="filter",
                description=f"source_a filters on '{col}', source_b's query has no equivalent filter on '{col}'",
                query_a_snippet=map_a[col],
                query_b_snippet="(no filter on this column)",
            )
        )
    for col in sorted(set(map_b) - set(map_a)):
        diffs.append(
            SQLStructuralDifference(
                category="filter",
                description=f"source_b filters on '{col}', source_a's query has no equivalent filter on '{col}'",
                query_a_snippet="(no filter on this column)",
                query_b_snippet=map_b[col],
            )
        )
    return diffs


def _diff_date_fields(a: ParsedSQL, b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Flag when the two sides use different columns for date-based filtering."""
    bare_a = {_bare_column(c) for c in a.date_columns}
    bare_b = {_bare_column(c) for c in b.date_columns}
    if bare_a == bare_b:
        return []

    map_a = _column_filter_map(a)
    map_b = _column_filter_map(b)
    snippet_a = "; ".join(map_a.get(_bare_column(c), c) for c in a.date_columns) or "(no date filter)"
    snippet_b = "; ".join(map_b.get(_bare_column(c), c) for c in b.date_columns) or "(no date filter)"

    return [
        SQLStructuralDifference(
            category="date_field",
            description=(
                f"source_a uses {sorted(a.date_columns) or 'no column'} for date-based filtering, "
                f"source_b uses {sorted(b.date_columns) or 'no column'}"
            ),
            query_a_snippet=snippet_a,
            query_b_snippet=snippet_b,
        )
    ]


def _diff_aggregations(a: ParsedSQL, b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Flag a differing number of aggregate calls, or (when counts match)
    any call whose function or column differs at the same position. This
    positional comparison assumes SELECT-clause aggregate order corresponds
    across the two queries, which holds for every scenario in scope."""
    if len(a.aggregations) != len(b.aggregations):
        return [
            SQLStructuralDifference(
                category="aggregation",
                description=(
                    f"source_a has {len(a.aggregations)} aggregate call(s), "
                    f"source_b has {len(b.aggregations)}"
                ),
                query_a_snippet=", ".join(f"{c.function}({c.column})" for c in a.aggregations) or "(none)",
                query_b_snippet=", ".join(f"{c.function}({c.column})" for c in b.aggregations) or "(none)",
            )
        ]

    diffs = []
    for call_a, call_b in zip(a.aggregations, b.aggregations):
        same_function = call_a.function == call_b.function
        same_column = _bare_column(call_a.column) == _bare_column(call_b.column)
        if not (same_function and same_column):
            diffs.append(
                SQLStructuralDifference(
                    category="aggregation",
                    description=(
                        f"source_a aggregates via {call_a.function}({call_a.column}), "
                        f"source_b aggregates via {call_b.function}({call_b.column})"
                    ),
                    query_a_snippet=f"{call_a.function}({call_a.column})",
                    query_b_snippet=f"{call_b.function}({call_b.column})",
                )
            )
    return diffs


def _diff_distinct(a: ParsedSQL, b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Flag when one side uses DISTINCT and the other does not."""
    if a.has_distinct == b.has_distinct:
        return []
    return [
        SQLStructuralDifference(
            category="distinct",
            description=(
                f"source_a {'uses' if a.has_distinct else 'does not use'} DISTINCT, "
                f"source_b {'uses' if b.has_distinct else 'does not use'} DISTINCT"
            ),
            query_a_snippet=a.raw_sql,
            query_b_snippet=b.raw_sql,
        )
    ]


def _diff_grouping(a: ParsedSQL, b: ParsedSQL) -> list[SQLStructuralDifference]:
    """Flag when GROUP BY columns differ between the two sides."""
    bare_a = sorted(_bare_column(c) for c in a.group_by_columns)
    bare_b = sorted(_bare_column(c) for c in b.group_by_columns)
    if bare_a == bare_b:
        return []
    return [
        SQLStructuralDifference(
            category="grouping",
            description=(
                f"source_a groups by {a.group_by_columns or '(no GROUP BY)'}, "
                f"source_b groups by {b.group_by_columns or '(no GROUP BY)'}"
            ),
            query_a_snippet=", ".join(a.group_by_columns) or "(no GROUP BY)",
            query_b_snippet=", ".join(b.group_by_columns) or "(no GROUP BY)",
        )
    ]
