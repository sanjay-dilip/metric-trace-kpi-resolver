"""Deterministic declared-vs-declared metric-definition diff. Compares two
DeclaredDefinition instances field by field, producing list[DefinitionDifference]
per src/schema.py. No LLM calls, no inference-from-SQL logic (that is Day 3
Part 2's job), no self-consistency checks (that is Day 4's job).

This is also where the value-level comparison Day 2's sql_diff._diff_filters
deliberately stayed out of actually happens: excluded_statuses is compared as
a set of values, not merely flagged as "filtered vs not filtered"."""

from src.schema import DefinitionDifference
from src.scenario import DeclaredDefinition


def diff_declared_definitions(
    def_a: DeclaredDefinition, def_b: DeclaredDefinition
) -> list[DefinitionDifference]:
    """Compare two declared metric definitions and return only the fields
    that actually differ — an empty list means the two declarations match on
    every field this tool checks.

    Both arguments must be present DeclaredDefinition instances. Callers must
    not pass None on either side: a source with no declared_definition is not
    this function's job (that is Day 3 Part 2's inferred-fallback path) and
    silently returning an empty list for a missing declaration would be
    indistinguishable from "the declarations matched," which is a dangerous
    ambiguity to leave unresolved. This function raises a clear TypeError
    instead of guessing.
    """
    if def_a is None or def_b is None:
        raise TypeError(
            "diff_declared_definitions requires both sides to have a declared "
            "definition; a None side must be routed to the Day 3 Part 2 "
            "inferred-fallback path instead of being passed here."
        )
    diffs = []
    if def_a.date_field != def_b.date_field:
        diffs.append(
            DefinitionDifference(
                field="date_field",
                source_a_value=def_a.date_field,
                source_b_value=def_b.date_field,
                source="declared",
                confidence="high",
            )
        )
    set_a, set_b = set(def_a.excluded_statuses), set(def_b.excluded_statuses)
    if set_a != set_b:
        diffs.append(
            DefinitionDifference(
                field="excluded_statuses",
                source_a_value=", ".join(sorted(set_a)) or "(none)",
                source_b_value=", ".join(sorted(set_b)) or "(none)",
                source="declared",
                confidence="high",
            )
        )
    if def_a.aggregation != def_b.aggregation:
        diffs.append(
            DefinitionDifference(
                field="aggregation",
                source_a_value=def_a.aggregation,
                source_b_value=def_b.aggregation,
                source="declared",
                confidence="high",
            )
        )
    return diffs
