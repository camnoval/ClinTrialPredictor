"""Tests for market_label. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from datetime import date

from trial_pos.services.drug_resolution import (
    MATCH_FULL, MATCH_NONE, MATCH_PARTIAL_ONLY, MATCH_UNDETERMINABLE,
)
from trial_pos.services.eligibility import ELIGIBLE, INELIGIBLE, UNDETERMINABLE, years_after
from trial_pos.services.form_approval import (
    APPROVAL_COLUMNS, AgentApproval, Query, approval_row, approved_within, parse_approval_row,
)
from trial_pos.services.market_label import (
    BOUND_LOOSE, BOUND_STRICT, DECISION_BEYOND, DECISION_NONE, DECISION_W3, DECISION_W5,
    D10_LOWER_YEARS, D10_UPPER_YEARS, LABEL_NEGATIVE, LABEL_POSITIVE, REASON_PARTIAL_ONLY,
    REASON_PRIOR_APPROVAL, REASON_PRIOR_UNKNOWN, REASON_SELECTION_UNDETERMINABLE,
    REASON_UNRESOLVED, REASON_WINDOW_UNKNOWN, decision_line, decisive_cases, identity_key,
    lag_summary, nearest_rank, trial_market_label,
)

R = date(2015, 6, 1)
W = 3
OK = {"verdict": ELIGIBLE, "reason": ""}


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def _a(strict=(), loose=None, su=0, lu=0, gap=False):
    loose = tuple(strict) if loose is None else tuple(loose)
    return AgentApproval(len(loose), tuple(strict), loose, su, lu, len(strict), len(loose),
                         None, gap)


BEFORE = _a(strict=(date(2000, 1, 1),))
LATER = _a(strict=(years_after(R, W - 1),))
TOO_LATE = _a(strict=(years_after(R, W + 1),))
NEVER = _a()
MAYBE_WITHIN = _a(strict=(), loose=(years_after(R, W - 1),))
GAP = _a(gap=True)


def test_market_eligibility_verdicts_pass_through_unchanged():
    for rec in ({"verdict": INELIGIBLE, "reason": "not_a_pivotal_phase"},
                {"verdict": UNDETERMINABLE, "reason": "readout_date_is_planned_or_absent"}):
        got = trial_market_label(rec, MATCH_FULL, [LATER], R, W)
        assert (got["verdict"], got["reason"], got["label"]) == (
            rec["verdict"], rec["reason"], None)


def test_an_unidentified_drug_is_undeterminable_never_zero_d24():
    for mc, reason in ((MATCH_PARTIAL_ONLY, REASON_PARTIAL_ONLY), (MATCH_NONE, REASON_UNRESOLVED),
                       (MATCH_UNDETERMINABLE, REASON_SELECTION_UNDETERMINABLE)):
        got = trial_market_label(OK, mc, [], R, W)
        assert got == {"verdict": UNDETERMINABLE, "reason": reason, "label": None}
    assert _raises(trial_market_label, OK, "bogus", [], R, W)
    assert _raises(trial_market_label, OK, MATCH_FULL, [], R, W)


def test_the_floor_excludes_a_trial_whose_every_agent_was_already_approved():
    got = trial_market_label(OK, MATCH_FULL, [BEFORE, BEFORE], R, W)
    assert got == {"verdict": INELIGIBLE, "reason": REASON_PRIOR_APPROVAL, "label": None}


def test_a_new_agent_approved_within_the_window_is_one_and_outside_it_zero():
    assert trial_market_label(OK, MATCH_FULL, [LATER], R, W)["label"] == LABEL_POSITIVE
    assert trial_market_label(OK, MATCH_FULL, [TOO_LATE], R, W)["label"] == LABEL_NEGATIVE
    assert trial_market_label(OK, MATCH_FULL, [NEVER], R, W)["label"] == LABEL_NEGATIVE
    # an old partner drug does not stop a new agent's verdict
    assert trial_market_label(OK, MATCH_FULL, [BEFORE, NEVER], R, W)["label"] == LABEL_NEGATIVE
    assert trial_market_label(OK, MATCH_FULL, [BEFORE, LATER], R, W)["label"] == LABEL_POSITIVE


def test_unknowns_never_become_zero_but_a_proved_one_stands():
    got = trial_market_label(OK, MATCH_FULL, [GAP, NEVER], R, W)
    assert got == {"verdict": UNDETERMINABLE, "reason": REASON_PRIOR_UNKNOWN, "label": None}
    assert trial_market_label(OK, MATCH_FULL, [GAP, LATER], R, W)["label"] == LABEL_POSITIVE
    got = trial_market_label(OK, MATCH_FULL, [MAYBE_WITHIN], R, W)
    assert got == {"verdict": UNDETERMINABLE, "reason": REASON_WINDOW_UNKNOWN, "label": None}


def test_an_approval_on_the_readout_day_is_within_and_the_day_before_is_prior():
    same_day = _a(strict=(R,))
    assert trial_market_label(OK, MATCH_FULL, [same_day], R, W)["label"] == LABEL_POSITIVE
    day_before = _a(strict=(date.fromordinal(R.toordinal() - 1),))
    assert trial_market_label(OK, MATCH_FULL, [day_before], R, W)["reason"] == REASON_PRIOR_APPROVAL


def test_approved_within_is_tri_state_and_refuses_a_backwards_window():
    end = years_after(R, W)
    assert approved_within(LATER, R, end) is True
    assert approved_within(NEVER, R, end) is False
    assert approved_within(MAYBE_WITHIN, R, end) is None
    assert approved_within(_a(lu=1), R, end) is None
    assert approved_within(GAP, R, end) is None
    assert _raises(approved_within, LATER, R, date(2000, 1, 1))


def test_approval_rows_round_trip_and_malformed_rows_fail_closed():
    a = _a(strict=(date(2012, 1, 1),), loose=(date(2010, 1, 1), date(2012, 1, 1)), lu=1)
    row = approval_row("NCT1", 1, "5", Query(frozenset({5})), a, "contained", "pin")
    assert parse_approval_row(row) == a
    for col, bad in (("strict_dates", "2012-01-01|2010-01-01"), ("source_gap", ""),
                     ("loose_dates", "notadate"), ("brand_pinned", "yes")):
        broken = dict(row, **{col: bad})
        assert _raises(parse_approval_row, broken), col
    short = {k: v for k, v in row.items() if k != APPROVAL_COLUMNS[-1]}
    assert _raises(parse_approval_row, short)


# ---- the lag -------------------------------------------------------------------------------
SNAP = date(2026, 10, 1)


def _rec(ident, nct, readout, a):
    return (identity_key(*ident), nct, readout, a)


def test_decisive_case_is_the_earliest_new_readout_with_follow_up():
    ident = ("5", "", "", "")
    early, late = date(2010, 1, 1), date(2012, 1, 1)
    approval = _a(strict=(date(2013, 1, 1),))
    cases = decisive_cases([_rec(ident, "NCT2", late, approval),
                            _rec(ident, "NCT1", early, approval)], SNAP, 10)
    assert len(cases) == 1 and cases[0].readout == early and cases[0].nct_id == "NCT1"
    # without enough follow-up, a readout is not used
    assert decisive_cases([_rec(ident, "NCT3", date(2020, 1, 1), approval)], SNAP, 10) == []
    # an identity already approved at the readout is not a new molecule there
    old = _a(strict=(date(2000, 1, 1),))
    assert decisive_cases([_rec(ident, "NCT4", early, old)], SNAP, 10) == []


def test_lag_summary_reports_both_bounds_and_window_shares():
    cases = decisive_cases([
        _rec(("1", "", "", ""), "NCT1", date(2010, 1, 1),
             _a(strict=(date(2014, 1, 1),), loose=(date(2011, 1, 1), date(2014, 1, 1)))),
        _rec(("2", "", "", ""), "NCT2", date(2010, 1, 1), _a(strict=(date(2012, 1, 1),))),
        _rec(("3", "", "", ""), "NCT3", date(2010, 1, 1), _a()),
    ], SNAP, 10)
    s = lag_summary(cases, 10, (3, 5))
    assert s["identities"] == 3
    assert s[BOUND_LOOSE]["approved_within_follow_up"] == 2
    assert s[BOUND_STRICT]["within_window"] == {3: 1, 5: 2}
    assert s[BOUND_LOOSE]["within_window"] == {3: 2, 5: 2}
    assert s[BOUND_LOOSE]["percentiles"][90] <= s[BOUND_STRICT]["percentiles"][90]


def test_nearest_rank_and_the_d10_decision_line():
    assert nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9
    assert nearest_rank([7], 50) == 7
    assert _raises(nearest_rank, [], 90)
    assert decision_line(D10_LOWER_YEARS) == DECISION_W3
    assert decision_line(D10_LOWER_YEARS + 0.1) == DECISION_W5
    assert decision_line(D10_UPPER_YEARS) == DECISION_W5
    assert decision_line(D10_UPPER_YEARS + 0.1) == DECISION_BEYOND
    assert decision_line(None) == DECISION_NONE