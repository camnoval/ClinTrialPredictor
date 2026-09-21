"""Tests for the AACT one-to-many aggregate specs. Plain asserts for tests/_run_stdlib.py.

These are mostly STRUCTURAL tests over the declaration, not example-based tests of one
query. That is deliberate: the module exists so lesson 14 -- aggregate a one-to-many table
in a subquery, never a plain join -- is enforced for every source including ones added
later, rather than being re-checked by hand each time. A test that only covered the six
current sources would pass while the seventh silently multiplied the studies rows.

No transcribed expected SQL. Expectations are derived from the declaration or from
structural properties of the generated string.
"""
from __future__ import annotations

import re

from trial_pos.services.aact_aggregates import (
    AGGREGATE_SOURCES, AGG_SEPARATOR, ENTITY_FIELDS, IDS_PARAM, LEAD_SPONSOR_FLAG,
    aggregate_field_names, available_sources, build_aggregate_sql, source_by_alias,
)

SCHEMA = "ctgov"


def _sql_for_all():
    return build_aggregate_sql(SCHEMA, AGGREGATE_SOURCES)


# ---- the declaration itself ------------------------------------------------
def test_aliases_are_unique():
    # a duplicated alias produces SQL that either errors or, worse, resolves to the wrong
    # subquery and returns another table's values under this one's column name
    aliases = [s.alias for s in AGGREGATE_SOURCES]
    assert len(aliases) == len(set(aliases))


def test_output_field_names_are_unique_across_sources():
    names = aggregate_field_names(AGGREGATE_SOURCES)
    assert len(names) == len(set(names))


def test_every_source_declares_nct_id_as_required():
    # the aggregate key. A source that does not require it cannot be grouped to one row
    # per trial, which is the entire purpose of the module.
    for source in AGGREGATE_SOURCES:
        assert "nct_id" in source.required_columns, source.alias


def test_every_required_column_beyond_the_key_is_actually_used():
    # a required column nobody reads would disable a source for no reason, and the
    # disable path is silent-by-design (a printed warning, not a failure)
    for source in AGGREGATE_SOURCES:
        expressions = " ".join(f.expression for f in source.fields)
        for column in source.required_columns:
            if column == "nct_id":
                continue
            assert column in expressions, (source.alias, column)


def test_every_source_states_why_it_exists():
    for source in AGGREGATE_SOURCES:
        assert source.why.strip(), source.alias


def test_entity_fields_are_all_real_declared_fields():
    declared = set(aggregate_field_names(AGGREGATE_SOURCES))
    assert set(ENTITY_FIELDS) <= declared


def test_free_text_flag_is_set_where_values_are_sponsor_entered():
    # the separator is only safe for controlled vocabularies. Sources reading `name` or
    # `affiliation` are prose and must be flagged so the reader knows a value containing
    # a pipe would split into two.
    for source in AGGREGATE_SOURCES:
        reads_prose = any(("name" in f.expression or "affiliation" in f.expression)
                          for f in source.fields)
        if reads_prose:
            assert source.free_text, source.alias


# ---- lesson 14, enforced ---------------------------------------------------
def test_every_source_aggregates_in_a_subquery():
    _, joins = _sql_for_all()
    for source in AGGREGATE_SOURCES:
        fragment = f"FROM {SCHEMA}.{source.table} "
        assert fragment in joins, source.table
        # the table name must appear INSIDE a subquery that groups, never as a bare join
        before = joins.split(fragment)[0]
        assert before.rsplit("LEFT JOIN", 1)[-1].lstrip().startswith("(SELECT nct_id"), \
            source.table


def test_no_source_is_joined_without_grouping():
    # the failure this guards is silent: a bare join multiplies studies rows and inflates
    # every count in the audit without erroring
    _, joins = _sql_for_all()
    assert joins.count("GROUP BY nct_id") == len(AGGREGATE_SOURCES)
    assert joins.count("LEFT JOIN") == len(AGGREGATE_SOURCES)


def test_every_join_is_a_left_join():
    # an inner join would drop trials that simply have no row in the side table, shrinking
    # a population that is defined by query
    _, joins = _sql_for_all()
    assert "LEFT JOIN" in joins
    assert joins.count("JOIN") == joins.count("LEFT JOIN")


def test_every_subquery_is_scoped_to_the_id_block():
    _, joins = _sql_for_all()
    scoped = f"WHERE nct_id = ANY(%({IDS_PARAM})s)"
    assert joins.count(scoped) == len(AGGREGATE_SOURCES)


