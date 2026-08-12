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
    fixture-build step -- this field only names it."""

    scenario_id: str
    description: str
    source_a: DashboardSource
    source_b: DashboardSource
    reported_value_a: float
    reported_value_b: float
    known_gap: float
    seed_table: str
