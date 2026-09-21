"""Tests for the FDAAA applicability rule. Plain asserts so tests/_run_stdlib.py works.

The most important tests here are the ASYMMETRY ones. AACT cannot see the IND hook, so the
rule can identify applicable trials but can almost never confidently say "not applicable"
on jurisdictional grounds. Several tests pin that directly, because the tempting bug is to
read a missing US facility as a negative and thereby mislabel the 55.4% of trials where
`has_us_facility` is false.

No transcribed expected values. Dates come from the statutory constants in `population`,
phase memberships from the phase sets, and verdicts from the verdict constants.
"""
from __future__ import annotations

from datetime import timedelta

from trial_pos.services.fdaaa import (
    APPLICABLE, APPLICABILITY_REASONS, APPLICABILITY_VERDICTS, FLAG_FDAAA_EXEMPT,
    FLAG_NOT_REQUIRED_TO_POST, FLAG_THIN_TRAINING, NOT_APPLICABLE, PHASE1_ONLY_PHASES,
    PHASE_SPANNING_PHASE1, REASON_DOC, REASON_ERA_UNKNOWN, REASON_HOOKS_UNOBSERVABLE,
    REASON_HOOK_US_EXPORT, REASON_HOOK_US_FACILITY, REASON_NO_REGULATED_PRODUCT,
    REASON_PHASE_1_ONLY, REASON_PHASE_UNKNOWN, REASON_PRE_STATUTE,
    REASON_PRODUCT_UNKNOWN, UNDETERMINABLE, VERDICT_DOC, applicability_coverage,
    applicability_for_row, compose_flag, product_in_scope, visible_hook,
)
from trial_pos.services.population import FDAAA_RESULTS_EFFECTIVE

ONE_DAY = timedelta(days=1)
AFTER_STATUTE = (FDAAA_RESULTS_EFFECTIVE + timedelta(days=365)).isoformat()


def _row(phase="PHASE3", drug="t", device=None, facility="t", export=None,
         completed=AFTER_STATUTE):
    """A trial that is APPLICABLE by default, so each test perturbs one condition."""
    row = {"phase": phase, "primary_completion_date": completed}
    if drug is not None:
        row["is_fda_regulated_drug"] = drug
    if device is not None:
        row["is_fda_regulated_device"] = device
    if facility is not None:
        row["has_us_facility"] = facility
    if export is not None:
        row["is_us_export"] = export
    return row


def test_the_default_fixture_is_applicable():
    # if this fails every other test below is testing the wrong thing
    assert applicability_for_row(_row())["verdict"] == APPLICABLE


# ---- THE ASYMMETRY: no visible hook is UNKNOWN, never a negative ---------
def test_no_visible_hook_is_undeterminable_not_not_applicable():
    # AACT records no IND field, so a trial with no US site may still have run under an
    # IND. Reading this as a negative would mislabel a majority of the population.
    out = applicability_for_row(_row(facility="f", export="f"))
    assert out["verdict"] == UNDETERMINABLE
    assert out["reason"] == REASON_HOOKS_UNOBSERVABLE


def test_absent_jurisdiction_fields_are_also_undeterminable():
    out = applicability_for_row(_row(facility=None, export=None))
    assert out["verdict"] == UNDETERMINABLE


def test_no_verdict_is_ever_not_applicable_on_jurisdiction_alone():
    # structural: vary ONLY the jurisdiction inputs on an otherwise-applicable trial and
    # confirm the rule never returns a negative. This is the module's central claim.
    for facility in ("t", "f", None):
        for export in ("t", "f", None):
            out = applicability_for_row(_row(facility=facility, export=export))
            assert out["verdict"] in (APPLICABLE, UNDETERMINABLE), (facility, export)


def test_either_hook_alone_is_enough():
    assert applicability_for_row(_row(facility="t", export="f"))["verdict"] == APPLICABLE
    assert applicability_for_row(_row(facility="f", export="t"))["verdict"] == APPLICABLE


def test_us_facility_is_preferred_as_the_stated_reason():
    # it derives from facility records rather than a sponsor declaration, so it exists for
    # pre-2017 trials; when both hooks fire the more reliable one should be named
    out = applicability_for_row(_row(facility="t", export="t"))
    assert out["reason"] == REASON_HOOK_US_FACILITY


