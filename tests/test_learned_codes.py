"""Tests for learned_codes. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.learned_codes import (
    KIND_OTHER, KIND_PRIMARY, LEARNED_ALREADY, LEARNED_CONFLICT, LEARNED_KEPT, LEARNED_TOO_FEW,
    MIN_TRIALS, Evidence, code_key, learn_codes,
)

A, B = frozenset({1}), frozenset({2})


def _never(_code):
    return False


def test_only_a_whole_name_shaped_like_a_code_is_a_code():
    assert code_key("LCZ696") == "lcz696" and code_key("BMS-734016") == "bms734016"
    assert code_key("MK 3475") == "mk3475"
    for name in ("sintilimab", "vitamin d3", "pembrolizumab 200 mg", "lcz696 tablets"):
        assert code_key(name) is None, name


def test_a_code_needs_agreeing_evidence_from_enough_trials():
    ev = [Evidence(f"NCT{i}", KIND_OTHER, "ABC-777", (), A) for i in range(MIN_TRIALS)]
    kept, trials, audit = learn_codes(ev, _never)
    assert kept == {"abc777": A} and trials["abc777"] == MIN_TRIALS
    assert audit[LEARNED_KEPT] == 1
    few, _t, audit = learn_codes(ev[:MIN_TRIALS - 1], _never)
    assert few == {} and audit[LEARNED_TOO_FEW] == 1


def test_evidence_naming_two_drug_sets_is_refused_not_voted():
    ev = [Evidence("NCT1", KIND_OTHER, "ABC-777", (), A),
          Evidence("NCT2", KIND_OTHER, "ABC-777", (), A),
          Evidence("NCT3", KIND_PRIMARY, "beta", ("ABC-777",), B)]
    kept, _t, audit = learn_codes(ev, _never)
    assert kept == {} and audit[LEARNED_CONFLICT] == 1


def test_a_code_that_already_resolves_is_not_relearned():
    ev = [Evidence(f"NCT{i}", KIND_OTHER, "ABC-777", (), A) for i in range(3)]
    kept, _t, audit = learn_codes(ev, lambda c: True)
    assert kept == {} and audit[LEARNED_ALREADY] == 1


def test_a_primary_names_other_names_are_the_codes_read():
    ev = [Evidence(f"NCT{i}", KIND_PRIMARY, "alpha", ("XY-12", "Alpha 10 mg"), A)
          for i in range(MIN_TRIALS)]
    kept, _t, _a = learn_codes(ev, _never)
    assert kept == {"xy12": A}
