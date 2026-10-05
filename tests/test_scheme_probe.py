"""Tests for the scheme-repair diagnostic. Plain asserts for _run_stdlib.

NO TRANSCRIBED EXPECTED VALUES. The load-bearing test here is
`test_the_defaults_reproduce_the_shipping_rule`: this module re-implements the resolution
step, so the guard against drift is that at default arguments it must agree with
`classify_title` and `trial_gate` on every title in the endpoint-type corpus. Nothing here
asserts how much any variant improves anything -- that is the diagnostic's output, and a
test pinning it would freeze whatever the current patterns happen to do.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The endpoint-type corpus is REUSED rather than copied: the guard test below claims to
# agree with the shipping rule on every title that rule is tested against, and a second
# private corpus here would let the two drift until the claim was false. The path is added
# explicitly rather than relying on the runner to have done it -- `tests/_run_stdlib.py`
# happens to put this directory on the path and pytest happens to as well, and a test that
# depends on which runner invoked it is a test that fails in the other one.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trial_pos.services.endpoint_type import (
    CLASS_DOSE_FINDING, CLASS_EFFICACY, CLASS_GATE, CLASS_OTHER, CLASS_PHARMACOKINETIC,
    CLASS_PRECEDENCE, CLASS_SAFETY, ENDPOINT_CLASSES, GATE_APPLICABLE,
    GATE_NOT_APPLICABLE, GATE_UNDETERMINABLE, GATE_VERDICTS, classify_title,
    matched_classes, trial_gate,
)
from trial_pos.services.scheme_probe import (
    UNPROBEABLE, VARIANT_BASELINE, VARIANT_DOC, VARIANT_EFFICACY_OVER_PK, VARIANT_ORDER,
    VARIANT_PK_APPLICABLE, VARIANTS, gate_under, moved_share, outcome_gate, reclassified,
    resolve_class, variant_gates,
)
from test_endpoint_type import CORPUS

TITLES = tuple(title for title, _ in CORPUS)


# ---- the guard against drift ----------------------------------------------
def test_the_defaults_reproduce_the_shipping_rule():
    # THE test in this file. If the parameterised resolution step ever disagrees with the
    # shipping one at default arguments, every variant figure is measured against the
    # wrong baseline and the disagreement would surface only as a diagnostic somebody
    # trusted.
    for title in TITLES:
        expected = trial_gate([classify_title(title)])["gate"]
        assert outcome_gate(title) == expected


def test_the_baseline_variant_is_the_shipping_rule():
    assert variant_gates(TITLES, VARIANT_BASELINE) == [
        trial_gate([classify_title(t)])["gate"] for t in TITLES]


def test_resolve_class_at_default_matches_classify_title():
    for title in TITLES:
        assert resolve_class(matched_classes(title)) == classify_title(title)


# ---- resolution ------------------------------------------------------------
def test_nothing_matched_resolves_to_other():
    assert resolve_class(()) == CLASS_OTHER


def test_resolution_follows_the_given_order_not_the_input_order():
    # The bug this function was written to avoid: `matched_classes` returns hits already
    # sorted by the shipping precedence, so a resolver reading the input order would
    # reproduce baseline for every variant and the whole diagnostic would be inert.
    matched = (CLASS_DOSE_FINDING, CLASS_EFFICACY)
    assert resolve_class(matched, CLASS_PRECEDENCE) == CLASS_DOSE_FINDING
    reversed_order = tuple(reversed(CLASS_PRECEDENCE))
    assert resolve_class(matched, reversed_order) == CLASS_EFFICACY


def test_resolution_is_indifferent_to_the_input_sequence():
    matched = (CLASS_EFFICACY, CLASS_SAFETY)
    assert (resolve_class(matched) == resolve_class(tuple(reversed(matched))))


def test_a_class_outside_the_precedence_resolves_to_other():
    # CLASS_OTHER is not in CLASS_PRECEDENCE, so a matched set containing only it has no
    # selectable member and must fall through rather than be returned by accident.
    assert resolve_class((CLASS_OTHER,)) == CLASS_OTHER


# ---- gate mapping ----------------------------------------------------------
def test_gate_under_defaults_to_the_shipping_mapping():
    for endpoint_class in ENDPOINT_CLASSES:
        assert gate_under(endpoint_class) == CLASS_GATE[endpoint_class]


def test_gate_under_raises_on_an_unmapped_class():
    try:
        gate_under("a_class_nobody_decided_about")
    except ValueError:
        return
    raise AssertionError("gate_under should raise rather than default")


def test_gate_under_raises_on_a_verdict_outside_the_vocabulary():
    bad = dict(CLASS_GATE, **{CLASS_SAFETY: "probably_fine"})
    try:
        gate_under(CLASS_SAFETY, bad)
    except ValueError:
        return
    raise AssertionError("a candidate mapping with a bogus verdict should raise")


def test_every_variant_maps_every_class_to_a_real_verdict():
    for variant, (_, class_gate) in VARIANTS.items():
        assert set(class_gate) == set(ENDPOINT_CLASSES), variant
        for endpoint_class in ENDPOINT_CLASSES:
            assert gate_under(endpoint_class, class_gate) in GATE_VERDICTS


# ---- the variant registry --------------------------------------------------
def test_every_variant_is_documented():
    assert set(VARIANT_DOC) == set(VARIANTS)


def test_the_variant_order_is_a_permutation_of_the_variants():
    assert sorted(VARIANT_ORDER) == sorted(VARIANTS)
    assert len(VARIANT_ORDER) == len(set(VARIANT_ORDER))


def test_the_baseline_comes_first_in_the_order():
    # A table of candidates whose baseline is not the first row is a table read wrongly.
    assert VARIANT_ORDER[0] == VARIANT_BASELINE


def test_every_variant_precedence_is_a_permutation_of_the_shipping_one():
    # A candidate that DROPPED a class from the precedence order would send every title
    # matching only that class to CLASS_OTHER, which would register as a repair while
    # actually being an erasure.
    for variant, (precedence, _) in VARIANTS.items():
        assert sorted(precedence) == sorted(CLASS_PRECEDENCE), variant


def test_variant_gates_raises_on_an_unknown_variant():
    try:
        variant_gates(TITLES, "wishful_thinking")
    except ValueError:
        return
    raise AssertionError("variant_gates should raise on an unregistered variant")


# ---- what each variant actually does --------------------------------------
def test_the_pk_variant_moves_pharmacokinetic_endpoints_to_applicable():
    pk_titles = [t for t, expected in CORPUS if expected == CLASS_PHARMACOKINETIC]
    assert pk_titles, "the corpus must contain pharmacokinetic titles"
    for title in pk_titles:
        assert outcome_gate(title) == GATE_NOT_APPLICABLE
        assert variant_gates([title], VARIANT_PK_APPLICABLE) == [GATE_APPLICABLE]


def test_the_pk_variant_leaves_every_other_class_alone():
    # One change at a time, checked rather than asserted: a variant with side effects on
    # other classes would attribute their movement to the wrong cause.
    for title, expected in CORPUS:
        if expected == CLASS_PHARMACOKINETIC:
            continue
        assert variant_gates([title], VARIANT_PK_APPLICABLE) == [outcome_gate(title)]


def test_the_pk_variant_refuses_nothing_at_all():
    # The ceiling: with the only refusing class flipped, no endpoint can be refused.
    for gate in variant_gates(TITLES, VARIANT_PK_APPLICABLE):
        assert gate != GATE_NOT_APPLICABLE


def test_the_precedence_variant_only_rescues_titles_matching_both():
    # Structural. Pharmacokinetic is the only refusing class, so moving efficacy ahead of
    # it can change a verdict ONLY for a title that matched both patterns, and must change
    # nothing else. Asserted both ways so a variant with wider effects is caught.
    # The predicate is "baseline resolved to pharmacokinetic AND something else matched",
    # not "matched efficacy too". Moving pharmacokinetic to LAST in the order means it
    # loses to every other class, and the classes it now loses to all allow -- so
    # "Safety, Tolerability and Pharmacokinetics of Multiple Ascending Doses" moves via
    # safety_tolerability, with no efficacy pattern involved. A narrower predicate looks
    # right and is wrong in a way only the corpus reveals.
    for title in TITLES:
        matched = set(matched_classes(title))
        rescuable = (resolve_class(matched) == CLASS_PHARMACOKINETIC
                     and matched != {CLASS_PHARMACOKINETIC})
        base = outcome_gate(title)
        candidate = variant_gates([title], VARIANT_EFFICACY_OVER_PK)[0]
        if rescuable:
            assert base == GATE_NOT_APPLICABLE, title
            assert candidate == GATE_APPLICABLE, title
        else:
            assert candidate == base, title


def test_no_variant_ever_moves_an_endpoint_into_undeterminable():
    # undeterminable means "the rule could not read the text", which is a property of the
    # PATTERNS. A resolution-step change that produced it would be reporting a pattern
    # failure it did not cause.
    for variant in VARIANT_ORDER:
        for before, after in reclassified(TITLES, variant)["moves"]:
            assert after != GATE_UNDETERMINABLE, variant


def test_the_baseline_moves_nothing():
    report = reclassified(TITLES, VARIANT_BASELINE)
    assert report["moved"] == 0
    assert report["moves"] == {}
    assert moved_share(report) == 0.0


def test_the_ceiling_variant_moves_at_least_as_much_as_the_precedence_one():
    # Flipping the only refusing class rescues every endpoint the precedence change
    # rescues, and generally more. Closed-form monotonicity, not a measured figure, and it
    # is what makes VARIANT_PK_APPLICABLE a ceiling rather than another candidate.
    ceiling = reclassified(TITLES, VARIANT_PK_APPLICABLE)["moved"]
    assert ceiling >= reclassified(TITLES, VARIANT_EFFICACY_OVER_PK)["moved"]


def test_no_variant_moves_an_endpoint_into_a_refusal():
    # Every live variant only ever turns a refusal into an allowance. A variant that
    # created refusals would be a different kind of proposal and would need its own
    # argument.
    for variant in VARIANT_ORDER:
        for _, after in reclassified(TITLES, variant)["moves"]:
            assert after != GATE_NOT_APPLICABLE, variant


def test_move_counts_never_exceed_the_corpus():
    for variant in VARIANT_ORDER:
        report = reclassified(TITLES, variant)
        assert report["moved"] <= report["total"] == len(TITLES)
        assert sum(report["moves"].values()) == report["moved"]


def test_moved_share_is_none_on_an_empty_corpus():
    # None, not 0.0: "no endpoints to move" is not "moved none of them".
    assert moved_share(reclassified([], VARIANT_PK_APPLICABLE)) is None


def test_reclassified_raises_on_an_unknown_variant():
    try:
        reclassified(TITLES, "not_a_variant")
    except ValueError:
        return
    raise AssertionError("reclassified should raise on an unregistered variant")


# ---- the stated limits -----------------------------------------------------
def test_the_unprobeable_list_is_not_empty():
    # The pattern-level defect is recorded so its absence from the variant table is not
    # read as its being fine. An empty list here would mean somebody removed the note.
    assert UNPROBEABLE
    assert all(isinstance(entry, str) and entry for entry in UNPROBEABLE)