def test_visible_hook_returns_none_rather_than_a_false_reason():
    assert visible_hook({"has_us_facility": "f", "is_us_export": "f"}) is None
    assert visible_hook({"has_us_facility": "t"}) == REASON_HOOK_US_FACILITY
    assert visible_hook({"is_us_export": "t"}) == REASON_HOOK_US_EXPORT


# ---- the conditions AACT observes fully: confident negatives -------------
def test_completion_before_the_statute_is_not_applicable():
    before = (FDAAA_RESULTS_EFFECTIVE - ONE_DAY).isoformat()
    out = applicability_for_row(_row(completed=before))
    assert (out["verdict"], out["reason"]) == (NOT_APPLICABLE, REASON_PRE_STATUTE)


def test_phase_1_only_is_not_applicable():
    for phase in PHASE1_ONLY_PHASES:
        out = applicability_for_row(_row(phase=phase))
        assert (out["verdict"], out["reason"]) == (NOT_APPLICABLE,
                                                   REASON_PHASE_1_ONLY), phase


def test_phase_1_exemption_holds_regardless_of_jurisdiction():
    # the statute excludes phase-1-only studies outright; ordering the jurisdiction test
    # first would turn this clear exemption into an undeterminable
    phase = sorted(PHASE1_ONLY_PHASES)[0]
    for facility in ("t", "f", None):
        out = applicability_for_row(_row(phase=phase, facility=facility, export=None))
        assert out["reason"] == REASON_PHASE_1_ONLY, facility


def test_both_product_declarations_false_is_not_applicable():
    out = applicability_for_row(_row(drug="f", device="f"))
    assert (out["verdict"], out["reason"]) == (NOT_APPLICABLE,
                                               REASON_NO_REGULATED_PRODUCT)


def test_one_false_declaration_and_one_absent_leaves_the_question_open():
    # a trial can be either or both, and an absent declaration is not a denial
    assert product_in_scope({"is_fda_regulated_drug": "f"}) is None
    assert product_in_scope({"is_fda_regulated_drug": "f",
                             "is_fda_regulated_device": "f"}) is False
    assert product_in_scope({"is_fda_regulated_drug": "t",
                             "is_fda_regulated_device": "f"}) is True


def test_device_alone_puts_a_trial_in_scope():
    assert applicability_for_row(_row(drug=None, device="t"))["verdict"] == APPLICABLE


# ---- undeterminable for missing inputs ------------------------------------
def test_absent_product_declarations_are_undeterminable():
    out = applicability_for_row(_row(drug=None, device=None))
    assert (out["verdict"], out["reason"]) == (UNDETERMINABLE, REASON_PRODUCT_UNKNOWN)


def test_unknown_phase_is_undeterminable_not_assumed_non_phase_1():
    # assuming it is not phase 1 would MANUFACTURE applicable trials
    for phase in ("NA", "", None):
        out = applicability_for_row(_row(phase=phase))
        assert (out["verdict"], out["reason"]) == (UNDETERMINABLE,
                                                   REASON_PHASE_UNKNOWN), phase


def test_no_completion_date_is_undeterminable():
    row = _row(completed=None)
    row.pop("primary_completion_date", None)
    out = applicability_for_row(row)
    assert (out["verdict"], out["reason"]) == (UNDETERMINABLE, REASON_ERA_UNKNOWN)


def test_era_is_tested_before_everything_else():
    # a pre-statute trial with no other usable input is still a confident negative: no
    # obligation existed to breach, whatever else is missing
    before = (FDAAA_RESULTS_EFFECTIVE - ONE_DAY).isoformat()
    out = applicability_for_row(_row(phase="NA", drug=None, device=None, facility=None,
                                     export=None, completed=before))
    assert (out["verdict"], out["reason"]) == (NOT_APPLICABLE, REASON_PRE_STATUTE)


# ---- the PHASE1/PHASE2 reading, flagged as a judgment --------------------
def test_spanning_phase_is_treated_as_in_scope():
    # not phase-1-ONLY on its face, so the exclusion does not apply. A reading, not a fact.
    for phase in PHASE_SPANNING_PHASE1:
        out = applicability_for_row(_row(phase=phase))
        assert out["verdict"] == APPLICABLE, phase
        assert out["reason"] != REASON_PHASE_1_ONLY


