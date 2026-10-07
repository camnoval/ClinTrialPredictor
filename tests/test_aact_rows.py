"""Tests for aact_rows. Plain asserts so tests/_run_stdlib.py works.

No transcribed counts: every check iterates the declarations or builds its own rows.
"""
from __future__ import annotations

from trial_pos.services.aact_aggregates import IDS_PARAM
from trial_pos.services.aact_fields import ROW_LEVEL_ONLY_TABLES, output_columns
from trial_pos.services.aact_provenance import (
    CARDINALITY_MANY, KEY_COLUMNS, PROVENANCES, TABLES, table_spec,
)
from trial_pos.services.aact_rows import (
    BYTE_ORDER, FILE_PREFIX, FOREIGN_KEYS, ROW_ID, TRIAL_ID, TRIAL_KEY, TRIAL_TABLES,
    UNCHECKED_REFERENCES, VOCAB_TABLES, VOCABULARY, ChunkError, ForeignKey, all_tables,
    check_chunk, chunk_sql, columns, count_sql, file_name, foreign_key_violations,
    keys_by_parent, orphan_sql, provenance, required_server_columns, vocab_sql,
)
from trial_pos.services.aact_snapshot import required_tables

_S = "ctgov"


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


def _row(table, **values):
    """A row in columns(table) order, every unnamed column None."""
    return tuple(values.get(c) for c in columns(table))


def test_every_trial_table_is_registered_and_exports_every_classified_column():
    registered = {s.table for s in TABLES}
    for t in TRIAL_TABLES:
        assert t in registered, t
        spec = table_spec(t)
        assert set(columns(t)) == set(TRIAL_KEY) | set(spec.columns), t
        assert columns(t)[:len(TRIAL_KEY)] == TRIAL_KEY, t


def test_every_exported_column_has_a_provenance_and_keys_are_reported_as_keys():
    for t in all_tables():
        for c in columns(t):
            p = provenance(t, c)
            if t in VOCAB_TABLES:
                assert p == VOCABULARY
            elif c in KEY_COLUMNS:
                assert p == "key"
            else:
                assert p in PROVENANCES, (t, c, p)


def test_tables_and_files_are_unique_and_prefixed():
    tables = all_tables()
    assert len(tables) == len(set(tables))
    names = [file_name(t) for t in tables]
    assert len(names) == len(set(names))
    assert all(n.startswith(FILE_PREFIX) for n in names)
    assert _raises(file_name, "not_a_table", exc=KeyError)


def test_trial_sql_is_scoped_ordered_and_never_joins():
    for t in TRIAL_TABLES:
        sql = chunk_sql(_S, t)
        assert f"= ANY(%({IDS_PARAM})s)" in sql, t
        assert sql.endswith(f"ORDER BY {TRIAL_ID} {BYTE_ORDER}, {ROW_ID}"), t
        assert " JOIN " not in sql.upper(), t
        assert f"FROM {_S}.{t} " in sql, t
        for c in columns(t):
            assert c in sql, (t, c)


def test_vocabulary_sql_is_ordered_by_id_and_unscoped():
    for t in VOCAB_TABLES:
        sql = vocab_sql(_S, t)
        assert sql.endswith(f"ORDER BY {ROW_ID}") and IDS_PARAM not in sql


def test_bad_identifiers_and_wrong_kinds_raise():
    assert _raises(chunk_sql, "bad schema", TRIAL_TABLES[0])
    assert _raises(chunk_sql, _S, next(iter(VOCAB_TABLES)), exc=KeyError)
    assert _raises(vocab_sql, _S, TRIAL_TABLES[0], exc=KeyError)
    assert _raises(count_sql, _S, next(iter(VOCAB_TABLES)), exc=KeyError)
    assert _raises(orphan_sql, _S, ForeignKey("a", "b", "c"), exc=KeyError)


