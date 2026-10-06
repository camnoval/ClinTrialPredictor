"""The extra-fields pull, as data: which columns, which aggregates, which file, which SQL.

Every output column traces to `aact_provenance`: a plain column carries its registry
provenance, an aggregate carries the WORST provenance among the columns it reads. A column
with no provenance cannot be emitted, by construction rather than by review.

One-per-trial tables are LEFT JOINed directly; the probe found them exactly 1:1, and the
pull re-asserts that per chunk because the snapshot can change. Many-per-trial tables only
ever appear inside a GROUP BY subquery scoped to the chunk's ids (lesson 14).

Counts are DISTINCT over a content key, never raw rows: the probe found exact duplicate
rows in `interventions` and `facilities`. AACT's own `has_single_facility` counts rows,
which is why the raw row count is pulled beside the distinct one -- the gap is a finding.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.aact_aggregates import AGG_SEPARATOR, IDS_PARAM
from trial_pos.services.aact_provenance import (
    CARDINALITY_MANY, CARDINALITY_ONE, PROVENANCE_EDITABLE, PROVENANCE_POST_HOC,
    PROVENANCE_REGISTRATION, PROVENANCES, TABLES, provenance_for, table_spec,
)
from trial_pos.services.population import is_planned_date, parse_date

PARENT_TABLE = "studies"
FILE_FIELDS = "fields"
FILE_TEXT = "text"
OUTPUT_FILES = (FILE_FIELDS, FILE_TEXT)
KIND_COLUMN = "column"
KIND_DERIVED = "derived"
NAME_SEP = "__"

_SEVERITY = {PROVENANCE_REGISTRATION: 0, PROVENANCE_EDITABLE: 1, PROVENANCE_POST_HOC: 2}

# Empty on the server at probe time. Pulling them adds a column of blanks that reads like
# "unknown everywhere" -- the shape of the section 0.3 incident.
SKIP_COLUMNS = frozenset({
    ("design_outcomes", "population"),
    ("calculated_values", "nlm_download_date"),
    ("studies", "nlm_download_date_description"),
})

# Long sponsor-entered prose. Routed to the text file so the fields file stays small enough
# to load whole.
TEXT_COLUMNS = frozenset({
    ("studies", "brief_title"), ("studies", "official_title"),
    ("studies", "why_stopped"), ("studies", "baseline_population"),
    ("studies", "limitations_and_caveats"), ("studies", "biospec_description"),
    ("studies", "plan_to_share_ipd_description"), ("studies", "ipd_access_criteria"),
    ("studies", "ipd_time_frame"),
    ("designs", "masking_description"), ("designs", "intervention_model_description"),
    ("eligibilities", "criteria"), ("eligibilities", "population"),
    ("eligibilities", "gender_description"),
    ("brief_summaries", "description"),
})

# design_outcomes is pulled by its own script with per-outcome rows; here only counts.
LEAD_FLAG = "lead"
MESH_LIST = "mesh-list"
DRUG_TYPE = "DRUG"
SECONDARY_ID_SOURCE = "secondary_id"
EUDRACT_ID_TYPE = "EUDRACT_NUMBER"
# AACT's spelling of the country, as seen in facilities.country. If the audit finds zero
# US facilities the spelling has changed, which it reports rather than assumes.
US_COUNTRY_NAME = "United States"
# design_groups.group_type vocabulary at probe time. An unlisted value is counted under
# n_groups_other_type, not dropped.
GROUP_TYPES = ("EXPERIMENTAL", "ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR",
               "NO_INTERVENTION", "SHAM_COMPARATOR", "OTHER")
OUTCOME_TYPES = ("primary", "secondary", "other")
# A unit separator cannot occur in registry text, so content keys built with it cannot
# collide by concatenation.
KEY_SEP_SQL = "chr(31)"


class Derived(NamedTuple):
    table: str
    name: str
    expression: str
    sources: tuple
    file: str
    why: str


def _key(*columns: str) -> str:
    parts = ", ".join(f"coalesce({c}::text, '')" for c in columns)
    return f"concat_ws({KEY_SEP_SQL}, {parts})"


def _agg(expr: str, where: str = "") -> str:
    clause = f" FILTER (WHERE {where})" if where else ""
    return f"string_agg(DISTINCT {expr}, '{AGG_SEPARATOR}'){clause}"


def _count(expr: str, where: str = "") -> str:
    clause = f" FILTER (WHERE {where})" if where else ""
    return f"count(DISTINCT {expr}){clause}"


_IS_LEAD = f"lower(lead_or_collaborator) = '{LEAD_FLAG}'"
_NOT_LEAD = f"lower(lead_or_collaborator) <> '{LEAD_FLAG}'"

DERIVED: tuple = (
    Derived("sponsors", "n_lead_rows", f"count(*) FILTER (WHERE {_IS_LEAD})",
            ("lead_or_collaborator",), FILE_FIELDS,
            "the probe found exactly one lead per trial; asserted, not assumed"),
    Derived("sponsors", "lead_agency_class", _agg("agency_class", _IS_LEAD),
            ("agency_class", "lead_or_collaborator"), FILE_FIELDS,
            "independent of the label file's copy, which section 0.3 destroyed once"),
    Derived("sponsors", "n_collaborators", _count("name", _NOT_LEAD),
            ("name", "lead_or_collaborator"), FILE_FIELDS, "collaborator count"),
    Derived("sponsors", "collaborator_agency_classes", _agg("agency_class", _NOT_LEAD),
            ("agency_class", "lead_or_collaborator"), FILE_FIELDS, "collaborator classes"),
    Derived("conditions", "n_conditions", _count("downcase_name"),
            ("downcase_name",), FILE_FIELDS, "registered condition count"),
    Derived("conditions", "names", _agg("name"), ("name",), FILE_TEXT,
            "free text, so text file"),
    Derived("keywords", "n_keywords", _count("downcase_name"),
            ("downcase_name",), FILE_FIELDS, "keyword count"),
    Derived("keywords", "names", _agg("name"), ("name",), FILE_TEXT,
            "free text, so text file"),
    Derived("browse_conditions", "n_mesh_list", _count("downcase_mesh_term",
                                                       f"mesh_type = '{MESH_LIST}'"),
            ("downcase_mesh_term", "mesh_type"), FILE_FIELDS,
            "indexed terms only; ancestors would make every oncology trial 'Neoplasms'"),
    Derived("browse_conditions", "n_mesh_ancestor",
            _count("downcase_mesh_term", f"mesh_type <> '{MESH_LIST}'"),
            ("downcase_mesh_term", "mesh_type"), FILE_FIELDS, "breadth of the tree"),
    Derived("browse_interventions", "n_mesh_list",
            _count("downcase_mesh_term", f"mesh_type = '{MESH_LIST}'"),
            ("downcase_mesh_term", "mesh_type"), FILE_FIELDS, "indexed drug terms"),
    Derived("interventions", "n_rows", "count(*)", ("intervention_type",), FILE_FIELDS,
            "raw rows, beside the distinct count, so the duplicate gap is visible"),
    Derived("interventions", "n_distinct", _count(_key("intervention_type", "name")),
            ("intervention_type", "name"), FILE_FIELDS,
            "the probe found exact duplicate rows; this is the honest count"),
    Derived("interventions", "n_drug", _count("lower(name)",
                                              f"upper(intervention_type) = '{DRUG_TYPE}'"),
            ("intervention_type", "name"), FILE_FIELDS, "distinct drug interventions"),
    Derived("interventions", "types", _agg("intervention_type"), ("intervention_type",),
            FILE_FIELDS, "type set"),
    Derived("intervention_other_names", "n_distinct", _count("lower(name)"),
            ("name",), FILE_FIELDS, "synonyms and code names"),
    Derived("design_groups", "n_distinct", _count(_key("group_type", "title")),
            ("group_type", "title"), FILE_FIELDS, "arm count from the arm table"),
    *(Derived("design_groups", f"n_{g.lower()}",
              _count("title", f"upper(group_type) = '{g}'"),
              ("group_type", "title"), FILE_FIELDS, f"arms typed {g}")
      for g in GROUP_TYPES),
    Derived("design_groups", "n_type_absent", _count("title", "group_type IS NULL"),
            ("group_type", "title"), FILE_FIELDS, "untyped arms: unknown, not zero"),
    Derived("design_groups", "n_other_type",
            _count("title", "group_type IS NOT NULL AND upper(group_type) NOT IN ("
                   + ", ".join(f"'{g}'" for g in GROUP_TYPES) + ")"),
            ("group_type", "title"), FILE_FIELDS, "vocabulary drift, counted"),
    Derived("id_information", "n_secondary_ids",
            _count("id_value", f"id_source = '{SECONDARY_ID_SOURCE}'"),
            ("id_source", "id_value"), FILE_FIELDS, "secondary identifiers"),
    Derived("id_information", "n_eudract_ids",
            _count("id_value", f"upper(id_type) = '{EUDRACT_ID_TYPE}'"),
            ("id_type", "id_value"), FILE_FIELDS, "EU registration present"),
    Derived("countries", "n_current", _count("name", "NOT removed"),
            ("name", "removed"), FILE_FIELDS, "countries listed now"),
    Derived("countries", "n_removed", _count("name", "removed"),
            ("name", "removed"), FILE_FIELDS, "countries dropped during conduct"),
    Derived("countries", "names_current", _agg("name", "NOT removed"),
            ("name", "removed"), FILE_FIELDS, "short controlled vocabulary"),
    Derived("facilities", "n_rows", "count(*)", ("name",), FILE_FIELDS,
            "raw rows; AACT's has_single_facility is computed from these"),
    Derived("facilities", "n_distinct",
            _count(_key("name", "city", "state", "zip", "country")),
            ("name", "city", "state", "zip", "country"), FILE_FIELDS,
            "sites with exact duplicate rows collapsed"),
    Derived("facilities", "n_us_distinct",
            _count(_key("name", "city", "state", "zip", "country"),
                   f"country = '{US_COUNTRY_NAME}'"),
            ("name", "city", "state", "zip", "country"), FILE_FIELDS, "US sites"),
    Derived("facilities", "n_countries", _count("country"), ("country",), FILE_FIELDS,
            "countries from the site list, against the countries table"),
    *(Derived("design_outcomes", f"n_{t}",
              _count(_key("measure", "time_frame", "description"),
                     f"lower(outcome_type) = '{t}'"),
              ("outcome_type", "measure", "time_frame", "description"), FILE_FIELDS,
              f"registered {t} outcomes")
      for t in OUTCOME_TYPES),
)


def output_name(table: str, column: str) -> str:
    return f"{table}{NAME_SEP}{column}"


def worst_provenance(provenances: Iterable[str]) -> str:
    """The most leakage-prone of several provenances. Raises on an empty or unknown set."""
    values = list(provenances)
    if not values:
        raise ValueError("no provenance to combine")
    for v in values:
        if v not in PROVENANCES:
            raise ValueError(f"{v!r} is not one of {PROVENANCES}")
    return max(values, key=_SEVERITY.__getitem__)


def derived_provenance(item: Derived) -> str:
    return worst_provenance(provenance_for(item.table, c) for c in item.sources)


class OutputColumn(NamedTuple):
    name: str
    file: str
    kind: str
    table: str
    sources: tuple
    provenance: str
    note: str


def plain_columns() -> list:
    """(table, column) for every one-per-trial column the pull takes, registry order."""
    out = []
    for spec in TABLES:
        if spec.cardinality != CARDINALITY_ONE:
            continue
        for column in spec.columns:
            if (spec.table, column) not in SKIP_COLUMNS:
                out.append((spec.table, column))
    return out


def output_columns(include_text: bool = True) -> list:
    """Every emitted column with its file and provenance. nct_id is implicit in both."""
    out = []
    for table, column in plain_columns():
        file = FILE_TEXT if (table, column) in TEXT_COLUMNS else FILE_FIELDS
        out.append(OutputColumn(output_name(table, column), file, KIND_COLUMN, table,
                                (column,), provenance_for(table, column),
                                table_spec(table).notes.get(column, "")))
    for item in DERIVED:
        out.append(OutputColumn(output_name(item.table, item.name), item.file,
                                KIND_DERIVED, item.table, item.sources,
                                derived_provenance(item), item.why))
    if not include_text:
        out = [c for c in out if c.file != FILE_TEXT]
    return out


def required_server_columns() -> dict:
    """{table: set(columns)} the pull reads. The script fails closed if any is absent."""
    need: dict = {}
    for table, column in plain_columns():
        need.setdefault(table, set()).add(column)
    for item in DERIVED:
        need.setdefault(item.table, set()).update(item.sources)
    for table in need:
        need[table].add("nct_id")
    return need


def _alias(table: str) -> str:
    names = [spec.table for spec in TABLES]
    return f"t{names.index(table)}"


def chunk_sql(schema: str) -> str:
    """One query per chunk: studies, LEFT JOIN each one-per-trial table, LEFT JOIN each
    many-per-trial table ONLY through a GROUP BY subquery scoped to the chunk's ids."""
    ids = f"%({IDS_PARAM})s"
    base = _alias(PARENT_TABLE)
    select = [f"{base}.nct_id AS nct_id"]
    joins = []
    one_tables = []
    for table, column in plain_columns():
        if table not in one_tables:
            one_tables.append(table)
        select.append(f"{_alias(table)}.{column} AS {output_name(table, column)}")
    for table in one_tables:
        if table != PARENT_TABLE:
            a = _alias(table)
            joins.append(f"LEFT JOIN {schema}.{table} {a} ON {a}.nct_id = {base}.nct_id")
    many_tables = []
    for item in DERIVED:
        if item.table not in many_tables:
            many_tables.append(item.table)
    for table in many_tables:
        if table_spec(table).cardinality != CARDINALITY_MANY:
            raise ValueError(f"{table} is aggregated but not declared many-per-trial")
        a = _alias(table)
        exprs = [f"{d.expression} AS {d.name}" for d in DERIVED if d.table == table]
        joins.append(
            f"LEFT JOIN (SELECT nct_id, {', '.join(exprs)} FROM {schema}.{table} "
            f"WHERE nct_id = ANY({ids}) GROUP BY nct_id) {a} ON {a}.nct_id = {base}.nct_id")
        for d in DERIVED:
            if d.table == table:
                select.append(f"{a}.{d.name} AS {output_name(table, d.name)}")
    return (f"SELECT {', '.join(select)} FROM {schema}.{PARENT_TABLE} {base} "
            + " ".join(joins) + f" WHERE {base}.nct_id = ANY({ids})")


def csv_value(value) -> str:
    """Python value from psycopg2 -> CSV text. None is blank (unknown), never "None" and
    never "0"; booleans spell as the label file spells them."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


# ---- retrospective registration -----------------------------------------
def registration_lag_days(submitted, primary_completion) -> Optional[int]:
    """First-submitted minus primary-completion, in days. Positive = registered after."""
    s, p = parse_date(submitted), parse_date(primary_completion)
    if s is None or p is None:
        return None
    return (s - p).days


def registered_after_primary_completion(submitted, primary_completion,
                                        completion_type) -> Optional[bool]:
    """Was the trial first submitted AFTER its primary completion? Tri-state.

    False when submission is on or before the completion date, whatever its type. True
    when after, unless the type says the date is a PLAN: an estimated completion already
    past at submission is incoherent, so that case is unknown. An ABSENT type with a past
    date reads as after -- old records lack types, and a sponsor does not register a
    planned completion date that has already gone by.
    """
    lag = registration_lag_days(submitted, primary_completion)
    if lag is None:
        return None
    if lag <= 0:
        return False
    if is_planned_date(completion_type) is True:
        return None
    return True