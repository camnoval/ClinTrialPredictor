"""One-to-many AACT tables, aggregated in subqueries. Specs plus the SQL that builds them.

WHY THIS IS A MODULE AND NOT MORE STRING CONCATENATION IN THE PULL SCRIPT
========================================================================
Lesson 14: aggregate a one-to-many table in a subquery, never a plain join. Joining
`interventions` straight onto `studies` multiplies the studies rows -- a trial with four
interventions becomes four trials -- and inflates every count in the audit WITHOUT RAISING
AN ERROR. That is the worst failure shape available: the numbers stay plausible.

Until now that rule lived in a comment beside one hand-written subquery. The widened pull
adds five more sources (sponsors, responsible parties, intervention names, intervention
MeSH, condition MeSH), all one-to-many, and a rule enforced by a comment does not survive
six copies of the same pattern. So the sources are declared as DATA here and the SQL is
generated from the declaration, which lets a test assert the structural property directly:
every source aggregates, none joins bare, and the id list is always parameterised.

WHAT IS DELIBERATELY NOT HERE
=============================
No interpretation of the values. `agency_class` is carried as AACT spells it and
normalised elsewhere; indication and drug matching happen downstream. This module knows
about row multiplicity and nothing else.

SEPARATOR
=========
Aggregates are pipe-joined because a pipe does not occur in AACT's controlled vocabularies
and survives CSV quoting. Free-text columns (intervention names, MeSH terms) CAN contain
almost anything, so `AGG_SEPARATOR` is a named constant and the parser on the other side
must be told which fields are free text -- a name containing a pipe would otherwise split
into two drugs and quietly inflate resolution coverage.
"""
from __future__ import annotations

from typing import NamedTuple, Sequence

# Chosen over comma because commas are common in condition and intervention names.
AGG_SEPARATOR = "|"

# The bind parameter the pull passes its id block through. Named so the test can assert
# every generated subquery is scoped to it: an unscoped subquery would aggregate the whole
# 460k-row table for every chunk, which is slow rather than wrong, but on a 15-minute pull
# slow is its own failure.
IDS_PARAM = "ids"


class AggregateField(NamedTuple):
    """One output column and the SQL expression that produces it."""
    name: str
    expression: str


class AggregateSource(NamedTuple):
    """A one-to-many AACT table, reduced to one row per nct_id.

    `alias` is the SQL alias and must be unique. `required_columns` are the columns the
    schema probe has to find before this source can be used at all; when they are absent
    the pull disables the source with a printed warning rather than failing mid-run, the
    same way `intervention_types` already does. `free_text` marks sources whose values are
    sponsor-entered prose, so the reader knows the separator is not guaranteed safe.
    """
    alias: str
    table: str
    fields: tuple[AggregateField, ...]
    required_columns: tuple[str, ...]
    free_text: bool
    why: str


def _agg(column: str, where: str = "") -> str:
    """DISTINCT pipe-joined aggregate of one column, optionally filtered.

    DISTINCT because AACT repeats values across rows (the same agency class for several
    collaborators, the same MeSH term at two branch depths) and an un-deduplicated
    aggregate would make a trial look like it had more sponsors than it does.
    """
    filter_clause = f" FILTER (WHERE {where})" if where else ""
    return (f"string_agg(DISTINCT {column}, '{AGG_SEPARATOR}'){filter_clause}")


# AACT's sponsors.lead_or_collaborator value for the lead sponsor, lowercased before
# comparison because the column's casing has changed across registry revisions.
LEAD_SPONSOR_FLAG = "lead"

