"""Pydantic schema for input scenario representation. A scenario is the raw
input to an investigation: two SQL queries and their reported values, with an
optional declared metric-definition config per side. No loading, parsing, or
diff logic lives here — this module is schema-only."""

from pydantic import BaseModel


class DeclaredDefinition(BaseModel):
    """A source's self-declared metric definition, used for declared-vs-declared
    or declared-vs-inferred comparison in the Day 3 definition diff tool."""

    date_field: str
    excluded_statuses: list[str]
    aggregation: str


class DashboardSource(BaseModel):
    """One side of a KPI dispute: the SQL that produced a reported number, plus
    its declared metric definition if the source author provided one. A None
    declared_definition triggers the Day 3 inferred-fallback path instead of a
    declared-vs-declared comparison."""

    label: str
    sql: str
    declared_definition: DeclaredDefinition | None


class Scenario(BaseModel):
    """One KPI dispute between two dashboard sources, with the known gap between
    their reported values. known_gap is defined as reported_value_a minus
    reported_value_b: positive means source A reports higher than source B,
    negative means source B reports higher than source A. known_gap is the
    hand-authored, independently-asserted expected value -- it is not derived
    from executing against seed_table, and this schema change does not alter
    that.

    seed_table names the DuckDB table containing the synthetic row-level data
    that this scenario's queries (source_a.sql, source_b.sql) execute
    against. The table itself is created and populated separately, in a
    fixture-build step -- this field only names it. For a scenario where a
    Build 2 data-quality/freshness cause is being tested, seed_table (via
    its existing "_a"/"_b" per-side resolution, scripts/build_seed_data.py)
    names the "stale/as-delivered" snapshot -- the data actually in place
    when source_a.sql/source_b.sql produced reported_value_a/reported_value_b.
    This is an interpretation, not a renaming: seed_table's meaning for
    every Build 1 fixture is completely unchanged; a freshness fixture
    simply happens to be a case where "the data as it was" and "stale" are
    the same fact.

    freshness_complete_seed_table (Build 2, Day 1; None for every
    non-freshness scenario, including all 7 Build 1 fixtures, which are
    unaffected) names the base for the second, "complete" counterfactual
    snapshot pair a freshness cause needs -- what source_a.sql/source_b.sql
    would have returned had the data been fully current, rather than
    stale/incomplete. It resolves via the exact same "_a"/"_b" per-side
    suffixing seed_table already uses (once Build 2 Day 2+ writes the
    resolving code): {freshness_complete_seed_table}_a.duckdb /
    {freshness_complete_seed_table}_b.duckdb, sitting alongside
    {seed_table}_a.duckdb / {seed_table}_b.duckdb rather than replacing
    them. Naming judgment call, flagged explicitly (matching how
    seed_table's own "two files per scenario" interpretation was flagged
    in Build 1): the task that requested this field suggested a single
    base with a four-way suffix chain instead
    ({seed_table}_a_complete.duckdb / {seed_table}_a_stale.duckdb / ..._b_complete... / ..._b_stale...).
    A second, independent field was chosen instead, for two reasons: (1)
    it requires zero changes to seed_table's existing meaning or to any
    already-committed code that resolves it (scripts/build_seed_data.py,
    reconciliation_assembly.py, self_consistency.py all keep resolving
    seed_table exactly as they do today, freshness fixture or not); (2) it
    avoids a physical duplicate "_stale" file that would just be a copy of
    data seed_table's own "_a"/"_b" files already provide -- the "stale"
    snapshot for a freshness fixture is not new data, it is the data that
    was already going to be built for every other fixture anyway. The
    field is optional with no requirement to be populated on a
    non-freshness scenario (unlike declared_definition, which must always
    be explicitly set to None -- freshness applicability is a genuine
    per-scenario opt-in, not a value every scenario must always consider),
    so it carries a schema-level default of None rather than forcing every
    existing and future non-freshness fixture to write it out.

    Anticipated future use, not built this session: Build 2's planned
    freshness_attribution(seed_db_path_stale, seed_db_path_complete,
    query_sql) -> float (sibling to reconciliation.py's
    single_cause_attribution/shapley_pair_attribution) will take
    seed_db_path_stale from seed_table's existing per-side resolution and
    seed_db_path_complete from this field's -- nothing above blocks that
    signature.

    Calibration convention (decision 13, non-negotiable, carried forward
    to this new fixture shape): when a Build 2, Day 2+ freshness fixture
    is authored, its dollar figures must be derived from real execution
    against its real complete/stale snapshot pair from the moment it is
    authored -- never a hand-typed placeholder, even temporarily. The same
    standing rule that governs seed_table-backed fixtures applies
    identically to freshness_complete_seed_table-backed ones."""

    scenario_id: str
    description: str
    source_a: DashboardSource
    source_b: DashboardSource
    reported_value_a: float
    reported_value_b: float
    known_gap: float
    seed_table: str
    freshness_complete_seed_table: str | None = None
