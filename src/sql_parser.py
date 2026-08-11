"""Thin sqlglot wrapper that parses a raw SQL string into the structural
pieces Day 2 Part 2's diff tool will compare: joins, WHERE filters,
date-related columns, aggregation functions, DISTINCT usage, and GROUP BY
columns. Parsing only — no comparison logic lives here."""

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

_DATE_LITERAL_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DATE_NAME_PATTERN = re.compile(r"date|_at$", re.IGNORECASE)
_COMPARATORS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


@dataclass
class JoinClause:
    """One JOIN in a query: its kind (INNER, LEFT, RIGHT, ...) and ON condition."""

    kind: str
    condition: str


@dataclass
class AggregationCall:
    """One aggregate function call: its function name and the column or
    expression it wraps (DISTINCT stripped out — that's captured separately
    via has_distinct, not folded into the column text)."""

    function: str
    column: str


@dataclass
class ParsedSQL:
    """The structural pieces extracted from one parsed SQL query. Not a
    pydantic schema boundary like InvestigationEvidence — an internal
    parsing utility consumed only by Day 2 Part 2's diff tool."""

    raw_sql: str
    joins: list[JoinClause]
    where_filters: list[str]
    date_columns: list[str]
    aggregations: list[AggregationCall]
    has_distinct: bool
    group_by_columns: list[str]


def parse_sql(sql: str) -> ParsedSQL:
    """Parse a raw SQL string into the structural pieces used for later diffing."""
    tree = sqlglot.parse_one(sql)

    joins = [
        JoinClause(
            kind=(join.args.get("kind") or join.args.get("side") or "INNER").upper(),
            condition=join.args["on"].sql() if join.args.get("on") else "",
        )
        for join in tree.find_all(exp.Join)
    ]

    where = tree.find(exp.Where)
    where_filters = _split_and_conditions(where.this) if where else []
    date_columns = sorted(_extract_date_columns(where.this)) if where else []

    aggregations = [_extract_aggregation_call(agg) for agg in tree.find_all(exp.AggFunc)]
    has_distinct = bool(list(tree.find_all(exp.Distinct)))

    group = tree.find(exp.Group)
    group_by_columns = [column.sql() for column in group.expressions] if group else []

    return ParsedSQL(
        raw_sql=sql,
        joins=joins,
        where_filters=where_filters,
        date_columns=date_columns,
        aggregations=aggregations,
        has_distinct=has_distinct,
        group_by_columns=group_by_columns,
    )


def _extract_aggregation_call(agg: exp.AggFunc) -> AggregationCall:
    """Build one AggregationCall from an AggFunc node, unwrapping a nested
    DISTINCT (e.g. COUNT(DISTINCT customer_id)) down to its bare column."""
    argument = agg.this
    if isinstance(argument, exp.Distinct):
        column = argument.expressions[0].sql() if argument.expressions else argument.sql()
    else:
        column = argument.sql()
    return AggregationCall(function=type(agg).__name__.upper(), column=column)


def _split_and_conditions(node: exp.Expression) -> list[str]:
    """Flatten a chain of AND-connected predicates into individual condition strings."""
    if isinstance(node, exp.And):
        return _split_and_conditions(node.this) + _split_and_conditions(node.expression)
    return [node.sql()]


def _extract_date_columns(node: exp.Expression) -> set[str]:
    """Heuristically identify columns compared against a date-like literal
    (YYYY-MM-DD) or named like a date field, within a WHERE predicate tree."""
    columns: set[str] = set()
    for comparison in node.find_all(_COMPARATORS):
        column = comparison.this if isinstance(comparison.this, exp.Column) else None
        literal = comparison.expression if isinstance(comparison.expression, exp.Literal) else None
        if column is None:
            column = comparison.expression if isinstance(comparison.expression, exp.Column) else None
            literal = comparison.this if isinstance(comparison.this, exp.Literal) else literal
        if column is None:
            continue

        is_date_literal = bool(literal and literal.is_string and _DATE_LITERAL_PATTERN.match(literal.this))
        is_date_name = bool(_DATE_NAME_PATTERN.search(column.this.this))
        if is_date_literal or is_date_name:
            columns.add(column.sql())
    return columns
