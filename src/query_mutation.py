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
                 distinct (Build 3, Day 2 cleanup, Part 1 -- see below),
                 filter (Build 3, Day 2, Part 13 for the ADD direction;
                 Build 3, Day 2 cleanup, Part 1 for the REMOVE direction --
                 see apply_filter_correction's own docstring)
    NOT covered: grouping, other
                 (SQLStructuralDifference categories with no mutation rule --
                 see construct_corrected_query's docstring for why)

A category with no mutation rule raises UnsupportedCorrectionCategory
(NotImplementedError) rather than silently passing the SQL through
unmodified or guessing at a correction -- a gap in coverage must be visible
immediately, not produce a wrong corrected-query result downstream.
NotImplementedError, not ValueError, is deliberate: every OTHER raise in
this module signals "this input is ambiguous, I could support this
category but won't guess which one" (a supported category given bad/
ambiguous input); grouping/other signal a categorically different fact --
"no mutation rule exists for this category at all," independent of the
input. A caller can distinguish the two failure modes by type.

Build 3, Day 2 cleanup, Part 1 investigated all three previously-uncovered
categories (distinct, grouping, other) against this project's own real
scenario shapes before deciding which got full logic vs. a fallback --
see docs/decisions.md for the full reasoning. Summary:

  - `distinct` IS reachable with a real, already-proven fixture (below) --
    given full correction logic (apply_distinct_correction).
  - `grouping` is NOT reachable in a realistic shape for this project:
    every reconciliation primitive in src/reconciliation.py
    (single_cause_attribution, shapley_pair_attribution,
    freshness_attribution) reads its result via `fetchone()[0]` -- a
    SINGLE scalar row. A query with a GROUP BY returns multiple rows;
    reconciling a GROUP BY-differing pair would silently read only an
    arbitrary first group's value, not a real number, corrupting every
    downstream sum. This is not a gap in this module alone -- it is
    architecturally incompatible with this project's whole scalar-KPI
    reconciliation model, and no currently-committed scenario uses GROUP
    BY at all. Left as a fallback, not built on spec.
  - `other` is NOT reachable at all: confirmed by direct inspection,
    src/sql_diff.py's diff_sql() never constructs a
    SQLStructuralDifference with category="other" anywhere -- it is a
    schema-level placeholder (src/schema.py's Literal enum) with zero
    producers. Left as a fallback, not built on spec.

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
its `distinct` category intact -- this is the real, reachable shape
apply_distinct_correction (below) is built and proven against.

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


class UnsupportedCorrectionCategory(NotImplementedError):
    """Raised by construct_corrected_query for a SQLStructuralDifference
    category with no mutation rule at all (grouping, other -- see this
    module's docstring for why each is left uncovered). Distinct from
    ValueError, which this module reserves for a SUPPORTED category given
    genuinely ambiguous input (Build 3, Day 2 cleanup, Part 1) -- a caller
    can tell "not handled yet" apart from "handled, but this input needs a
    human" by exception type alone."""


class DateFieldCorrectionMissing(ValueError):
    """Raised by apply_date_field_correction when the side being corrected
    has ZERO date-like columns -- the query never filters by date at all.
    Build 3, Day 2 cleanup, Part 1: this is a data-completeness fact, not a
    "which column" ambiguity (DateFieldCorrectionAmbiguous, below) -- a
    caller should route it as additive data-quality evidence
    (src/reconciliation_assembly.py), not treat it as a correction
    failure. Subclasses ValueError so any caller that has not been updated
    to catch this specific type still catches it via the pre-existing
    ValueError handling."""


class DateFieldCorrectionAmbiguous(ValueError):
    """Raised by apply_date_field_correction when the side being corrected
    has MORE THAN ONE date-like column -- which one to correct is
    genuinely ambiguous. Build 3, Day 2 cleanup, Part 1: a caller should
    escalate rather than guess (this project's standing discipline --
    decisions 10, 12, 18, 22, 26 never suppress or pick a side without
    proof), the same way assemble_investigation_evidence already refuses
    to guess which sub-pairs interact at 3+ remaining causes. Subclasses
    ValueError for the same backward-compatibility reason as
    DateFieldCorrectionMissing."""


def apply_date_field_correction(sql: str, target_field: str, target_predicate: str | None = None) -> str:
    """Swap the date column used in date-based filtering to `target_field`.

    Requires exactly one existing date-like column in `sql` (per
    src.sql_parser's date-column heuristic). Build 3, Day 2 cleanup, Part
    1: zero and multiple are now two DISTINCT, clearly-typed failure
    modes, not one generic ValueError -- zero (DateFieldCorrectionMissing)
    means the query never filters by date at all, a data-completeness
    fact; multiple (DateFieldCorrectionAmbiguous) means which column to
    correct is genuinely ambiguous. Neither is guessed at; a caller
    decides what to do with each (src/reconciliation_assembly.py).

    `target_predicate` (Build 3, Day 3, Part 2 -- fixes a real, previously-
    undiscovered gap, see the module-level note below): when given, the
    ENTIRE matched predicate leaf is replaced with `target_predicate`
    (parsed via sqlglot), correcting both the column AND the comparison
    threshold/operator to the target side's actual values -- not just the
    column name. When omitted (default, unchanged from this function's
    original Day 6 behavior), only the bare column identifier is renamed in
    place and the original threshold/operator are preserved -- the correct
    behavior for a SelfConsistencyIssue correction, which has no "other
    side" predicate to adopt (a source is only ever compared against its
    own declared definition, not another source's real SQL), and the
    default when construct_corrected_query has no other_side_sql to draw
    a real predicate from.
    """
    parsed = parse_sql(sql)
    existing_bare = sorted({_bare_column(column) for column in parsed.date_columns})
    if not existing_bare:
        raise DateFieldCorrectionMissing(
            "apply_date_field_correction found no date-like column in the given "
            "SQL -- this query does not filter by date at all."
        )
    if len(existing_bare) > 1:
        raise DateFieldCorrectionAmbiguous(
            f"apply_date_field_correction found {len(existing_bare)} date-like "
            f"columns ({existing_bare}) in the given SQL -- which one to correct "
            "is ambiguous."
        )
    current_bare = existing_bare[0]

    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    if where is None:
        raise ValueError("apply_date_field_correction found no WHERE clause to correct a date column in.")

    if target_predicate is not None:
        leaves = [
            leaf
            for leaf in _split_and_condition_nodes(where.this)
            if any(_bare_column(column.sql()) == current_bare for column in leaf.find_all(exp.Column))
        ]
        if len(leaves) != 1:
            raise ValueError(
                f"apply_date_field_correction requires exactly one predicate "
                f"referencing date column '{current_bare}' to replace with a full "
                f"target predicate; found {len(leaves)} in the given SQL."
            )
        leaves[0].replace(sqlglot.condition(target_predicate))
        return tree.sql()

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


def _extract_date_predicate_snippet(sql: str, column: str) -> str:
    """Extract the full raw predicate text (column, operator, AND threshold
    value) for `column`'s date filter in `sql` -- e.g. "last_active_date >=
    '2024-01-01'" -- so a DefinitionDifference('date_field') correction can
    adopt the target side's REAL threshold, not just its column name. Build
    3, Day 3, Part 2: apply_date_field_correction's original column-only-
    swap silently kept the corrected side's OWN threshold literal, which is
    only correct when both sides happen to already use the identical
    threshold (true for Case 3 and all three then-committed ambiguous
    scenarios, confirmed by direct execution, but not a general guarantee --
    AMBIGUOUS_CUSTOMER_COUNTING's two sides use genuinely different
    thresholds, '2000-01-01' vs '2024-01-01', and that mismatch is exactly
    what produced its 50.0 unexplained_residual). Requires exactly one WHERE
    predicate referencing the bare `column` in `sql` -- multiple or zero
    matches make which predicate to adopt ambiguous, so this raises rather
    than guessing, the same discipline every other snippet-extraction
    helper in this module already follows.
    """
    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    leaves = (
        [
            leaf
            for leaf in _split_and_condition_nodes(where.this)
            if any(_bare_column(col.sql()) == column for col in leaf.find_all(exp.Column))
        ]
        if where is not None
        else []
    )
    if len(leaves) != 1:
        raise ValueError(
            f"_extract_date_predicate_snippet requires exactly one predicate "
            f"referencing column '{column}' in the other side's SQL; found {len(leaves)}."
        )
    return leaves[0].sql()


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


def apply_filter_removal(sql: str, own_filter_condition: str) -> str:
    """Strip the predicate referencing `own_filter_condition`'s column
    entirely from `sql`'s WHERE clause (Build 3, Day 2 cleanup, Part 1 --
    the REMOVE direction apply_filter_correction's own docstring left
    unbuilt after Build 3, Day 2, Part 13's ADD direction). `sql` is the
    side WITH the extraneous predicate (a SQLStructuralDifference('filter')
    finding's OWN snippet on this side, e.g.
    CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION's source_a) -- correcting
    toward the other side's real value (no filter at all) means removing
    the clause entirely, mirroring apply_excluded_statuses_correction's own
    "empty target = remove the clause" convention (Build 3, Day 1, Part 5)
    rather than a vacuous always-true predicate.

    `own_filter_condition` is `sql`'s OWN predicate text (e.g. the
    SQLStructuralDifference's query_a_snippet when `sql` is source A) --
    used only to identify which column's predicate to remove; the removal
    itself walks `sql`'s live AST via _remove_predicate (already generic,
    not status-specific, shared with apply_excluded_statuses_correction),
    not string-matched against the passed-in text.

    Requires exactly one predicate in `sql` referencing that column --
    same "raise rather than guess" discipline as every other mutation
    function in this module.
    """
    target_column = _bare_predicate_column(own_filter_condition)
    if target_column is None:
        raise ValueError(
            "apply_filter_removal received a condition that does not name a real "
            f"filter predicate to remove ('{own_filter_condition}')."
        )

    tree = sqlglot.parse_one(sql)
    where = tree.find(exp.Where)
    leaves = (
        [
            leaf
            for leaf in _split_and_condition_nodes(where.this)
            if any(_bare_column(column.sql()) == target_column for column in leaf.find_all(exp.Column))
        ]
        if where is not None
        else []
    )
    if len(leaves) != 1:
        raise ValueError(
            f"apply_filter_removal requires exactly one predicate referencing "
            f"column '{target_column}' in `sql`; found {len(leaves)}."
        )
    _remove_predicate(where, leaves[0])
    return tree.sql()


def apply_filter_correction(sql: str, other_side_filter_condition: str, own_filter_condition: str | None = None) -> str:
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

    Two directions are supported (Build 3, Day 2 cleanup, Part 1 added the
    second):

      - ADD (Build 3, Day 2, Part 13, unchanged): `other_side_filter_condition`
        names a real predicate -- `sql` is the side MISSING it, added via
        sqlglot's own `.where()` builder (AND-appends to an existing WHERE
        clause, or creates one from scratch if `sql` has none at all).
      - REMOVE (Build 3, Day 2 cleanup, Part 1, new): `other_side_filter_condition`
        is the "(no filter on this column)" placeholder -- `sql` is
        actually the side WITH the filter (e.g.
        CASE_13_FILTER_EXCLUDED_STATUSES_COLLISION's source_a) and the
        correction needed is REMOVAL. `own_filter_condition` (`sql`'s own
        predicate text, e.g. the same finding's query_a_snippet) is
        REQUIRED in this branch -- the placeholder alone carries no column
        identity to remove -- and this delegates to apply_filter_removal
        (above) for the actual mechanics. Raises clearly if
        `own_filter_condition` is missing rather than guessing which
        column `sql` might need stripped.

    Raises clearly, not silently, for the shape neither direction covers:
    `sql` already has its own predicate on the same column as
    `other_side_filter_condition` -- would mean both sides have differing
    predicates on this column, a shape src.sql_diff's presence-only
    `_diff_filters` should never produce as a `filter` finding in the first
    place (it only flags a column filtered on one side and absent on the
    other); raising rather than silently overwriting or double-adding a
    predicate.
    """
    if other_side_filter_condition.strip() == _NO_FILTER_SNIPPET_PLACEHOLDER:
        if own_filter_condition is None:
            raise ValueError(
                "apply_filter_correction received the '(no filter on this column)' "
                "placeholder (a REMOVE-direction correction) but no "
                "own_filter_condition to identify which predicate to remove."
            )
        return apply_filter_removal(sql, own_filter_condition)

    target_column = _bare_predicate_column(other_side_filter_condition)
    if target_column is None:
        raise ValueError(
            "apply_filter_correction received a target that does not name a real "
            f"filter predicate to add ('{other_side_filter_condition}')."
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


def apply_distinct_correction(sql: str, target_has_distinct: bool) -> str:
    """Toggle DISTINCT on `sql`'s sole aggregate call to match
    `target_has_distinct` (Build 3, Day 2 cleanup, Part 1 -- the `distinct`
    SQLStructuralDifference category, confirmed reachable in a real,
    already-proven fixture: COUNT(DISTINCT customer_id) vs MAX(customer_id),
    tests/test_structural_definitional_precedence.py::
    test_does_not_over_fire_on_unrelated_distinct_and_aggregation_findings --
    decision 10's suppression does not fire here since 'max' is a genuinely
    different function, not the same fact under a DISTINCT toggle, so
    `distinct` survives to reach this function for real).

    Requires exactly one aggregate call in `sql` -- same "raise rather than
    guess" discipline as apply_aggregation_correction, which this function
    otherwise mirrors closely: only the DISTINCT wrapping changes, the
    aggregate function and its column/expression are preserved exactly.
    Toggling DISTINCT on a function where it is not meaningful (e.g. MAX,
    already the target of this module's own reachable-collision proof) is
    a legal SQL no-op, not an error -- sqlglot round-trips it faithfully
    and DuckDB accepts it, so this function does not special-case which
    function it is wrapping.
    """
    tree = sqlglot.parse_one(sql)
    agg_nodes = list(tree.find_all(exp.AggFunc))
    if len(agg_nodes) != 1:
        raise ValueError(
            f"apply_distinct_correction requires exactly one aggregate call; "
            f"found {len(agg_nodes)} in the given SQL."
        )
    agg_node = agg_nodes[0]
    inner = agg_node.this
    column_expr = inner.expressions[0] if isinstance(inner, exp.Distinct) else inner

    if target_has_distinct:
        agg_node.set("this", exp.Distinct(expressions=[column_expr]))
    else:
        agg_node.set("this", column_expr)
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


def _extract_date_field_target_from_snippet(snippet: str) -> str:
    """Extract a single bare date column name from a SQLStructuralDifference
    date_field snippet (Build 3, Day 2 cleanup, Part 1 -- the target_value
    plumbing gap), mirroring _extract_join_target_from_snippet's "adopt B's
    value" default. src.sql_diff._diff_date_fields joins multiple columns
    per side with '; ' when a side itself has more than one date column
    (its own ambiguous case) and uses the literal '(no date filter)'
    placeholder when a side has none -- neither shape names a single,
    unambiguous target, so both raise rather than guess, the same
    discipline as every other snippet extractor in this module."""
    try:
        condition = sqlglot.condition(snippet)
    except Exception as exc:
        raise ValueError(f"could not parse a date column from snippet '{snippet}'.") from exc
    columns = {_bare_column(column.sql()) for column in condition.find_all(exp.Column)}
    if len(columns) != 1:
        raise ValueError(
            f"could not extract a single unambiguous date column from snippet "
            f"'{snippet}' (found {sorted(columns) or 'none'})."
        )
    return next(iter(columns))


_STRUCTURAL_AGGREGATION_TARGETS = {"SUM": "sum", "COUNT": "count"}
"""SQLStructuralDifference's aggregation snippet (src.sql_diff._diff_aggregations)
carries only the bare SQL function name -- DISTINCT is tracked separately
at the query level (src/sql_parser.py's AggregationCall docstring) and
never folded in, so a structural snippet alone can never distinguish
COUNT from COUNT DISTINCT. Extraction is therefore limited to the two
functions a bare name maps to unambiguously; anything else (including a
genuinely DISTINCT target this extraction cannot see) raises rather than
silently producing a non-DISTINCT correction that might be wrong."""


def _extract_aggregation_target_from_snippet(snippet: str) -> str:
    """Extract a target_aggregation value from a SQLStructuralDifference
    aggregation snippet (Build 3, Day 2 cleanup, Part 1), e.g. 'SUM(amount)'
    -> 'sum'. Only succeeds for a snippet that parses to exactly one
    AggFunc call with a function in _STRUCTURAL_AGGREGATION_TARGETS --
    src.sql_diff's own "differing NUMBER of calls" branch produces a
    comma-joined list or the literal '(none)', neither of which parses to
    a single AggFunc, so that shape correctly raises here too rather than
    guessing which call matters."""
    try:
        tree = sqlglot.parse_one(snippet)
    except Exception as exc:
        raise ValueError(f"could not parse an aggregate function from snippet '{snippet}'.") from exc
    if not isinstance(tree, exp.AggFunc):
        raise ValueError(f"snippet '{snippet}' does not parse to a single aggregate function call.")
    function = type(tree).__name__.upper()
    if function not in _STRUCTURAL_AGGREGATION_TARGETS:
        raise ValueError(
            f"could not extract a supported aggregation target from snippet "
            f"'{snippet}' (function '{function}' is not one of "
            f"{sorted(_STRUCTURAL_AGGREGATION_TARGETS)})."
        )
    return _STRUCTURAL_AGGREGATION_TARGETS[function]


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
    other_side_sql: str | None = None,
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
      - SQLStructuralDifference, category "date_field" or "aggregation"
        (Build 3, Day 2 cleanup, Part 1 -- the target_value plumbing gap):
        no structured target value exists on this schema (only descriptive
        text), so when target_value is not passed explicitly this now
        extracts one from `difference.query_b_snippet` (same "adopt B's
        value" convention as join_type/filter) via
        _extract_date_field_target_from_snippet /
        _extract_aggregation_target_from_snippet -- both raise ValueError
        not guess, when the snippet doesn't parse to a single unambiguous
        target (e.g. a side with its own multiple date columns, or the
        "differing NUMBER of aggregate calls" shape). Genuinely ambiguous
        input still fails loudly; only the previously-unconditional raise
        (regardless of whether extraction was even attempted) is gone.
      - SQLStructuralDifference, category "filter" (Build 3, Day 2, Part
        13 for ADD, Build 3, Day 2 cleanup, Part 1 for REMOVE): defaults
        to `difference.query_b_snippet` (same "adopt B's value" convention
        as join_type), routed to apply_filter_correction, with
        `difference.query_a_snippet` also passed through as
        `own_filter_condition` -- used only by the REMOVE branch (when
        query_b_snippet is the "(no filter on this column)" placeholder,
        meaning original_sql is the side WITH the filter and needs it
        stripped), a no-op for the ADD branch.
      - SQLStructuralDifference, category "distinct" (Build 3, Day 2
        cleanup, Part 1, new): defaults to
        `parse_sql(difference.query_b_snippet).has_distinct` -- distinct's
        own query_a_snippet/query_b_snippet (src.sql_diff._diff_distinct)
        is each side's FULL raw SQL, not a bare predicate, so "adopt B's
        value" here means re-parsing B's own SQL for its has_distinct
        flag, not extracting a snippet fragment. An explicit target_value
        override for this category is read as the string "true"/"false"
        (case-insensitive), not any other truthy/falsy convention.
      - SQLStructuralDifference, category "grouping" or "other": no
        mutation rule exists at all -- raises UnsupportedCorrectionCategory
        (NotImplementedError), regardless of target_value. See this
        module's docstring for why each is left uncovered.

    In every case an explicit `target_value` argument overrides the default.

    `other_side_sql` (Build 3, Day 3, Part 2, new parameter -- fixes a real
    gap found root-causing AMBIGUOUS_CUSTOMER_COUNTING's 50.0
    unexplained_residual): for a DefinitionDifference on `date_field` only,
    when `target_value` was NOT manually overridden, `other_side_sql` (the
    OTHER source's real, uncorrected SQL) is parsed to extract the target
    column's actual filter predicate -- column AND comparison threshold --
    via _extract_date_predicate_snippet, so the corrected query adopts the
    target side's REAL threshold rather than silently keeping
    `original_sql`'s own, possibly-different one (apply_date_field_correction's
    original Day 6 behavior, still the fallback when `other_side_sql` is
    omitted). This value was not previously accessible at this function's
    call site at all -- no existing field on DefinitionDifference or
    SQLStructuralDifference (once decision 12 suppression discards the
    latter) carries a real threshold literal, so a new parameter, not a
    smarter default, was required. Ignored for every other difference
    type/field/category -- SelfConsistencyIssue has no "other side" to draw
    a threshold from at all (see apply_date_field_correction's own
    docstring), and non-date_field DefinitionDifference fields/SQLStructuralDifference
    categories have their own, already-correct target-value mechanisms.
    """
    if isinstance(difference, SelfConsistencyIssue):
        target = target_value if target_value is not None else difference.declared_value
        return _dispatch_field(original_sql, difference.declared_field, target)

    if isinstance(difference, DefinitionDifference):
        target = target_value if target_value is not None else difference.source_b_value
        if difference.field == "date_field" and target_value is None and other_side_sql is not None:
            target_predicate = _extract_date_predicate_snippet(other_side_sql, target)
            return apply_date_field_correction(original_sql, target, target_predicate)
        return _dispatch_field(original_sql, difference.field, target)

    if isinstance(difference, SQLStructuralDifference):
        category = difference.category
        if category == "join_type":
            target = target_value if target_value is not None else _extract_join_target_from_snippet(difference.query_b_snippet)
            return apply_join_type_correction(original_sql, target)
        if category == "date_field":
            target = target_value if target_value is not None else _extract_date_field_target_from_snippet(difference.query_b_snippet)
            return apply_date_field_correction(original_sql, target)
        if category == "aggregation":
            target = target_value if target_value is not None else _extract_aggregation_target_from_snippet(difference.query_b_snippet)
            return apply_aggregation_correction(original_sql, target)
        if category == "filter":
            target = target_value if target_value is not None else difference.query_b_snippet
            return apply_filter_correction(original_sql, target, own_filter_condition=difference.query_a_snippet)
        if category == "distinct":
            target_has_distinct = (
                target_value.strip().lower() == "true"
                if target_value is not None
                else parse_sql(difference.query_b_snippet).has_distinct
            )
            return apply_distinct_correction(original_sql, target_has_distinct)
        raise UnsupportedCorrectionCategory(
            f"No corrected-query mutation rule exists for SQLStructuralDifference "
            f"category '{category}'. Covered categories: join_type, date_field, "
            "aggregation, filter, distinct. Not covered: grouping, other."
        )

    raise TypeError(f"construct_corrected_query does not support difference type {type(difference).__name__}.")