def test_foreign_keys_point_between_exported_tables_and_parents_come_first():
    order = {t: i for i, t in enumerate(TRIAL_TABLES)}
    for fk in FOREIGN_KEYS:
        assert fk.child in order and fk.parent in order, fk
        assert order[fk.parent] < order[fk.child], fk
        assert fk.column in columns(fk.child), fk
    for child, column, parent in UNCHECKED_REFERENCES:
        assert child in order and column in columns(child)
        assert parent not in order, "an exported parent must be checked, not listed"


def test_every_id_shaped_column_of_an_exported_table_is_accounted_for():
    """A column ending in _id that refers to another row must be checked or listed."""
    covered = {(fk.child, fk.column) for fk in FOREIGN_KEYS} | \
              {(c, col) for c, col, _p in UNCHECKED_REFERENCES}
    not_references = {("documents", "document_id")}     # the document's own identifier
    for t in TRIAL_TABLES:
        for c in columns(t):
            if c.endswith("_" + ROW_ID) and c != TRIAL_ID and (t, c) not in not_references:
                assert (t, c) in covered, (t, c)


def test_orphan_sql_requires_the_same_trial():
    for fk in FOREIGN_KEYS:
        sql = orphan_sql(_S, fk)
        assert f"p.{TRIAL_ID} = c.{TRIAL_ID}" in sql and "IS NULL" in sql
        assert f"c.{fk.column}" in sql and f"= ANY(%({IDS_PARAM})s)" in sql


def test_check_chunk_returns_ids_by_trial():
    t = TRIAL_TABLES[0]
    rows = [_row(t, nct_id="NCT1", id=1), _row(t, nct_id="NCT1", id=2),
            _row(t, nct_id="NCT2", id=3)]
    assert check_chunk(t, rows, ["NCT1", "NCT2"]) == {"NCT1": {1, 2}, "NCT2": {3}}
    assert check_chunk(t, [], ["NCT1"]) == {}


def test_check_chunk_fails_closed():
    t = TRIAL_TABLES[0]
    outside = [_row(t, nct_id="NCT9", id=1)]
    twice = [_row(t, nct_id="NCT1", id=1), _row(t, nct_id="NCT2", id=1)]
    disorder = [_row(t, nct_id="NCT2", id=1), _row(t, nct_id="NCT1", id=2)]
    by_id = [_row(t, nct_id="NCT1", id=2), _row(t, nct_id="NCT1", id=1)]
    for rows in (outside, twice, disorder, by_id):
        assert _raises(check_chunk, t, rows, ["NCT1", "NCT2"], exc=ChunkError)


def test_foreign_key_violations_are_per_trial_and_null_is_a_violation():
    fk = FOREIGN_KEYS[0]
    parents = {"NCT1": {10}, "NCT2": {20}}
    ok = _row(fk.child, nct_id="NCT1", id=1, **{fk.column: 10})
    other_trial = _row(fk.child, nct_id="NCT1", id=2, **{fk.column: 20})
    null = _row(fk.child, nct_id="NCT2", id=3, **{fk.column: None})
    assert foreign_key_violations(fk, [ok], parents) == []
    assert foreign_key_violations(fk, [ok, other_trial, null], parents) == \
        [("NCT1", 2, 20), ("NCT2", 3, None)]


def test_keys_by_parent_groups_every_foreign_key_once():
    grouped = keys_by_parent()
    assert sorted(fk for fks in grouped.values() for fk in fks) == sorted(FOREIGN_KEYS)


def test_required_columns_cover_the_export_and_the_restore_check_covers_the_tables():
    need = required_server_columns()
    assert set(need) == set(all_tables())
    for t in all_tables():
        assert need[t] == set(columns(t))
    assert set(all_tables()) <= set(required_tables())


def test_row_level_only_tables_stay_out_of_the_fields_file():
    in_fields = {c.table for c in output_columns()}
    assert ROW_LEVEL_ONLY_TABLES <= set(TRIAL_TABLES)
    assert not (ROW_LEVEL_ONLY_TABLES & in_fields)


def test_many_per_trial_tables_dominate_and_the_one_per_trial_ones_are_row_level_only():
    for t in TRIAL_TABLES:
        if table_spec(t).cardinality != CARDINALITY_MANY:
            assert t in ROW_LEVEL_ONLY_TABLES, t
