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
