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

    covered:     date_field, excluded_statuses, aggregation, join_type,
                 filter (Build 3, Day 2, Part 13 -- ADD direction only,
                 see apply_filter_correction's own docstring for the
                 REMOVE direction this does not cover)
    NOT covered: distinct, grouping, other
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


def _remove_predicate(where: exp.Where, leaf: exp.Expression) -> None:
    """Remove `leaf` from `where`'s AND-chain entirely -- collapsing to
    whichever sibling condition remains if `leaf` was AND-ed with others,
    or dropping the WHERE clause altogether if `leaf` was the only
    condition. Used when a correction target names zero statuses to
    exclude, meaning "no exclusion filter on this column at all" (Build 3,
    Day 1, Part 5, locked design decision) -- NOT a vacuous `NOT IN ()`,
    which is a different, not-requested reading. Both tree shapes verified
    directly before writing this: a multi-predicate AND-chain has `leaf`
    as a direct child of an `exp.And` node, so replacing that `And` node
    with `leaf`'s sibling correctly collapses to the remaining
    condition(s); a single-predicate WHERE has `leaf` AS `where.this`
    directly (parent is the `Where` node itself, not an `And`), so
    `where.pop()` is what removes it instead."""
    parent = leaf.parent
    if isinstance(parent, exp.And):
        sibling = parent.expression if parent.this is leaf else parent.this
        parent.replace(sibling)
    else:
        where.pop()


_EXCLUDED_STATUSES_COLUMN = "status"
"""The bare column name apply_excluded_statuses_correction's zero-predicate
ADD case builds a new predicate on. Matches src.definition_diff's own
hardcoded assumption (_status_filter_texts/_infer_excluded_statuses always
look for a bare 'status' column; already noted as a standing assumption in
src.self_consistency._same_filter_exclusion_fact's own docstring) -- not a
new assumption introduced here, just the first place it needs to be
written down explicitly rather than only ever read back off an existing
predicate."""


def _build_excluded_statuses_predicate(column_sql: str, statuses: list[str]) -> exp.Expression:
    """Build a NEQ (single status) or NOT IN (multiple statuses) exclusion
    predicate node for `column_sql`, excluding exactly `statuses`. Shared
    by both apply_excluded_statuses_correction's rewrite-existing path and
    its zero-predicate ADD path (Build 3, Day 2, Part 14) -- the same
    predicate-shape decision, not two independent implementations."""
    excluded = sorted(set(statuses))
    if len(excluded) == 1:
        return exp.NEQ(this=exp.to_column(column_sql), expression=exp.Literal.string(excluded[0]))
    return exp.Not(
        this=exp.In(this=exp.to_column(column_sql), expressions=[exp.Literal.string(v) for v in excluded])
    )


def apply_excluded_statuses_correction(sql: str, target_statuses: list[str]) -> str:
    """Rewrite the status-exclusion filter to exclude exactly `target_statuses`,
    remove the exclusion filter entirely when `target_statuses` is empty
    (Build 3, Day 1, Part 5, locked design decision: "corrected toward zero
    exclusions" means no exclusion clause on that column at all, not an
    empty `NOT IN ()`), or ADD a brand-new exclusion predicate when `sql`
    has no status predicate at all yet (Build 3, Day 2, Part 14 -- mirrors
    apply_filter_correction's proven ADD-direction pattern exactly, same
    session's own precedent: correcting a side missing something toward a
    real target, not rewriting or removing an existing one).

    Zero-predicate case (Part 14, new): when `sql` has no WHERE clause at
    all, or a WHERE clause with zero predicates referencing the bare
    'status' column, and `target_statuses` is non-empty, a new NOT IN
    (Xs)/!= predicate (via _build_excluded_statuses_predicate, shared with
    the rewrite path below) is added via sqlglot's own `.where()` builder
    (AND-appends to an existing WHERE, or creates one from scratch --
    verified directly in apply_filter_correction, Part 13). When `sql` has
    zero status predicates AND `target_statuses` is ALSO empty, this is
    already-correct-toward-zero -- a no-op, `sql` returned unchanged, not
    an error (this shape should not arise from a real diff_definitions
    comparison, since both sides would already match with zero
    exclusions, but is handled explicitly rather than left to the
    populated-predicate branch below to mishandle).

    Populated-predicate case (existing, UNCHANGED by Part 14): requires
    exactly one WHERE predicate that references the bare 'status' column,
    in a recognized exclusion shape (NOT IN or !=) -- same recognized
    shapes as src.definition_diff's inference -- REGARDLESS of whether
    `target_statuses` is empty or populated: removing/rewriting a filter
    mechanically still requires being sure exactly one recognized
    status-exclusion predicate exists to touch. Multiple status
    predicates, or an unrecognized shape (e.g. status = 'active'), still
    raise rather than guessing which filter to touch or how -- that
    discipline is untouched; only the previously-unconditional zero-case
    raise is now a distinct, valid branch.
    """
    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    leaves = _status_predicate_leaves(where.this) if where is not None else []

    if not leaves:
        if not target_statuses:
            return tree.sql()
        new_predicate = _build_excluded_statuses_predicate(_EXCLUDED_STATUSES_COLUMN, target_statuses)
        return tree.where(new_predicate.sql()).sql()

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

    if not target_statuses:
        _remove_predicate(where, leaf)
        return tree.sql()

    column_node = leaf.find(exp.Column)
    if column_node is None:
        raise ValueError("apply_excluded_statuses_correction could not find a status column in the matched predicate.")
    column_sql = column_node.sql()

    new_node = _build_excluded_statuses_predicate(column_sql, target_statuses)
    leaf.replace(new_node)
    return tree.sql()


