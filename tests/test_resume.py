"""Tests for the resume guards.

No expected value is transcribed. Assertions derive from MANIFEST_FIELDS itself, from
structural properties (a conflict list is empty exactly when the manifests agree; the
remaining ids partition the input), or from a constructed round-trip.
"""
import json

from trial_pos.services.resume import (
    MANIFEST_FIELDS, MANIFEST_FIELD_DOC, build_manifest, describe_conflicts,
    header_problems, ids_remaining, manifest_conflicts,
)


def _settings(**overrides):
    """A complete settings dict built FROM the field list, so adding a manifest field
    makes these tests exercise it rather than silently ignore it."""
    base = {f: i for i, f in enumerate(MANIFEST_FIELDS)}
    base.update(overrides)
    return base


# ---- manifest construction ----------------------------------------------
def test_manifest_carries_exactly_the_declared_fields():
    m = build_manifest(_settings(extra_thing="ignored"))
    assert set(m) == set(MANIFEST_FIELDS)


def test_every_manifest_field_is_documented():
    assert set(MANIFEST_FIELD_DOC) == set(MANIFEST_FIELDS)
    assert all(MANIFEST_FIELD_DOC[f].strip() for f in MANIFEST_FIELDS)


def test_missing_setting_raises_rather_than_defaulting():
    # A defaulted value would compare equal to an explicitly-set one, which defeats the
    # comparison the manifest exists to perform.
    for field in MANIFEST_FIELDS:
        partial = _settings()
        del partial[field]
        try:
            build_manifest(partial)
        except KeyError:
            continue
        raise AssertionError(f"build_manifest accepted a manifest missing {field}")


# ---- conflict detection --------------------------------------------------
def test_identical_settings_have_no_conflicts():
    m = build_manifest(_settings())
    assert manifest_conflicts(m, m) == []


def test_a_change_in_any_single_field_is_detected():
    # Loop over the field list rather than spot-checking one field, so a newly added
    # field cannot be left unguarded.
    for field in MANIFEST_FIELDS:
        saved = build_manifest(_settings())
        current = dict(saved)
        current[field] = "a-different-value"
        conflicts = manifest_conflicts(saved, current)
        assert [c[0] for c in conflicts] == [field], field
        assert conflicts[0][1] == saved[field]
        assert conflicts[0][2] == current[field]


def test_absent_manifest_conflicts_on_every_field():
    # "We do not know how the existing rows were made" must be strictly worse than a
    # known mismatch, never equivalent to agreement.
    current = build_manifest(_settings())
    conflicts = manifest_conflicts(None, current)
    assert {c[0] for c in conflicts} == set(MANIFEST_FIELDS)
    assert all(c[1] is None for c in conflicts)


def test_json_roundtrip_does_not_invent_conflicts():
    # The manifest is stored as JSON; a resume must not be blocked by serialisation.
    m = build_manifest(_settings(alpha=0.05, era_fallback=True,
                                 broad_includes_safety=False,
                                 population="interventional", schema="ctgov"))
    restored = json.loads(json.dumps(m))
    assert manifest_conflicts(restored, m) == []


def test_numeric_string_and_float_agree():
    # An older manifest may hold alpha as a string; 0.05 == "0.05" is False in Python and
    # would block a legitimate resume.
    saved = build_manifest(_settings(alpha="0.05"))
    current = build_manifest(_settings(alpha=0.05))
    assert [c[0] for c in manifest_conflicts(saved, current)] == []


def test_bool_and_int_do_not_silently_agree():
    # True == 1 in Python. A flag and a count are different things and must not compare
    # equal just because one is truthy.
    saved = build_manifest(_settings(era_fallback=True))
    current = build_manifest(_settings(era_fallback=1))
    assert [c[0] for c in manifest_conflicts(saved, current)] == ["era_fallback"]


def test_false_and_none_are_distinguished():
    saved = build_manifest(_settings(era_fallback=False))
    current = build_manifest(_settings(era_fallback=None))
    assert [c[0] for c in manifest_conflicts(saved, current)] == ["era_fallback"]


def test_every_conflict_gets_an_explanatory_line():
    current = build_manifest(_settings())
    conflicts = manifest_conflicts(None, current)
    lines = describe_conflicts(conflicts)
    assert len(lines) == len(conflicts)
    for field in MANIFEST_FIELDS:
        assert any(line.startswith(f"{field}:") for line in lines), field


# ---- id diffing ----------------------------------------------------------
def test_remaining_ids_partition_the_input():
    all_ids = [f"NCT{i:08d}" for i in range(10)]
    done = all_ids[:4]
    remaining = ids_remaining(all_ids, done)
    # structural: nothing lost, nothing duplicated, nothing invented
    assert len(remaining) + len(done) == len(all_ids)
    assert set(remaining) | set(done) == set(all_ids)
    assert not set(remaining) & set(done)


def test_remaining_ids_preserve_input_order():
    # The pull walks ids in order and the operator reads chunk numbers against that.
    all_ids = [f"NCT{i:08d}" for i in range(10)]
    done = {all_ids[3], all_ids[7]}
    remaining = ids_remaining(all_ids, done)
    assert remaining == [i for i in all_ids if i not in done]


def test_id_comparison_ignores_case_and_whitespace():
    all_ids = ["NCT00000001", "NCT00000002"]
    assert ids_remaining(all_ids, ["nct00000001"]) == ["NCT00000002"]
    assert ids_remaining(all_ids, ["  NCT00000001  "]) == ["NCT00000002"]


def test_nothing_done_returns_everything_and_all_done_returns_nothing():
    all_ids = [f"NCT{i:08d}" for i in range(5)]
    assert ids_remaining(all_ids, []) == all_ids
    assert ids_remaining(all_ids, all_ids) == []


def test_done_ids_not_in_the_population_are_harmless():
    # A previous run with a wider scope may have ids the current population lacks; that
    # must not error or drop current ids.
    all_ids = ["NCT00000001"]
    assert ids_remaining(all_ids, ["NCT99999999"]) == all_ids


def test_blank_done_ids_are_ignored_rather_than_matching_everything():
    all_ids = ["NCT00000001"]
    assert ids_remaining(all_ids, ["", "   ", None]) == all_ids


# ---- header validation ---------------------------------------------------
def test_matching_header_is_safe():
    expected = list(MANIFEST_FIELDS)
    assert header_problems(expected, expected) == []


def test_missing_header_is_a_problem():
    assert header_problems(None, list(MANIFEST_FIELDS))


def test_reordered_header_is_refused_even_though_the_columns_match():
    # The dangerous case: same column set, different order. Appending would write values
    # under the wrong headers, and no set-based check would notice.
    expected = list(MANIFEST_FIELDS)
    reordered = list(reversed(expected))
    assert expected != reordered, "need at least two columns for this test to mean anything"
    problems = header_problems(reordered, expected)
    assert problems
    assert any("ORDER" in p or "order" in p for p in problems)


def test_missing_and_extra_columns_are_both_reported():
    expected = list(MANIFEST_FIELDS)
    existing = expected[1:] + ["something_else"]
    problems = header_problems(existing, expected)
    joined = " ".join(problems)
    assert expected[0] in joined
    assert "something_else" in joined