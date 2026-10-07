"""Tests for aact_fields. Plain asserts so tests/_run_stdlib.py works.

Structural: every check iterates the declarations or uses their own constants.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from trial_pos.services.aact_aggregates import IDS_PARAM
from trial_pos.services.aact_fields import (
    DERIVED, FILE_FIELDS, FILE_TEXT, GROUP_TYPES, KIND_DERIVED, OUTPUT_FILES,
    OUTCOME_TYPES, PARENT_TABLE, SKIP_COLUMNS, TEXT_COLUMNS, chunk_sql, csv_value,
    derived_provenance, output_columns, plain_columns, registered_after_primary_completion,
    registration_lag_days, required_server_columns, worst_provenance,
)
from trial_pos.services.aact_provenance import (
    CARDINALITY_MANY, CARDINALITY_ONE, PROVENANCES, PROVENANCE_EDITABLE,
    PROVENANCE_POST_HOC, PROVENANCE_REGISTRATION, TABLES, provenance_for, table_spec,
)

SCHEMA = "ctgov"


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def test_every_output_column_has_a_provenance_and_a_file():
    for column in output_columns():
        assert column.provenance in PROVENANCES, column.name
        assert column.file in OUTPUT_FILES, column.name


def test_output_names_are_unique_and_never_shadow_the_key():
    names = [c.name for c in output_columns()]
    assert len(names) == len(set(names))
    assert "nct_id" not in names


def test_plain_columns_come_only_from_one_per_trial_tables_and_skip_the_empties():
    taken = set(plain_columns())
    for table, column in taken:
        assert table_spec(table).cardinality == CARDINALITY_ONE, table
    assert not (taken & SKIP_COLUMNS)
    for table, column in SKIP_COLUMNS:
        assert column in table_spec(table).columns, (table, column)


def test_text_columns_are_classified_one_per_trial_columns_and_land_in_the_text_file():
    by_name = {c.name: c for c in output_columns()}
    for table, column in TEXT_COLUMNS:
        assert table_spec(table).cardinality == CARDINALITY_ONE
        assert by_name[f"{table}__{column}"].file == FILE_TEXT


def test_dropping_text_removes_exactly_the_text_file_columns():
    full = output_columns(include_text=True)
    lean = output_columns(include_text=False)
    assert [c for c in full if c.file != FILE_TEXT] == lean


def test_every_derived_reads_classified_columns_of_a_many_per_trial_table():
    for item in DERIVED:
        assert table_spec(item.table).cardinality == CARDINALITY_MANY, item.table
        for source in item.sources:
            provenance_for(item.table, source)


def test_a_derived_column_takes_the_worst_provenance_it_reads():
    for item in DERIVED:
        sources = [provenance_for(item.table, c) for c in item.sources]
        assert derived_provenance(item) == worst_provenance(sources)
    assert worst_provenance([PROVENANCE_REGISTRATION, PROVENANCE_EDITABLE]) \
        == PROVENANCE_EDITABLE
    assert worst_provenance([PROVENANCE_EDITABLE, PROVENANCE_POST_HOC,
                             PROVENANCE_REGISTRATION]) == PROVENANCE_POST_HOC
    assert _raises(worst_provenance, [])
    assert _raises(worst_provenance, ["vibes"])


def test_facility_counts_are_editable_never_registration():
    by_name = {c.name: c for c in output_columns()}
    for name, column in by_name.items():
        if column.kind == KIND_DERIVED and column.table == "facilities":
            assert column.provenance == PROVENANCE_EDITABLE, name


def test_required_columns_cover_every_column_read_and_carry_the_key():
    need = required_server_columns()
    for table, column in plain_columns():
        assert column in need[table]
    for item in DERIVED:
        assert set(item.sources) <= need[item.table]
    for table, columns in need.items():
        assert "nct_id" in columns, table


def test_group_and_outcome_vocabularies_each_get_a_column():
    names = {(d.table, d.name) for d in DERIVED}
    for g in GROUP_TYPES:
        assert ("design_groups", f"n_{g.lower()}") in names
    for t in OUTCOME_TYPES:
        assert ("design_outcomes", f"n_{t}") in names


# ---- the SQL ----------------------------------------------------------------
def test_every_many_table_appears_only_inside_a_scoped_group_by_subquery():
    sql = chunk_sql(SCHEMA)
    many = {item.table for item in DERIVED}
    for table in many:
        bare = re.findall(rf"JOIN {SCHEMA}\.{table}\b", sql)
        assert not bare, f"{table} joined bare"
        sub = re.search(rf"FROM {SCHEMA}\.{table} WHERE nct_id = ANY\(%\({IDS_PARAM}\)s\)"
                        rf" GROUP BY nct_id", sql)
        assert sub, f"{table} subquery not scoped and grouped"


def test_every_one_table_is_left_joined_on_nct_id_and_the_parent_is_scoped():
    sql = chunk_sql(SCHEMA)
    for spec in TABLES:
        if spec.cardinality == CARDINALITY_ONE and spec.table != PARENT_TABLE:
            assert re.search(rf"LEFT JOIN {SCHEMA}\.{spec.table} t\d+ ON t\d+\.nct_id",
                             sql), spec.table
    # the parent is scoped to the chunk, and rows come back in nct_id order
    assert re.search(rf"WHERE (t\d+)\.nct_id = ANY\(%\({IDS_PARAM}\)s\) "
                     rf"ORDER BY \1\.nct_id$", sql.rstrip()), sql[-120:]


def test_every_output_column_is_selected_exactly_once():
    sql = chunk_sql(SCHEMA)
    for column in output_columns():
        assert len(re.findall(rf" AS {column.name}\b", sql)) == 1, column.name


def test_raw_and_distinct_counts_are_both_pulled_where_duplicates_were_found():
    names = {(d.table, d.name) for d in DERIVED}
    for table in ("interventions", "facilities"):
        assert (table, "n_rows") in names and (table, "n_distinct") in names


# ---- values -------------------------------------------------------------------
def test_csv_values_keep_unknown_distinct_from_false_and_zero():
    assert csv_value(None) == ""
    assert csv_value(False) == "False" and csv_value(True) == "True"
    assert csv_value(0) == "0"
    assert csv_value(date(2020, 1, 2)) == "2020-01-02"
    assert csv_value(datetime(2020, 1, 2, 3, 4)) == "2020-01-02T03:04:00"
    assert csv_value(Decimal("1.50")) == "1.50"


# ---- retrospective registration ---------------------------------------------
_D = date(2015, 6, 1)
_DAY = timedelta(days=1)


def test_lag_sign_and_missing_dates():
    assert registration_lag_days(_D + _DAY, _D) == 1
    assert registration_lag_days(_D - _DAY, _D) == -1
    assert registration_lag_days(None, _D) is None
    assert registration_lag_days(_D, "") is None


def test_on_or_before_completion_is_prospective_whatever_the_type():
    for completion_type in ("ACTUAL", "ESTIMATED", None):
        assert registered_after_primary_completion(_D, _D, completion_type) is False
        assert registered_after_primary_completion(_D - _DAY, _D, completion_type) is False


def test_after_an_actual_or_untyped_completion_is_retrospective():
    assert registered_after_primary_completion(_D + _DAY, _D, "ACTUAL") is True
    assert registered_after_primary_completion(_D + _DAY, _D, None) is True


def test_after_an_estimated_completion_is_unknown_not_retrospective():
    assert registered_after_primary_completion(_D + _DAY, _D, "ESTIMATED") is None


def test_missing_dates_are_unknown():
    assert registered_after_primary_completion(None, _D, "ACTUAL") is None
    assert registered_after_primary_completion(_D, None, "ACTUAL") is None


def test_every_string_agg_is_ordered_in_byte_order():
    # without ORDER BY the aggregate's order is unspecified, and under the database's
    # default collation it differs between AACT's server and a local restore
    from trial_pos.services.aact_aggregates import AGG_COLLATION
    sql = chunk_sql(SCHEMA)
    c = re.escape(f"COLLATE {AGG_COLLATION}")
    aggs = re.findall(rf"string_agg\(DISTINCT (.+? {c}), '\|' ORDER BY (.+? {c})\)", sql)
    assert aggs and len(aggs) == sql.count("string_agg(")
    for arg, order in aggs:
        assert arg == order and arg.endswith(f"COLLATE {AGG_COLLATION}"), (arg, order)
