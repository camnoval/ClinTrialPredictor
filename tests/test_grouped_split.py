"""Tests for grouped_split and the D-67 helpers in training_design."""
from __future__ import annotations

from datetime import timedelta

from trial_pos.services import grouped_split as G
from trial_pos.services import training_design as D
from trial_pos.services.eligibility import years_after
from trial_pos.services.temporal_split import SIDE_TEST, SIDE_TRAIN
from trial_pos.services.trial_features import TARGET_ADVANCEMENT, TARGET_ENDPOINT_MET

DAY = timedelta(days=1)


def test_groups_join_transitively_through_shared_keys():
    keys = {"a": {"x"}, "b": {"x", "y"}, "c": {"y"}, "d": {"z"}, "e": set()}
    got = G.groups("abcde", keys)
    assert got == [["a", "b", "c"], ["d"], ["e"]]


def _folds(n_trials, sponsors, drugs=None, folds=5):
    trials = [f"NCT{i:04d}" for i in range(n_trials)]
    sp = {t: sponsors(i) for i, t in enumerate(trials)}
    dk = {t: drugs(i) for i, t in enumerate(trials)} if drugs else {}
    return trials, G.assign_folds(trials, dk, sp, folds)


def test_no_group_is_split_across_folds_and_folds_are_balanced():
    trials, f = _folds(200, lambda i: f"s{i % 40}", lambda i: {f"d{i % 7}"} if i % 3 else set())
    assert f.grouping is not None
    for t in trials:
        for u in trials:
            if t < u and int(t[3:]) % 40 == int(u[3:]) % 40:
                assert f.fold_of[t] == f.fold_of[u]
    sizes = [sum(1 for t in trials if f.fold_of[t] == k) for k in range(5)]
    assert max(sizes) - min(sizes) <= f.largest_group


def test_a_giant_drug_cluster_falls_back_to_sponsor_groups():
    # one drug shared by every trial chains them all into one group
    trials, f = _folds(100, lambda i: f"s{i % 25}", lambda i: {"everywhere"})
    assert f.grouping == G.GROUPING_SPONSOR
    assert f.tried[0] == (G.GROUPING_DRUG_AND_SPONSOR, len(trials))


def test_when_no_grouping_fits_the_check_is_refused_not_forced():
    trials, f = _folds(50, lambda i: "one_sponsor")
    assert f.grouping is None and f.fold_of == {}


def test_assignment_is_deterministic_and_order_free():
    trials, f1 = _folds(120, lambda i: f"s{i % 17}", lambda i: {f"d{i % 5}"} if i % 2 else set())
    f2 = G.assign_folds(list(reversed(trials)), {t: ({f"d{int(t[3:]) % 5}"}
                                                      if int(t[3:]) % 2 else set())
                                                  for t in trials},
                        {t: f"s{int(t[3:]) % 17}" for t in trials}, 5)
    assert f1 == f2


def test_bad_fold_requests_raise():
    for call in (lambda: G.assign_folds(["a", "b"], {}, {}, 1),
                 lambda: G.assign_folds(["a"], {}, {}, 2)):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_rolling_boundaries_include_the_headline_and_reuse_its_rules():
    assert D.SPLIT_BOUNDARY in D.ROLLING_BOUNDARIES
    assert list(D.ROLLING_BOUNDARIES) == sorted(D.ROLLING_BOUNDARIES)
    readout = D.ROLLING_BOUNDARIES[0] + DAY            # after the first, before the last
    begun = years_after(readout, -1)
    assert D.split_side_for(TARGET_ENDPOINT_MET, readout, begun, "",
                            boundary=D.ROLLING_BOUNDARIES[-1])[0] == SIDE_TRAIN
    late = D.ROLLING_BOUNDARIES[0] + DAY
    assert D.split_side_for(TARGET_ENDPOINT_MET, years_after(late, 1), late, "",
                            boundary=D.ROLLING_BOUNDARIES[0])[0] == SIDE_TEST


def test_the_left_cut_applies_to_advancement_only():
    old = D.ADVANCEMENT_LEFT_CUT - DAY
    assert D.in_left_cut(TARGET_ADVANCEMENT, old) is True
    assert D.in_left_cut(TARGET_ADVANCEMENT, D.ADVANCEMENT_LEFT_CUT) is False
    assert D.in_left_cut(TARGET_ENDPOINT_MET, old) is False
