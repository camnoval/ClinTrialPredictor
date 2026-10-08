"""Tests for resolution_sample. Plain asserts so tests/_run_stdlib.py works.

No transcribed numbers: targets are checked by the property that defines them.
"""
from __future__ import annotations

from statistics import NormalDist

from trial_pos.services.resolution_sample import (
    CONFIDENCE, KEY_COLUMNS, LABEL_COLUMNS, PRECISION_GATE, RECALL_GATE, SHEET_COLUMNS,
    STRATA, STRATUM_MATCHED, STRATUM_UNMATCHED, TARGET_HALF_WIDTH, key_row,
    n_for_half_width, ordered_strata, presentation, sheet_row, strata, stratum_of, tranche,
    wilson_half_width, wilson_interval, z_value,
)


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


def test_z_is_the_two_sided_quantile():
    z = z_value(CONFIDENCE)
    assert abs(NormalDist().cdf(z) - (1 - (1 - CONFIDENCE) / 2)) < 1e-12


def test_wilson_contains_the_estimate_and_refuses_the_impossible():
    lo, hi = wilson_interval(9, 10)
    assert lo < 0.9 < hi and 0 <= lo and hi <= 1
    assert wilson_interval(0, 0) is None
    assert _raises(wilson_interval, 11, 10) and _raises(wilson_interval, -1, 10)


def test_half_width_shrinks_with_n():
    assert wilson_half_width(RECALL_GATE, 50) > wilson_half_width(RECALL_GATE, 500)


def test_targets_are_the_smallest_n_meeting_the_half_width():
    for p in (RECALL_GATE, PRECISION_GATE):
        n = n_for_half_width(p, TARGET_HALF_WIDTH)
        assert wilson_half_width(p, n) <= TARGET_HALF_WIDTH
        assert n == 1 or wilson_half_width(p, n - 1) > TARGET_HALF_WIDTH


def test_strata_put_undeterminable_with_unmatched_and_refuse_missing_trials():
    assert stratum_of(True) == STRATUM_MATCHED
    assert stratum_of(False) == stratum_of(None) == STRATUM_UNMATCHED
    got = strata({"N1": True, "N2": False, "N3": None}, ["N3", "N1", "N2"])
    assert got == {STRATUM_MATCHED: ["N1"], STRATUM_UNMATCHED: ["N2", "N3"]}
    assert _raises(strata, {"N1": True}, ["N1", "N9"], exc=KeyError)


def test_order_is_independent_of_input_order_and_tranche_two_continues_tranche_one():
    members = {STRATUM_MATCHED: [f"M{i}" for i in range(20)],
               STRATUM_UNMATCHED: [f"U{i}" for i in range(20)]}
    rev = {s: list(reversed(v)) for s, v in members.items()}
    o = ordered_strata(members)
    assert o == ordered_strata(rev)
    t1, t2 = tranche(o, 0, 5), tranche(o, 5, 5)
    for s in STRATA:
        assert [n for _r, n in t1[s] + t2[s]] == o[s][:10]
        assert [r for r, _n in t2[s]] == list(range(5, 10))


def test_presentation_ids_are_unique_and_the_sheet_reveals_no_stratum():
    o = ordered_strata({STRATUM_MATCHED: ["M1", "M2"], STRATUM_UNMATCHED: ["U1", "U2"]})
    shown = presentation(tranche(o, 0, 2))
    ids = [p[0] for p in shown]
    assert len(set(ids)) == len(ids) == 4
    row = sheet_row(ids[0], shown[0][3])
    assert set(row) == set(SHEET_COLUMNS) and all(row[c] == "" for c in LABEL_COLUMNS)
    assert "stratum" not in SHEET_COLUMNS and "matched" not in SHEET_COLUMNS
    k = key_row(ids[0], shown[0][1], shown[0][2], 1,
                {"nct_id": shown[0][3], "selection_outcome": "x", "matched": "", "drugs": ""})
    assert set(k) == set(KEY_COLUMNS)
