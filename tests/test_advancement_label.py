"""Tests for advancement_label. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from datetime import date

from trial_pos.services.advancement_label import (
    DISEASE_EXACT, DISEASE_FAMILY, LABEL_NEGATIVE, LABEL_POSITIVE, REASON_FLOOR,
    REASON_NO_CONDITION, REASON_NO_NAME, REASON_NOT_EARLY, AdvancementIndex, TrialFacts,
    name_keys, same_disease, trial_advancement_label,
)
from trial_pos.services.eligibility import ELIGIBLE, INELIGIBLE, UNDETERMINABLE

OK = {"verdict": ELIGIBLE, "reason": ""}
PSA, RA, NSCLC, LUNG = "arthritis, psoriatic", "arthritis, rheumatoid", \
    "carcinoma, non-small-cell lung", "lung neoplasms"


def _t(nct, phase, submitted, readout=None, names=("drugx",), mesh=(PSA,), ancestors=()):
    return TrialFacts(nct, phase, submitted, readout, frozenset(names), frozenset(mesh),
                      frozenset(ancestors))


FOCAL = _t("NCT1", "PHASE2", date(2015, 1, 1), date(2016, 1, 1))


def _label(focal, *others, rule=DISEASE_EXACT, w=3):
    return trial_advancement_label(OK, focal, AdvancementIndex((focal,) + others), w, rule)


def test_a_later_phase_in_the_same_disease_within_the_window_is_one():
    later = _t("NCT2", "PHASE3", date(2017, 6, 1))
    assert _label(FOCAL, later)["label"] == LABEL_POSITIVE
    too_late = _t("NCT3", "PHASE3", date(2019, 6, 1))
    assert _label(FOCAL, too_late)["label"] == LABEL_NEGATIVE
    same_phase = _t("NCT4", "PHASE2", date(2017, 6, 1))
    assert _label(FOCAL, same_phase)["label"] == LABEL_NEGATIVE


def test_psoriatic_is_not_rheumatoid_under_either_rule():
    other_disease = _t("NCT2", "PHASE3", date(2017, 6, 1), mesh=(RA,), ancestors=("arthritis",))
    focal = FOCAL._replace(ancestors=frozenset({"arthritis"}))
    for rule in (DISEASE_EXACT, DISEASE_FAMILY):
        assert _label(focal, other_disease, rule=rule)["label"] == LABEL_NEGATIVE, rule


def test_the_family_rule_joins_one_disease_coded_at_two_depths():
    focal = FOCAL._replace(mesh=frozenset({NSCLC}), ancestors=frozenset({LUNG}))
    later = _t("NCT2", "PHASE3", date(2017, 6, 1), mesh=(LUNG,))
    assert not same_disease(focal, later, DISEASE_EXACT)
    assert same_disease(focal, later, DISEASE_FAMILY)
    assert _label(focal, later, rule=DISEASE_FAMILY)["label"] == LABEL_POSITIVE


def test_the_floor_excludes_a_programme_already_further_along_in_this_disease():
    earlier = _t("NCT2", "PHASE3", date(2010, 1, 1))
    later = _t("NCT3", "PHASE3", date(2017, 1, 1))
    got = _label(FOCAL, later, earlier)
    assert got == {"verdict": INELIGIBLE, "reason": REASON_FLOOR, "label": None}
    elsewhere = _t("NCT4", "PHASE3", date(2010, 1, 1), mesh=(RA,))     # another disease
    assert _label(FOCAL, elsewhere, later)["label"] == LABEL_POSITIVE


def test_what_cannot_be_judged_is_undeterminable_and_late_phases_are_out():
    assert _label(FOCAL._replace(names=frozenset()))["reason"] == REASON_NO_NAME
    got = _label(FOCAL._replace(mesh=frozenset()))
    assert got["verdict"] == UNDETERMINABLE and got["reason"] == REASON_NO_CONDITION
    assert _label(FOCAL._replace(phase="PHASE3"))["reason"] == REASON_NOT_EARLY
    rec = {"verdict": INELIGIBLE, "reason": "post_approval_phase"}
    assert trial_advancement_label(rec, FOCAL, AdvancementIndex(()), 3, DISEASE_EXACT) == \
        {"verdict": INELIGIBLE, "reason": "post_approval_phase", "label": None}


def test_name_keys_link_a_code_to_its_spaced_spelling_and_drop_salts():
    assert name_keys("XYZ-123") & name_keys("xyz 123")
    assert name_keys("cediranib maleate") & name_keys("Cediranib")
    assert all(len(k) >= 4 for k in name_keys("IL-2 10 mg"))