AGGREGATE_SOURCES: tuple[AggregateSource, ...] = (
    AggregateSource(
        alias="iv",
        table="interventions",
        fields=(
            AggregateField("intervention_types", _agg("intervention_type")),
            # Free text with dosing noise ("XYZ 10 mg QD", "Placebo"). Pulled anyway
            # because it is the broadest drug-name source; the other two are cleaner and
            # narrower, and resolution rate is reported per source rather than pooled.
            AggregateField("intervention_names", _agg("name")),
        ),
        required_columns=("nct_id", "intervention_type", "name"),
        free_text=True,
        why="drug resolution (§8.1c) and the scoping signal is_drug_trial (§5)",
    ),
    AggregateSource(
        alias="ivo",
        table="intervention_other_names",
        fields=(AggregateField("intervention_other_names", _agg("name")),),
        required_columns=("nct_id", "name"),
        free_text=True,
        why="drug synonyms and internal code names, which often resolve where the "
            "primary name does not",
    ),
    AggregateSource(
        alias="bi",
        table="browse_interventions",
        fields=(AggregateField("intervention_mesh_terms", _agg("mesh_term")),),
        required_columns=("nct_id", "mesh_term"),
        free_text=False,
        why="curated but coarse drug vocabulary; the third resolution source",
    ),
    AggregateSource(
        alias="bc",
        table="browse_conditions",
        fields=(
            # mesh_type separates the condition AACT actually indexed from its ancestors
            # in the MeSH tree. Pooling them would make every oncology trial look like it
            # studied "Neoplasms", which is useless for indication matching.
            AggregateField(
                "condition_mesh_terms",
                _agg("mesh_term", "lower(mesh_type) = 'mesh-list'")),
            AggregateField(
                "condition_mesh_ancestors",
                _agg("mesh_term", "lower(mesh_type) <> 'mesh-list'")),
        ),
        required_columns=("nct_id", "mesh_term", "mesh_type"),
        free_text=False,
        why="the MeSH side of the code-to-code indication join (§1.2.1)",
    ),
    AggregateSource(
        alias="sp",
        table="sponsors",
        fields=(
            AggregateField(
                "lead_sponsor_class",
                _agg("agency_class",
                     f"lower(lead_or_collaborator) = '{LEAD_SPONSOR_FLAG}'")),
            AggregateField(
                "collaborator_classes",
                _agg("agency_class",
                     f"lower(lead_or_collaborator) <> '{LEAD_SPONSOR_FLAG}'")),
            AggregateField(
                "lead_sponsor_name",
                _agg("name", f"lower(lead_or_collaborator) = '{LEAD_SPONSOR_FLAG}'")),
        ),
        required_columns=("nct_id", "agency_class", "lead_or_collaborator", "name"),
        free_text=True,
        why="posting-rate-by-sponsor-class, which §8.2 requires and no existing column "
            "supports",
    ),
    AggregateSource(
        alias="rp",
        table="responsible_parties",
        fields=(
            AggregateField("responsible_party_type", _agg("responsible_party_type")),
            AggregateField("responsible_party_affiliation", _agg("affiliation")),
        ),
        required_columns=("nct_id", "responsible_party_type", "affiliation"),
        free_text=True,
        why="the FDAAA obligation falls on the RESPONSIBLE PARTY, not always the lead "
            "sponsor. Carried beside the sponsor class so the disagreement between them "
            "can be measured rather than assumed away",
    ),
)

# Every column these sources contribute, in declaration order. The pull writes the drug
# and condition aggregates to a SEPARATE entity file rather than into the label CSV, so
# the label file's schema stays stable for §8.2 while the market work iterates.
ENTITY_FIELDS = ("intervention_names", "intervention_other_names",
                 "intervention_mesh_terms", "condition_mesh_terms",
                 "condition_mesh_ancestors", "lead_sponsor_name",
                 "responsible_party_affiliation")


def source_by_alias(alias: str) -> AggregateSource:
    for source in AGGREGATE_SOURCES:
        if source.alias == alias:
            return source
    raise KeyError(alias)


def available_sources(found_columns: dict) -> tuple[tuple[AggregateSource, ...],
                                                    tuple[tuple[str, tuple[str, ...]], ...]]:
    """Split the declared sources into usable and unavailable, given a schema probe.

    `found_columns` maps table name -> set of column names, as `probe_schema` returns it.
    A source whose table or columns are missing is DISABLED rather than fatal: the pull
    prints the gap and the dependent fields read unknown everywhere, which is weaker but
    honest. Returns (usable, [(alias, missing_columns), ...]).
    """
    usable, unavailable = [], []
    for source in AGGREGATE_SOURCES:
        present = found_columns.get(source.table)
        # `probe_schema` pre-seeds every table with an EMPTY set rather than omitting it,
        # so "not visible" arrives as falsy rather than as a missing key. Both are the
        # same condition and both must report the table, not a list of every column.
        if not present:
            unavailable.append((source.alias, ("<table absent>",)))
            continue
        missing = tuple(c for c in source.required_columns if c not in present)
        if missing:
            unavailable.append((source.alias, missing))
        else:
            usable.append(source)
    return tuple(usable), tuple(unavailable)


def aggregate_field_names(sources: Sequence[AggregateSource]) -> tuple[str, ...]:
    return tuple(field.name for source in sources for field in source.fields)


def build_aggregate_sql(schema: str, sources: Sequence[AggregateSource],
                        join_to: str = "s") -> tuple[str, str]:
    """(select_fragment, join_fragment) for the given sources.

    Each source becomes ONE subquery aggregated to nct_id and LEFT JOINed on that natural
    key. LEFT so a trial with no rows in the table still appears -- an inner join would
    silently drop, for instance, every trial with no recorded sponsor, and a population
    defined by query must not shrink because of a missing attribute.

    The subquery carries `WHERE nct_id = ANY(%(ids)s)` so it is scoped to the same id block
    as the outer query rather than aggregating the whole table once per chunk.
    """
    selects, joins = [], []
    for source in sources:
        exprs = ", ".join(f"{field.expression} AS {field.name}"
                          for field in source.fields)
        selects.extend(f"{source.alias}.{field.name} AS {field.name}"
                       for field in source.fields)
        joins.append(
            f" LEFT JOIN (SELECT nct_id, {exprs} FROM {schema}.{source.table} "
            f"WHERE nct_id = ANY(%({IDS_PARAM})s) GROUP BY nct_id) {source.alias} "
            f"ON {source.alias}.nct_id = {join_to}.nct_id"
        )
    return (", ".join(selects), "".join(joins))