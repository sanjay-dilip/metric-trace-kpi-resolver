"""Deterministic dollar/count-attribution arithmetic for interacting cause
pairs (Build 1, Day 5, Task 2). Implements decision 11 (docs/decisions.md):
pairwise counterfactual averaging -- the exact 2-cause Shapley solution --
replacing the fixed-order sequential subtraction that real execution proved
wrong on Cases 2 and 3 (naive subtraction was order-dependent and off by
real amounts, e.g. 400.0 on Case 3).

Split into its own module rather than folded into src/self_consistency.py:
that module is evidence-*gathering* (running the diff/self-consistency
tools and applying precedence rules between their outputs). This module is
evidence-*consuming* arithmetic over already-known cause pairs -- a
different layer, with no dependency in either direction on
self_consistency.py, definition_diff.py, or sql_diff.py's internals (it
only executes SQL strings the caller supplies and does arithmetic on the
numeric results).

New surface area, flagged explicitly (per this task's own instruction not
to assume this away): constructing a "cause X corrected, cause Y left
as-written" SQL variant from an abstract DefinitionDifference would require
a generic SQL AST-mutation engine. No such engine exists anywhere in this
codebase -- src/sql_parser.py and src/sql_diff.py are read-only (parse and
diff; they never rewrite a query). Building one now, for a two-case proof,
would be exactly the kind of speculative abstraction the project's
"minimum code that solves the problem" convention rules out (and decision
11 makes the same call explicitly: overlap-detection-before-averaging is
deferred to Build 3, not built speculatively against 6-7 scenarios).
Consequently shapley_pair_attribution's signature takes the four already-
constructed SQL query variants directly, not abstract cause objects plus a
base_query -- the caller (a scenario's fixture author, same as every
counterfactual hand-built in the Day 4 close-out and Day 5 Task 1
verification sessions) is responsible for writing the "X only corrected"
and "Y only corrected" SQL text. If a later build needs this to happen
mechanically, that is new, separately-scoped work (the AST-mutation engine
above), not something this task silently grew to include.
"""

import math

import duckdb

# Tolerance for the sum-check float comparison. DuckDB SUM/COUNT over this
# project's seed data (small integer counts or exact-decimal dollar amounts)
# produces values with no meaningful accumulated floating-point error, so
# this is set tight -- it exists to catch a real arithmetic bug, not to
# paper over expected float noise.
_SUM_CHECK_TOLERANCE = 1e-9


def _execute_scalar(seed_db_path: str, sql: str) -> float:
    """Run `sql` against the DuckDB file at `seed_db_path` and return its
    single scalar result as a float. Read-only connection: this module
    never writes to seed data, only queries it."""
    con = duckdb.connect(seed_db_path, read_only=True)
    try:
        result = con.execute(sql).fetchone()[0]
    finally:
        con.close()
    return float(result)


def shapley_pair_attribution(
    seed_db_path: str,
    baseline_sql: str,
    x_only_sql: str,
    y_only_sql: str,
    both_sql: str,
) -> tuple[float, float]:
    """Attribute the total delta between `baseline_sql` and `both_sql` to
    two interacting causes X and Y, via pairwise Shapley averaging (exact
    at n=2): each cause's marginal contribution is computed under both
    possible orderings and averaged.

        X-then-Y ordering: X alone = x_only - baseline; Y on top of X = both - x_only
        Y-then-X ordering: Y alone = y_only - baseline; X on top of Y = both - y_only
        X's attribution = average of X's two marginal contributions
        Y's attribution = average of Y's two marginal contributions

    All four SQL variants are executed against the same `seed_db_path`
    (this project's cause-pair test cases -- Case 2, Case 3 -- use
    identical row data on both the "_a" and "_b" seed files, so either
    file works; the caller picks which one to point at).

    Sum-check: (X's attribution + Y's attribution) is asserted to equal
    (both - baseline) exactly (within _SUM_CHECK_TOLERANCE). Important
    caveat, stated plainly rather than overclaiming what this proves: this
    equality is an ALGEBRAIC IDENTITY of the two-ordering averaging formula
    above -- it holds no matter what x_only/y_only's actual values are,
    since those terms cancel out of the sum by construction. It is a real
    regression check (it would only ever fail from a coding error in this
    function, e.g. a mixed-up sign or a wrong variable), but it does NOT by
    itself prove x_only_sql/y_only_sql were constructed correctly or that
    the attribution split between X and Y is meaningful -- that proof comes
    from comparing the result against known real-execution figures (done in
    this task's Case 2/Case 3 verification, not inside this function).
    """
    baseline = _execute_scalar(seed_db_path, baseline_sql)
    x_only = _execute_scalar(seed_db_path, x_only_sql)
    y_only = _execute_scalar(seed_db_path, y_only_sql)
    both = _execute_scalar(seed_db_path, both_sql)

    x_attribution = ((x_only - baseline) + (both - y_only)) / 2
    y_attribution = ((y_only - baseline) + (both - x_only)) / 2

    total_delta = both - baseline
    summed = x_attribution + y_attribution
    if not math.isclose(summed, total_delta, abs_tol=_SUM_CHECK_TOLERANCE):
        raise AssertionError(
            f"Shapley sum-check failed: x_attribution ({x_attribution}) + "
            f"y_attribution ({y_attribution}) = {summed}, expected "
            f"both - baseline = {total_delta}. This is an algebraic identity "
            f"of the averaging formula -- a failure here indicates a bug in "
            f"shapley_pair_attribution itself, not bad input data."
        )

    return x_attribution, y_attribution


def freshness_attribution(seed_db_path_stale: str, seed_db_path_complete: str, query_sql: str) -> float:
    """Build 2, Day 2. Attribute the dollar effect of a data-freshness cause
    (stale extract, missing partition, late-arriving data): the SAME query
    is executed against two different DuckDB files -- the stale/as-delivered
    snapshot and the complete counterfactual -- rather than
    single_cause_attribution/shapley_pair_attribution's own shape (one
    database, two SQL variants). Freshness's counterfactual is a difference
    in the underlying DATA, not the query, which is exactly why this
    function's signature is shaped the opposite way from its siblings.

    Returns (complete - stale), mirroring single_cause_attribution's own
    (corrected - baseline) return convention exactly, with "complete"
    playing "corrected"'s role and "stale" playing "baseline"'s -- so a
    caller (src/data_quality.py) applies the identical sign convention
    already established for SelfConsistencyIssue.dollar_impact (negate for
    a source="a" issue, use as-is for source="b") without inventing a new
    rule for this cause type.
    """
    stale = _execute_scalar(seed_db_path_stale, query_sql)
    complete = _execute_scalar(seed_db_path_complete, query_sql)
    return complete - stale


def single_cause_attribution(seed_db_path: str, baseline_sql: str, corrected_sql: str) -> float:
    """Attribute the full delta to a single cause -- no pairwise averaging.

    Design choice: when only one cause exists (e.g. Case 1's lone join-type
    difference), the pairwise machinery is not invoked at all, rather than
    calling shapley_pair_attribution with some degenerate second cause.
    Averaging "X alone" against "X alone again" would just return the same
    number both ways, so the averaging step would be a no-op wrapped around
    extra query executions for no benefit -- simpler and more honest to
    skip the pairwise engine entirely and attribute the full observed delta
    (baseline - corrected) directly to the one cause that produced it.
    """
    baseline = _execute_scalar(seed_db_path, baseline_sql)
    corrected = _execute_scalar(seed_db_path, corrected_sql)
    return corrected - baseline