def test_spanning_and_excluded_phase_sets_are_disjoint():
    assert not (PHASE1_ONLY_PHASES & PHASE_SPANNING_PHASE1)


def test_coverage_reports_the_spanning_phase_sensitivity():
    # the reading moves real trials, so its effect must be countable rather than buried
    rows = [_row(phase=sorted(PHASE_SPANNING_PHASE1)[0]),
            _row(phase=sorted(PHASE1_ONLY_PHASES)[0]),
            _row()]
    out = applicability_coverage(rows)
    assert out["phase_spanning_phase1"]["total"] == 1
    assert sum(out["phase_spanning_phase1"]["verdict"].values()) == 1


# ---- record shape and documentation --------------------------------------
def test_every_verdict_and_reason_is_documented():
    for verdict in APPLICABILITY_VERDICTS:
        assert verdict in VERDICT_DOC and VERDICT_DOC[verdict].strip()
    for reason in APPLICABILITY_REASONS:
        assert reason in REASON_DOC and REASON_DOC[reason].strip()


def test_every_returned_reason_is_a_declared_one():
    # a reason invented at a branch would not be documented or countable
    rows = [_row(), _row(phase="PHASE1"), _row(phase="NA"), _row(drug="f", device="f"),
            _row(drug=None, device=None), _row(facility="f", export="f"),
            _row(completed=(FDAAA_RESULTS_EFFECTIVE - ONE_DAY).isoformat())]
    for row in rows:
        out = applicability_for_row(row)
        assert out["reason"] in APPLICABILITY_REASONS, out


def test_coverage_verdicts_sum_to_the_row_count():
    rows = [_row(), _row(phase="PHASE1"), _row(facility="f", export="f")]
    out = applicability_coverage(rows)
    assert sum(out["verdicts"].values()) == out["total"] == len(rows)
    assert sum(out["reasons"].values()) == len(rows)


# ---- the user-facing flag -------------------------------------------------
def test_phase_1_flag_names_the_statute_not_just_the_sample_size():
    # a flag that says only "few trials like yours" reads as generic hedging; naming the
    # mechanism is what makes it credible
    flag = compose_flag(applicability_for_row(_row(phase="PHASE1")))
    assert FLAG_FDAAA_EXEMPT in flag
    assert FLAG_THIN_TRAINING in flag


def test_other_not_applicable_verdicts_get_the_general_obligation_wording():
    flag = compose_flag(applicability_for_row(_row(drug="f", device="f")))
    assert FLAG_NOT_REQUIRED_TO_POST in flag
    assert FLAG_FDAAA_EXEMPT not in flag


def test_an_applicable_trial_with_no_endpoint_clause_gets_no_flag():
    assert compose_flag(applicability_for_row(_row())) == ""


def test_the_endpoint_clause_is_supplied_not_invented():
    # the endpoint-type classifier does not exist yet; a flag that fabricated this clause
    # would assert a measurement nobody made
    clause = "This trial's primary endpoint is a measurement rather than a threshold test."
    flag = compose_flag(applicability_for_row(_row()), endpoint_clause=clause)
    assert clause in flag
    assert FLAG_THIN_TRAINING in flag


def test_both_clauses_appear_together_when_both_apply():
    clause = "endpoint clause"
    flag = compose_flag(applicability_for_row(_row(phase="PHASE1")),
                        endpoint_clause=clause)
    assert FLAG_FDAAA_EXEMPT in flag and clause in flag


# ---- the guard that matters: the PULL must not import this --------------
def test_applicability_is_absent_from_the_pull_script():
    # population.py has a name-based tripwire for the same concern. This is the stronger
    # version: the pull must not reach the verdict at all, whatever it is called. A pull
    # that decided applicability would bury a judgment where nobody could audit it.
    from pathlib import Path
    pull = Path(__file__).resolve().parent.parent / "scripts" / "pull_aact_results.py"
    source = pull.read_text(encoding="utf-8")
    assert "services.fdaaa" not in source
    assert "applicability_for_row" not in source