def test_ids_are_parameterised_and_never_interpolated():
    # the ids must reach Postgres as a bind parameter; a formatted list would be an
    # injection surface and would also defeat the query plan cache.
    # Matches an NCT IDENTIFIER (letters then digits) rather than the letters alone --
    # the first version of this test asserted "NCT" was absent and failed on the word
    # DISTINCT, which ends in those three letters.
    _, joins = _sql_for_all()
    assert re.search(r"NCT\d", joins) is None
    assert f"%({IDS_PARAM})s" in joins


def test_every_aggregate_is_distinct():
    # AACT repeats values across rows (one agency class per collaborator, one MeSH term per
    # branch depth); without DISTINCT a trial looks like it has more sponsors than it does
    for source in AGGREGATE_SOURCES:
        for field in source.fields:
            assert "string_agg(DISTINCT" in field.expression, (source.alias, field.name)


def test_every_aggregate_uses_the_named_separator():
    for source in AGGREGATE_SOURCES:
        for field in source.fields:
            assert f"'{AGG_SEPARATOR}'" in field.expression, (source.alias, field.name)


def test_select_fragment_exposes_every_declared_field_once():
    selects, _ = _sql_for_all()
    for name in aggregate_field_names(AGGREGATE_SOURCES):
        assert selects.count(f"AS {name}") == 1, name


def test_join_target_is_configurable_and_used():
    _, joins = build_aggregate_sql(SCHEMA, AGGREGATE_SOURCES, join_to="st")
    assert ".nct_id = st.nct_id" in joins
    assert ".nct_id = s.nct_id" not in joins


# ---- sponsor split --------------------------------------------------------
def test_sponsor_lead_and_collaborator_are_complementary_filters():
    # the two must partition the sponsor rows: if both used '=', collaborators would
    # vanish; if both used '<>', the lead would
    sponsors = source_by_alias("sp")
    lead = next(f for f in sponsors.fields if f.name == "lead_sponsor_class")
    collab = next(f for f in sponsors.fields if f.name == "collaborator_classes")
    assert f"= '{LEAD_SPONSOR_FLAG}'" in lead.expression
    assert f"<> '{LEAD_SPONSOR_FLAG}'" in collab.expression


def test_sponsor_filter_is_case_insensitive():
    # the column's casing has changed across registry revisions, so a bare equality on
    # 'lead' would silently return nothing for one of the spellings
    sponsors = source_by_alias("sp")
    for field in sponsors.fields:
        if "lead_or_collaborator" in field.expression:
            assert "lower(lead_or_collaborator)" in field.expression, field.name


def test_condition_mesh_separates_indexed_terms_from_ancestors():
    # pooling them would make every oncology trial look like it studied "Neoplasms"
    conditions = source_by_alias("bc")
    indexed = next(f for f in conditions.fields if f.name == "condition_mesh_terms")
    ancestors = next(f for f in conditions.fields
                     if f.name == "condition_mesh_ancestors")
    assert "= 'mesh-list'" in indexed.expression
    assert "<> 'mesh-list'" in ancestors.expression


# ---- availability / graceful disable --------------------------------------
def _full_schema():
    return {s.table: set(s.required_columns) for s in AGGREGATE_SOURCES}


def test_all_sources_usable_when_the_schema_is_complete():
    usable, unavailable = available_sources(_full_schema())
    assert len(usable) == len(AGGREGATE_SOURCES)
    assert unavailable == ()


def test_absent_table_disables_only_its_own_source():
    schema = _full_schema()
    dropped = AGGREGATE_SOURCES[0]
    del schema[dropped.table]
    usable, unavailable = available_sources(schema)
    assert len(usable) == len(AGGREGATE_SOURCES) - 1
    assert [alias for alias, _ in unavailable] == [dropped.alias]


def test_missing_column_disables_the_source_and_names_the_column():
    source = source_by_alias("sp")
    missing = next(c for c in source.required_columns if c != "nct_id")
    schema = _full_schema()
    schema[source.table] = schema[source.table] - {missing}
    usable, unavailable = available_sources(schema)
    assert source.alias not in [s.alias for s in usable]
    assert dict(unavailable)[source.alias] == (missing,)


def test_sql_for_a_disabled_source_is_simply_absent():
    # the disable path must produce NO fragment rather than an empty one, or the generated
    # SQL is malformed and the pull fails at the point it should have degraded
    source = source_by_alias("bc")
    remaining = tuple(s for s in AGGREGATE_SOURCES if s.alias != source.alias)
    selects, joins = build_aggregate_sql(SCHEMA, remaining)
    assert source.table not in joins
    for field in source.fields:
        assert field.name not in selects


def test_no_sources_produces_empty_fragments_not_broken_sql():
    selects, joins = build_aggregate_sql(SCHEMA, ())
    assert selects == ""
    assert joins == ""