_NO_FILTER_SNIPPET_PLACEHOLDER = "(no filter on this column)"
"""Matches src.sql_diff._diff_filters's own literal placeholder text for
whichever side has no filter on the differing column -- the signal that a
SQLStructuralDifference('filter') finding's query_a_snippet/query_b_snippet
holds this string, not a real predicate, on that side."""


def _bare_predicate_column(condition_sql: str) -> str | None:
    """Extract the single bare column name referenced in a raw filter
    predicate string (e.g. "status NOT IN ('lost', 'open')"), parsed via
    sqlglot rather than string matching -- same reliability reasoning as
    every other snippet-parsing helper in this codebase. Returns None when
    the string doesn't parse as a condition, or references anything other
    than exactly one distinct bare column."""
    try:
        condition = sqlglot.condition(condition_sql)
    except Exception:
        return None
    columns = {_bare_column(column.sql()) for column in condition.find_all(exp.Column)}
    if len(columns) != 1:
        return None
    return next(iter(columns))


def apply_filter_correction(sql: str, other_side_filter_condition: str) -> str:
    """Add a filter predicate to `sql`'s WHERE clause, sourced from
    `other_side_filter_condition` -- the OTHER side's actual filter text on
    the same column (a SQLStructuralDifference('filter') finding's
    query_a_snippet/query_b_snippet, per src.sql_diff._diff_filters, which
    is presence-only: exactly one side has a real predicate on the
    differing column, the other has none at all). `sql` is the side
    MISSING the predicate -- this function adds it, using the other side's
    predicate verbatim (same column, same predicate shape) as the source
    of truth, not a declared value (SQLStructuralDifference('filter')
    findings carry no target value of their own -- Build 3, Day 2, Part 13,
    locked design decision).

    Scope, locked: only the ADD direction is supported -- correcting a
    side that is missing a filter, toward the other side's real predicate.
    Raises clearly, not silently, for the two shapes this does not cover:

      - `other_side_filter_condition` is itself the "(no filter on this
        column)" placeholder -- meaning `sql` is actually the side WITH
        the filter (e.g. CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION's
        source_a) and the correction needed is REMOVAL, the reverse
        direction. That is a separate, unlocked design decision (does
        "correcting toward no filter" mean dropping the predicate
        entirely, mirroring apply_excluded_statuses_correction's own
        zero-exclusion convention from Part 5, or something else?) --
        not decided or built here.
      - `sql` already has its own predicate on the same column as
        `other_side_filter_condition` -- would mean both sides have
        differing predicates on this column, a shape src.sql_diff's
        presence-only `_diff_filters` should never produce as a `filter`
        finding in the first place (it only flags a column filtered on
        one side and absent on the other); raising rather than silently
        overwriting or double-adding a predicate.

    Uses sqlglot's own `.where()` builder (AND-appends to an existing
    WHERE clause, or creates one from scratch if `sql` has none at all --
    verified directly, not assumed, since every prior mutation function in
    this module only ever rewrites an EXISTING predicate and none needed
    to handle the zero-WHERE-clause case before this one).
    """
    target_column = _bare_predicate_column(other_side_filter_condition)
    if other_side_filter_condition.strip() == _NO_FILTER_SNIPPET_PLACEHOLDER or target_column is None:
        raise ValueError(
            "apply_filter_correction received a target that does not name a real "
            f"filter predicate to add ('{other_side_filter_condition}'); removing an "
            "existing filter (the reverse direction) is not supported."
        )

    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    if where is not None:
        existing_columns = {_bare_column(column.sql()) for column in where.this.find_all(exp.Column)}
        if target_column in existing_columns:
            raise ValueError(
                f"apply_filter_correction found `sql` already has its own predicate "
                f"referencing column '{target_column}'; adding a second, differing "
                "predicate on the same column is not a well-formed correction."
            )

    return tree.where(other_side_filter_condition).sql()


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
    value (comma-joined, e.g. 'churned, trial') back into a list of status
    strings. `'(none)'` (src.definition_diff's own placeholder for "zero
    statuses excluded") parses to an EMPTY list, not an error (Build 3,
    Day 1, Part 5, locked design decision) -- apply_excluded_statuses_correction
    treats an empty target as "remove the exclusion filter entirely," the
    correct reading of "corrected toward zero exclusions" (not a vacuous
    `NOT IN ()`). Before this fix, an empty result here raised
    unconditionally; that raise is now apply_excluded_statuses_correction's
    job alone, for the cases that are genuinely still ambiguous (zero or
    multiple status predicates, or an unrecognized predicate shape) -- not
    for a well-formed, deliberately empty target."""
    return [status.strip() for status in value.split(",") if status.strip() and status.strip() != "(none)"]


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
      - SQLStructuralDifference, category "filter" (Build 3, Day 2, Part
        13): defaults to `difference.query_b_snippet` (same "adopt B's
        value" convention as join_type), routed to
        apply_filter_correction -- which itself raises if that snippet is
        the "(no filter on this column)" placeholder (the ADD direction
        is not applicable; original_sql already has the filter and needs
        REMOVAL instead, a separate, unlocked design decision) or if
        original_sql already has its own predicate on that column.
      - SQLStructuralDifference, any other category ("distinct",
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
        if category == "filter":
            target = target_value if target_value is not None else difference.query_b_snippet
            return apply_filter_correction(original_sql, target)
        raise ValueError(
            f"No corrected-query mutation rule exists for SQLStructuralDifference "
            f"category '{category}'. Covered categories: join_type, date_field, "
            "aggregation, filter. Not covered: distinct, grouping, other."
        )

    raise TypeError(f"construct_corrected_query does not support difference type {type(difference).__name__}.")
