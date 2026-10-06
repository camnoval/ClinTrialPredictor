"""Tests for aact_provenance. Plain asserts so tests/_run_stdlib.py works.

No transcribed counts: every check iterates the registry or uses its own constants.
"""
from __future__ import annotations

from trial_pos.services.aact_aggregates import AGGREGATE_SOURCES
from trial_pos.services.aact_provenance import (
    CARDINALITIES, CARDINALITY_MANY, CARDINALITY_ONE, KEY_COLUMNS, PROVENANCES,
    PROVENANCE_DOC, PROVENANCE_EDITABLE, PROVENANCE_POST_HOC, PROVENANCE_REGISTRATION,
    TABLES, VERDICT_CONSISTENT, VERDICT_CONTRADICTED, cardinality_verdict,
    columns_by_provenance, label_derived_violations, missing_columns, provenance_for,
    table_spec, unclassified_columns,
)
from trial_pos.services.endpoint_label import LABEL_DERIVED_FIELDS


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


def test_every_provenance_is_in_the_vocabulary_and_documented():
    assert set(PROVENANCE_DOC) == set(PROVENANCES)
    for spec in TABLES:
        assert spec.cardinality in CARDINALITIES, spec.table
        for column, value in spec.columns.items():
            assert value in PROVENANCES, (spec.table, column)


def test_notes_only_annotate_classified_columns():
    for spec in TABLES:
        assert set(spec.notes) <= set(spec.columns), spec.table


def test_tables_are_declared_once_and_keys_are_never_classified():
    names = [spec.table for spec in TABLES]
    assert len(names) == len(set(names))
    for spec in TABLES:
        assert not (set(spec.columns) & KEY_COLUMNS), spec.table


def test_the_named_leakage_fields_are_post_hoc():
    # rev 8 section 14 item 4: duration encodes a futility stop. Enrollment has the same
    # shape: overwritten with the actual count, and an early stop enrolls fewer.
    assert provenance_for("calculated_values", "actual_duration") == PROVENANCE_POST_HOC
    assert provenance_for("studies", "enrollment") == PROVENANCE_POST_HOC


def test_no_label_input_is_classified_as_anything_but_post_hoc():
    assert label_derived_violations() == []


def test_label_inputs_present_in_the_registry_are_actually_checked():
    # guards the test above against passing vacuously
    classified = {c for spec in TABLES for c in spec.columns}
    assert classified & set(LABEL_DERIVED_FIELDS)


def test_every_aggregate_source_table_is_classified():
    # the existing pull's one-to-many sources must not be the unclassified ones.
    # responsible_parties is aggregated defensively by the pull but DECLARED one-per-trial
    # here, so the probe can contradict that expectation if it is wrong.
    declared = {spec.table for spec in TABLES}
    for source in AGGREGATE_SOURCES:
        assert source.table in declared, source.table
        assert table_spec(source.table).cardinality == CARDINALITY_MANY \
            or source.table == "responsible_parties", source.table


def test_an_unclassified_column_raises_rather_than_defaulting():
    assert _raises(provenance_for, "designs", "not_a_real_column")
    assert _raises(provenance_for, "not_a_real_table", "x", exc=KeyError)


def test_unclassified_and_missing_are_complementary():
    spec = TABLES[0]
    known = sorted(spec.columns)
    found = known[1:] + ["nct_id", "id", "brand_new_column"]
    assert unclassified_columns(spec.table, found) == ["brand_new_column"]
    assert missing_columns(spec.table, found) == known[:1]


def test_cardinality_one_is_contradicted_by_any_repeat():
    assert cardinality_verdict(CARDINALITY_ONE, 5, 5) == VERDICT_CONSISTENT
    assert cardinality_verdict(CARDINALITY_ONE, 6, 5) == VERDICT_CONTRADICTED


def test_cardinality_many_permits_but_does_not_require_repeats():
    assert cardinality_verdict(CARDINALITY_MANY, 5, 5) == VERDICT_CONSISTENT
    assert cardinality_verdict(CARDINALITY_MANY, 9, 5) == VERDICT_CONSISTENT


def test_an_empty_table_has_no_verdict_and_impossible_counts_raise():
    for cardinality in CARDINALITIES:
        assert cardinality_verdict(cardinality, 0, 0) is None
        assert _raises(cardinality_verdict, cardinality, 3, 4)
    assert _raises(cardinality_verdict, "sometimes", 1, 1)


def test_columns_by_provenance_partitions_the_registry():
    total = sum(len(spec.columns) for spec in TABLES)
    parts = [columns_by_provenance(p) for p in PROVENANCES]
    assert sum(len(p) for p in parts) == total
    assert set(columns_by_provenance(PROVENANCE_REGISTRATION)).isdisjoint(
        columns_by_provenance(PROVENANCE_EDITABLE))
    assert _raises(columns_by_provenance, "sort_of")