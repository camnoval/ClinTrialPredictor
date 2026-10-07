"""The row-level AACT export, as data: which tables, which columns, which order, which keys.

WHY ROW LEVEL
=============
Every earlier pull reduces a many-per-trial table to one row per trial. That loses which
name belongs to which intervention and which intervention sits in which arm, so a trial's
TESTED agent cannot be told from its placebo or comparator. audit/probe_intervention_arms.py
measured it on the pinned restore: the arm links (`design_group_interventions`) exist,
are internally consistent, and are read by no pull. This export keeps every row of the
registration-side tables, keyed on the CHILD's own id, so a parent-to-child join is always
on the child's id and never multiplies anything.

WHAT IS IN IT
=============
`TRIAL_TABLES`: every registration-side many-per-trial table, plus `detailed_descriptions`
(one per trial, absent from the fields file) and `outcome_analysis_groups` (results-side,
a label input only). Columns are taken from `aact_provenance.TABLES`, so a column cannot be
exported without a provenance, and every column the registry classifies is exported.
`VOCAB_TABLES`: AACT's MeSH vocabulary, which has no trial key.

Not here, because another pull already writes them whole: the one-per-trial tables in the
fields file, and `design_outcomes` (pull_design_outcomes.py).

DETERMINISM
===========
Rows are ordered by `nct_id` in byte order, then by the child's integer id. Surrogate ids
are stable within one snapshot, and the snapshot is pinned (aact_snapshot.PINNED); they
must never be joined across snapshots (lesson 1).

FAILS CLOSED
============
`check_chunk` raises on a row outside the chunk, a repeated id, or rows out of order.
`foreign_key_violations` reports every child row whose parent id is not a row of the SAME
trial in the export, and the pull refuses to write if any exist. References to tables the
export does not carry are listed in `UNCHECKED_REFERENCES` rather than silently assumed.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple

from trial_pos.services.aact_aggregates import IDS_PARAM
from trial_pos.services.aact_provenance import KEY_COLUMNS, table_spec

ROW_ID = "id"
TRIAL_ID = "nct_id"
TRIAL_KEY = (TRIAL_ID, ROW_ID)
BYTE_ORDER = 'COLLATE "C"'

FILE_PREFIX = "rows__"
FILE_SUFFIX = ".csv"
MANIFEST_FILE = "rows.manifest.json"
PROVENANCE_FILE = "rows.provenance.csv"
VOCABULARY = "vocabulary"     # provenance word for a table that describes no trial

# Dependency order: a parent always precedes its children, so a chunk can be checked in one
# pass. The test asserts this against FOREIGN_KEYS.
TRIAL_TABLES = (
    "interventions",
    "intervention_other_names",
    "design_groups",
    "design_group_interventions",
    "browse_interventions",
    "browse_conditions",
    "conditions",
    "keywords",
    "countries",
    "facilities",
    "facility_investigators",
    "overall_officials",
    "sponsors",
    "id_information",
    "links",
    "documents",
    "provided_documents",
    "ipd_information_types",
    "study_references",
    "detailed_descriptions",
    "outcome_analysis_groups",
)

# AACT's MeSH vocabulary. No nct_id; exported whole. Columns as the pinned restore holds
# them (audit/probe_restore_inventory.py).
VOCAB_TABLES = {
    "mesh_terms": ("id", "qualifier", "tree_number", "description", "mesh_term",
                   "downcase_mesh_term"),
    "mesh_headings": ("id", "qualifier", "heading", "subcategory"),
}


class ForeignKey(NamedTuple):
    child: str
    column: str
    parent: str


FOREIGN_KEYS = (
    ForeignKey("intervention_other_names", "intervention_id", "interventions"),
    ForeignKey("design_group_interventions", "design_group_id", "design_groups"),
    ForeignKey("design_group_interventions", "intervention_id", "interventions"),
    ForeignKey("facility_investigators", "facility_id", "facilities"),
)

# References whose parent is not exported here. Named so their absence from the checks is
# a recorded fact, not an oversight.
UNCHECKED_REFERENCES = (
    ("outcome_analysis_groups", "outcome_analysis_id", "outcome_analyses"),
    ("outcome_analysis_groups", "result_group_id", "result_groups"),
)


class ChunkError(ValueError):
    """A chunk does not have the shape the export relies on. Nothing may be written."""


def trial_columns(table: str) -> tuple:
    """nct_id, id, then every column the registry classifies, in registry order."""
    spec = table_spec(table)
    return TRIAL_KEY + tuple(c for c in spec.columns if c not in KEY_COLUMNS)


def columns(table: str) -> tuple:
    if table in VOCAB_TABLES:
        return VOCAB_TABLES[table]
    if table in TRIAL_TABLES:
        return trial_columns(table)
    raise KeyError(f"{table} is not exported")


def all_tables() -> tuple:
    return TRIAL_TABLES + tuple(VOCAB_TABLES)


def file_name(table: str) -> str:
    columns(table)                      # raises for a table that is not exported
    return f"{FILE_PREFIX}{table}{FILE_SUFFIX}"


def provenance(table: str, column: str) -> str:
    """Registry provenance for a trial-table column; VOCABULARY for the MeSH tables; the
    key columns are reported as keys."""
    if table in VOCAB_TABLES:
        if column not in VOCAB_TABLES[table]:
            raise KeyError(f"{table}.{column} is not exported")
        return VOCABULARY
    if column in KEY_COLUMNS:
        return "key"
    return table_spec(table).columns[column]


def required_server_columns() -> dict:
    return {t: set(columns(t)) for t in all_tables()}


def _check_identifier(name: str) -> None:
    if not name.isidentifier():
        raise ValueError(f"bad SQL identifier {name!r}")


def chunk_sql(schema: str, table: str) -> str:
    """One trial table for one id block: no join, scoped to the block, byte-ordered."""
    _check_identifier(schema)
    if table not in TRIAL_TABLES:
        raise KeyError(f"{table} is not a trial table")
    cols = ", ".join(columns(table))
    return (f"SELECT {cols} FROM {schema}.{table} "
            f"WHERE {TRIAL_ID} = ANY(%({IDS_PARAM})s) "
            f"ORDER BY {TRIAL_ID} {BYTE_ORDER}, {ROW_ID}")


def vocab_sql(schema: str, table: str) -> str:
    _check_identifier(schema)
    if table not in VOCAB_TABLES:
        raise KeyError(f"{table} is not a vocabulary table")
    return f"SELECT {', '.join(columns(table))} FROM {schema}.{table} ORDER BY {ROW_ID}"


def count_sql(schema: str, table: str) -> str:
    """Rows and trials in the population, for --probe-only."""
    _check_identifier(schema)
    if table not in TRIAL_TABLES:
        raise KeyError(f"{table} is not a trial table")
    return (f"SELECT count(*), count(DISTINCT {TRIAL_ID}) FROM {schema}.{table} "
            f"WHERE {TRIAL_ID} = ANY(%({IDS_PARAM})s)")


def orphan_sql(schema: str, fk: ForeignKey) -> str:
    """Child rows in the population whose parent id is not a row of the SAME trial."""
    _check_identifier(schema)
    if fk not in FOREIGN_KEYS:
        raise KeyError(f"{fk} is not declared")
    return (f"SELECT count(*) FROM {schema}.{fk.child} c "
            f"LEFT JOIN {schema}.{fk.parent} p "
            f"ON p.{ROW_ID} = c.{fk.column} AND p.{TRIAL_ID} = c.{TRIAL_ID} "
            f"WHERE c.{TRIAL_ID} = ANY(%({IDS_PARAM})s) AND p.{ROW_ID} IS NULL")


def check_chunk(table: str, rows: list, block: Iterable[str]) -> dict:
    """Validate one table's rows for one id block; -> {nct_id: set(row ids)}.

    Raises ChunkError on a row whose trial is not in the block, a repeated row id, or rows
    not in (nct_id byte order, id) order. Rows are sequences in `columns(table)` order.
    """
    cols = columns(table)
    i_trial, i_id = cols.index(TRIAL_ID), cols.index(ROW_ID)
    allowed = set(block)
    seen: set = set()
    by_trial: dict = {}
    previous = None
    for row in rows:
        nct, rid = row[i_trial], row[i_id]
        if nct not in allowed:
            raise ChunkError(f"{table}: row {rid} belongs to {nct!r}, outside the chunk")
        if rid in seen:
            raise ChunkError(f"{table}: row id {rid} appears twice")
        key = (str(nct).encode("utf-8"), rid)
        if previous is not None and key < previous:
            raise ChunkError(f"{table}: rows are not in (nct_id, id) order at id {rid}")
        previous = key
        seen.add(rid)
        by_trial.setdefault(nct, set()).add(rid)
    return by_trial


def foreign_key_violations(fk: ForeignKey, child_rows: list, parent_ids: dict) -> list:
    """[(nct_id, child id, parent id)] for each child row whose parent id is not among the
    SAME trial's parent rows. A NULL reference is a violation too: the link is the point."""
    cols = columns(fk.child)
    i_trial, i_id, i_ref = cols.index(TRIAL_ID), cols.index(ROW_ID), cols.index(fk.column)
    out = []
    for row in child_rows:
        nct, ref = row[i_trial], row[i_ref]
        if ref is None or ref not in parent_ids.get(nct, ()):
            out.append((nct, row[i_id], ref))
    return out


def keys_by_parent() -> dict:
    """{child table: [ForeignKey, ...]} in declaration order."""
    out: dict = {}
    for fk in FOREIGN_KEYS:
        out.setdefault(fk.child, []).append(fk)
    return out
