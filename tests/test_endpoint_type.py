"""Tests for the endpoint-type classifier. Plain asserts so tests/_run_stdlib.py works.

NO TRANSCRIBED EXPECTED VALUES. Every expectation is derived from a closed form, an
independent computation, a structural property, or the constant depended on. In particular
nothing here asserts a share of the corpus (no "63% of phase 1 is pharmacokinetic"): that
figure is the thing the hand labels exist to measure, and a test that pinned it would
certify whatever the rule currently happens to do.

The test corpus below is drawn from the SHAPES that appear in AACT primary outcome text,
not from convenient strings. Several real bugs in this project survived synthetic fixtures,
and the two shapes that broke earlier attempts at this rule -- "dose-limiting toxicity"
(dose selection wearing safety words) and "viral clearance" (efficacy wearing a
pharmacokinetic word) -- are both in here on purpose.
"""
from __future__ import annotations

from trial_pos.services.endpoint_label import (
    HEADLINE_TIERS, TIER_A, TIER_D, label_row,
)
from trial_pos.services.endpoint_type import (
    CLASS_BIOEQUIVALENCE, CLASS_DOC, CLASS_DOSE_FINDING, CLASS_EFFICACY, CLASS_GATE,
    CLASS_OTHER, CLASS_PHARMACOKINETIC, CLASS_PRECEDENCE, CLASS_SAFETY, CLASS_UNCLEAR,
    DEFAULT_GATE_ROLLUP, ENDPOINT_CLASSES, GATE_APPLICABLE, GATE_DOC,
    GATE_KAPPA_MINIMUM, GATE_NOT_APPLICABLE, GATE_ONLY_FIELDS, GATE_REASONS,
    GATE_ROLLUPS, GATE_UNDETERMINABLE, GATE_VERDICTS, LABELLER_CLASSES,
    LABELLER_CLASS_DOC,
    MECHANISM_WORD, REASON_ALL_TESTABLE, REASON_ALL_UNTESTABLE, REASON_DOC,
    REASON_MIXED_UNDER_ALL, REASON_NO_ENDPOINT_TEXT, REASON_TESTABLE, REASON_UNREADABLE,
    REPORTABLE_KAPPA_MINIMUM, ROLLUP_ALL, ROLLUP_ANY, class_coverage, classify_title,
    classify_titles, collapse_to_gate, decline_sentence, endpoint_clause,
    gate_coverage, gate_for_class, gate_record_fields, gate_versus_tier, matched_classes,
    multi_match_rate, refuses_estimate, trial_gate, trial_gate_from_titles,
)
from trial_pos.services.fdaaa import compose_flag

# Realistic primary outcome text, one entry per shape that matters. The expected class is
# the one a reader of CLASS_DOC would assign; where a title matches two patterns the
# comment names which precedence step is being exercised.
CORPUS = (
    # pharmacokinetic
    ("Area Under the Plasma Concentration-Time Curve (AUC) of Drug X",
     CLASS_PHARMACOKINETIC),
    ("Maximum Observed Plasma Concentration (Cmax)", CLASS_PHARMACOKINETIC),
    ("Terminal Half-life (t1/2) of the Study Drug", CLASS_PHARMACOKINETIC),
    ("Apparent Clearance (CL/F) of Compound A", CLASS_PHARMACOKINETIC),
    # precedence: pharmacokinetic beats safety, because 'safety' is a generic word and
    # the PK terms are specific
    ("Safety, Tolerability and Pharmacokinetics of Multiple Ascending Doses",
     CLASS_PHARMACOKINETIC),
    # dose-finding
    ("Maximum Tolerated Dose (MTD) of Drug X", CLASS_DOSE_FINDING),
    ("Recommended Phase 2 Dose", CLASS_DOSE_FINDING),
    # precedence: dose-finding beats safety. The endpoint is the dose, not the toxicity.
    ("Number of Participants With Dose-Limiting Toxicities (DLTs)", CLASS_DOSE_FINDING),
    # bioequivalence: precedence over pharmacokinetic, and it earns its own sentence
    ("Bioequivalence of Test and Reference Formulations", CLASS_BIOEQUIVALENCE),
    ("Cmax of Test Product Versus Reference Product", CLASS_BIOEQUIVALENCE),
    # safety
    ("Number of Participants With Treatment-Emergent Adverse Events",
     CLASS_SAFETY),
    ("Incidence of Serious Adverse Events", CLASS_SAFETY),
    # efficacy-shaped
    ("Overall Survival", CLASS_EFFICACY),
    ("Progression-Free Survival (PFS)", CLASS_EFFICACY),
    ("Objective Response Rate (ORR)", CLASS_EFFICACY),
    ("Change From Baseline in HbA1c at Week 24", CLASS_EFFICACY),
    ("Proportion of Participants Achieving Sustained Virologic Response",
     CLASS_EFFICACY),
    # the disambiguation that broke an earlier pattern: 'clearance' in an efficacy sense
    ("Viral Clearance at Day 14", CLASS_EFFICACY),
    ("Mean Change in MADRS Total Score", CLASS_EFFICACY),
    # other: nothing matched
    ("Feasibility of the Manufacturing Process", CLASS_OTHER),
    ("Number of Participants Enrolled", CLASS_OTHER),
)


# ---- vocabulary and structure --------------------------------------------
def test_every_class_is_documented():
    for cls in LABELLER_CLASSES:
        assert cls in CLASS_DOC and CLASS_DOC[cls].strip()


def test_every_class_has_labeller_facing_wording_too():
    # CLASS_DOC explains the rule and the gate, which is the wrong frame for the person
    # labelling: a labeller told that `other` means "no pattern matched" is being asked to
    # guess what the regex did, which would make the reference a copy of the candidate
    for cls in LABELLER_CLASSES:
        assert cls in LABELLER_CLASS_DOC and LABELLER_CLASS_DOC[cls].strip()
        assert LABELLER_CLASS_DOC[cls] != CLASS_DOC[cls]


def test_every_class_has_a_deliberate_gate_verdict():
    # a class with no gate mapping is a class somebody added without deciding what the
    # tool should do about it
    for cls in ENDPOINT_CLASSES:
        assert CLASS_GATE[cls] in GATE_VERDICTS


def test_gate_for_class_raises_rather_than_defaulting():
    raised = False
    try:
        gate_for_class("a_class_nobody_declared")
    except ValueError:
        raised = True
    assert raised


def test_precedence_covers_every_class_except_the_fallthrough():
    # CLASS_OTHER is what remains when nothing matched, so it must NOT be in the order
    assert set(CLASS_PRECEDENCE) == set(ENDPOINT_CLASSES) - {CLASS_OTHER}
    assert len(CLASS_PRECEDENCE) == len(set(CLASS_PRECEDENCE))


def test_labeller_vocabulary_is_the_classifier_vocabulary_plus_unclear():
    assert set(LABELLER_CLASSES) == set(ENDPOINT_CLASSES) | {CLASS_UNCLEAR}


def test_classifier_never_emits_the_labeller_only_class():
    # unclear exists so a human can decline; a rule that could emit it would be declining
    # on the user's behalf without measuring anything
    for title, _ in CORPUS:
        assert classify_title(title) != CLASS_UNCLEAR
    assert classify_title("") != CLASS_UNCLEAR


def test_every_verdict_and_reason_is_documented():
    for verdict in GATE_VERDICTS:
        assert GATE_DOC[verdict].strip()
    for reason in GATE_REASONS:
        assert REASON_DOC[reason].strip()


def test_every_emitted_field_is_registered_as_gate_only():
    # R7: adding an output without registering it must fail here rather than turning up
    # in a feature matrix later
    record = trial_gate_from_titles(["Overall Survival"])
    assert set(gate_record_fields(record)) == set(GATE_ONLY_FIELDS)


def test_kappa_thresholds_are_ordered_and_pre_registered():
    assert GATE_KAPPA_MINIMUM > REPORTABLE_KAPPA_MINIMUM > 0.0


# ---- the coupling to the label's own multi-endpoint reading ---------------
def _two_outcome_trial(p_first, p_second):
    """A trial with two primary outcomes, each a superiority analysis with a p-value."""
    return {"nct_id": "NCT00000001"}, {
        "o1": [{"p_value": p_first, "non_inferiority_type": "SUPERIORITY_OR_OTHER"}],
        "o2": [{"p_value": p_second, "non_inferiority_type": "SUPERIORITY_OR_OTHER"}],
    }


def test_default_rollup_matches_the_label_rollup():
    """The gate's default roll-up must be the one the label actually uses.

    Derived from `label_row`'s BEHAVIOUR rather than from a constant: a trial with one
    endpoint met and one missed gets a strict label of met, which is the ANY reading. If
    section 8.4 moves the label to ALL, this test fails and the gate default has to move
    with it -- otherwise the tool would refuse trials whose labels it computed in training.
    """
    trial, outcomes = _two_outcome_trial("0.01", "0.90")
    record = label_row(trial, outcomes)
    label_is_any = record["endpoint_met_strict"] == 1
    assert label_is_any, "label_row no longer uses the ANY reading; see section 8.4"
    assert DEFAULT_GATE_ROLLUP == ROLLUP_ANY


# ---- classification ------------------------------------------------------
def test_every_corpus_title_gets_its_expected_class():
    for title, expected in CORPUS:
        assert classify_title(title) == expected, title


def test_classify_agrees_with_the_first_match_in_precedence_order():
    # consistency property: the class IS the highest-precedence match, always
    for title, _ in CORPUS:
        hits = matched_classes(title)
        assert classify_title(title) == (hits[0] if hits else CLASS_OTHER)


def test_matched_classes_is_returned_in_precedence_order():
    for title, _ in CORPUS:
        hits = matched_classes(title)
        positions = [CLASS_PRECEDENCE.index(c) for c in hits]
        assert positions == sorted(positions)


def test_the_precedence_order_is_actually_exercised_by_the_corpus():
    # a corpus where nothing matches twice would test the patterns and not the order, and
    # the order is the part that is a judgment
    assert any(len(matched_classes(title)) > 1 for title, _ in CORPUS)


def test_absent_text_is_other_rather_than_an_exception():
    # an absent registered measure is a real state; it must travel as "cannot read" rather
    # than as an error the caller has to remember to handle
    for empty in (None, "", "   ", "\n\t"):
        assert classify_title(empty) == CLASS_OTHER
        assert matched_classes(empty) == ()


def test_whitespace_does_not_change_the_class():
    for title, expected in CORPUS:
        mangled = "  " + title.replace(" ", "\n  ") + "  "
        assert classify_title(mangled) == expected, title


def test_case_does_not_change_the_class():
    for title, expected in CORPUS:
        assert classify_title(title.upper()) == expected, title
        assert classify_title(title.lower()) == expected, title


def test_acronyms_are_matched_on_word_boundaries():
    # 'PKU' is not 'PK'; 'AER' is not 'AE'. Without boundaries the rule would claim a
    # large slice of metabolic and respiratory trials
    assert classify_title("Plasma Phenylalanine in PKU Patients") != CLASS_PHARMACOKINETIC
    assert classify_title("Change in Auckland Frailty Score") != CLASS_PHARMACOKINETIC


# ---- the gate ------------------------------------------------------------
def test_single_outcome_trial_takes_its_class_gate_directly():
    # property over the whole vocabulary rather than one example
    for cls in ENDPOINT_CLASSES:
        for rollup in GATE_ROLLUPS:
            assert trial_gate([cls], rollup)["gate"] == gate_for_class(cls)


def test_all_untestable_is_refused_under_either_rollup():
    classes = [CLASS_PHARMACOKINETIC, CLASS_DOSE_FINDING]
    for rollup in GATE_ROLLUPS:
        record = trial_gate(classes, rollup)
        assert record["gate"] == GATE_NOT_APPLICABLE
        assert record["reason"] == REASON_ALL_UNTESTABLE


def test_all_testable_is_allowed_under_either_rollup():
    classes = [CLASS_EFFICACY, CLASS_EFFICACY]
    for rollup in GATE_ROLLUPS:
        record = trial_gate(classes, rollup)
        assert record["gate"] == GATE_APPLICABLE
        assert record["reason"] == REASON_ALL_TESTABLE


def test_mixed_trial_splits_on_the_rollup():
    classes = [CLASS_PHARMACOKINETIC, CLASS_PHARMACOKINETIC, CLASS_EFFICACY]
    under_any = trial_gate(classes, ROLLUP_ANY)
    under_all = trial_gate(classes, ROLLUP_ALL)
    assert under_any["gate"] == GATE_APPLICABLE
    assert under_any["reason"] == REASON_TESTABLE
    assert under_all["gate"] == GATE_NOT_APPLICABLE
    assert under_all["reason"] == REASON_MIXED_UNDER_ALL
    assert under_any["mixed"] is under_all["mixed"] is True


def test_unreadable_text_is_undeterminable_and_not_a_refusal():
    # the tri-state rule, and the reason CLASS_OTHER does not map to not_applicable: the
    # tool must not tell a user the question does not apply because a regex missed
    for rollup in GATE_ROLLUPS:
        record = trial_gate([CLASS_OTHER, CLASS_OTHER], rollup)
        assert record["gate"] == GATE_UNDETERMINABLE
        assert record["reason"] == REASON_UNREADABLE


def test_no_primary_outcomes_is_undeterminable_with_its_own_reason():
    # distinguished from "text that matched nothing", because they are different facts
    record = trial_gate([])
    assert record["gate"] == GATE_UNDETERMINABLE
    assert record["reason"] == REASON_NO_ENDPOINT_TEXT
    assert record["n_primary"] == 0


def test_unreadable_plus_untestable_refuses_under_any():
    # one endpoint read as a value-reporting measurement, one unreadable: ANY has no
    # testable endpoint to point at, so the refusal stands on the one it could read
    record = trial_gate([CLASS_PHARMACOKINETIC, CLASS_OTHER], ROLLUP_ANY)
    assert record["gate"] == GATE_NOT_APPLICABLE


def test_counts_partition_the_primary_outcomes():
    classes = [CLASS_EFFICACY, CLASS_PHARMACOKINETIC, CLASS_OTHER, CLASS_SAFETY]
    record = trial_gate(classes)
    assert (record["n_testable"] + record["n_untestable"] + record["n_unreadable"]
            == record["n_primary"] == len(classes))


def test_an_unknown_rollup_raises():
    raised = False
    try:
        trial_gate([CLASS_EFFICACY], "either")
    except ValueError:
        raised = True
    assert raised


def test_the_binary_collapse_refuses_exactly_the_not_applicable_classes():
    for cls in ENDPOINT_CLASSES:
        assert collapse_to_gate(cls) is (gate_for_class(cls) == GATE_NOT_APPLICABLE)
    assert refuses_estimate(GATE_UNDETERMINABLE) is False
    assert refuses_estimate(GATE_APPLICABLE) is False


def test_end_to_end_from_titles_matches_classifying_then_rolling_up():
    titles = [title for title, _ in CORPUS[:5]]
    assert (trial_gate_from_titles(titles)
            == trial_gate(classify_titles(titles), DEFAULT_GATE_ROLLUP))


# ---- what the user reads -------------------------------------------------
def test_no_decline_sentence_when_a_number_is_shown():
    for classes in ([CLASS_EFFICACY], [CLASS_EFFICACY, CLASS_PHARMACOKINETIC],
                    [CLASS_OTHER]):
        record = trial_gate(classes, ROLLUP_ANY)
        if record["gate"] != GATE_NOT_APPLICABLE:
            assert decline_sentence(record) is None


def test_mechanism_words_cover_exactly_the_refusing_classes():
    # Structural, so the two cannot drift: every class that refuses needs a word or its
    # decline sentence is generic hedging, and a class that does NOT refuse must not carry
    # one or the entry is a sentence nothing can produce. Bioequivalence moved from the
    # first group to the second when it became applicable.
    refusing = {c for c, gate in CLASS_GATE.items() if gate == GATE_NOT_APPLICABLE}
    assert set(MECHANISM_WORD) == refusing


def test_decline_sentence_names_the_mechanism_for_each_refused_class():
    for cls, word in MECHANISM_WORD.items():
        record = trial_gate([cls])
        sentence = decline_sentence(record)
        assert sentence is not None
        # the sentence must be about this mechanism, not generic hedging
        assert word.split()[0].lower() in sentence.lower(), cls


def test_undeterminable_produces_no_sentence_in_either_direction():
    # the whole point of the third state: nothing is asserted about endpoint type
    record = trial_gate([CLASS_OTHER])
    assert decline_sentence(record) is None
    assert endpoint_clause(record) is None


def test_endpoint_clause_only_appears_for_a_mixed_trial():
    assert endpoint_clause(trial_gate([CLASS_EFFICACY])) is None
    assert endpoint_clause(trial_gate([CLASS_PHARMACOKINETIC])) is None
    mixed = trial_gate([CLASS_EFFICACY, CLASS_PHARMACOKINETIC], ROLLUP_ANY)
    assert endpoint_clause(mixed) is not None


def test_mixed_clause_counts_add_up_to_the_registered_total():
    classes = [CLASS_PHARMACOKINETIC, CLASS_PHARMACOKINETIC, CLASS_EFFICACY]
    record = trial_gate(classes, ROLLUP_ANY)
    clause = endpoint_clause(record)
    # every count the sentence quotes is derived from the record, so the arithmetic in the
    # sentence is checkable against the record rather than read
    assert str(record["n_primary"]) in clause
    assert str(record["n_untestable"]) in clause
    assert str(record["n_testable"]) in clause
    assert record["n_untestable"] + record["n_testable"] == record["n_primary"]


def test_mixed_clause_agrees_in_number():
    one_each = trial_gate([CLASS_PHARMACOKINETIC, CLASS_EFFICACY], ROLLUP_ANY)
    assert " is " in endpoint_clause(one_each)
    assert " was " in endpoint_clause(one_each)
    two_each = trial_gate([CLASS_PHARMACOKINETIC] * 2 + [CLASS_EFFICACY] * 2, ROLLUP_ANY)
    assert " are " in endpoint_clause(two_each)
    assert " were " in endpoint_clause(two_each)


def test_the_mechanism_word_is_chosen_by_precedence_not_by_count():
    # so the sentence a user reads does not change because one more safety endpoint was
    # registered. Deterministic beats representative for an explanation.
    classes = [CLASS_SAFETY, CLASS_SAFETY, CLASS_SAFETY, CLASS_DOSE_FINDING]
    record = trial_gate(classes)
    sentence = decline_sentence(record)
    earlier = min(CLASS_PRECEDENCE.index(c) for c in set(classes))
    expected = MECHANISM_WORD[CLASS_PRECEDENCE[earlier]]
    assert expected.split()[0].lower() in sentence.lower()


def test_the_clause_reaches_the_flag_through_the_existing_parameter():
    # fdaaa.compose_flag is UNCHANGED: it already took the endpoint clause as an argument,
    # and this is the module that supplies one
    mixed = trial_gate([CLASS_PHARMACOKINETIC, CLASS_EFFICACY], ROLLUP_ANY)
    clause = endpoint_clause(mixed)
    flag = compose_flag({"verdict": GATE_NOT_APPLICABLE,
                         "reason": "phase_1_only_study"}, clause)
    assert clause in flag


def test_flag_without_a_clause_is_unchanged_from_the_flag_with_none():
    applicable = trial_gate([CLASS_EFFICACY])
    verdict = {"verdict": GATE_NOT_APPLICABLE, "reason": "phase_1_only_study"}
    assert compose_flag(verdict, endpoint_clause(applicable)) == compose_flag(verdict)


# ---- coverage aggregates -------------------------------------------------
def test_class_coverage_counts_partition_the_corpus():
    coverage = class_coverage([title for title, _ in CORPUS])
    assert sum(coverage["counts"].values()) == coverage["total"] == len(CORPUS)


def test_class_coverage_counts_match_classifying_each_title():
    titles = [title for title, _ in CORPUS]
    coverage = class_coverage(titles)
    for cls in ENDPOINT_CLASSES:
        independent = sum(1 for t in titles if classify_title(t) == cls)
        assert coverage["counts"][cls] == independent


def test_multi_match_rate_is_the_share_whose_class_the_order_decided():
    titles = [title for title, _ in CORPUS]
    coverage = class_coverage(titles)
    independent = sum(1 for t in titles if len(matched_classes(t)) > 1)
    assert coverage["multi_match"] == independent
    assert abs(multi_match_rate(coverage) - independent / len(titles)) < 1e-12


def test_multi_match_rate_of_nothing_is_none():
    assert multi_match_rate(class_coverage([])) is None


def test_empty_text_is_counted_apart_from_unmatched_text():
    coverage = class_coverage(["", "Feasibility of Recruitment"])
    assert coverage["empty_text"] == 1
    assert coverage["counts"][CLASS_OTHER] == 2


def test_gate_coverage_verdicts_partition_the_trials():
    records = [trial_gate([cls]) for cls in ENDPOINT_CLASSES]
    coverage = gate_coverage(records)
    assert sum(coverage["verdicts"].values()) == coverage["total"] == len(records)
    assert sum(coverage["reasons"].values()) == coverage["total"]


def test_gate_coverage_counts_mixed_trials_separately():
    # mixed trials get a different user-facing sentence, so they are a distinct product
    # state rather than a subset to be summarised away
    records = [trial_gate([CLASS_EFFICACY, CLASS_PHARMACOKINETIC], ROLLUP_ANY),
               trial_gate([CLASS_EFFICACY])]
    assert gate_coverage(records)["mixed"] == 1


def test_gate_versus_tier_cells_partition_the_input():
    rows = [(trial_gate([CLASS_PHARMACOKINETIC]), TIER_A),
            (trial_gate([CLASS_PHARMACOKINETIC]), TIER_D),
            (trial_gate([CLASS_EFFICACY]), TIER_A),
            (trial_gate([CLASS_OTHER]), TIER_D)]
    out = gate_versus_tier(rows, HEADLINE_TIERS)
    cells = sum(v for k, v in out.items() if k != "total")
    assert cells == out["total"] == len(rows)


def test_gate_versus_tier_isolates_the_informative_cell():
    # a trial the gate refuses which nonetheless carries a headline label is the only cell
    # that says something: the sponsor DID apply a threshold to that endpoint
    rows = [(trial_gate([CLASS_PHARMACOKINETIC]), TIER_A)]
    out = gate_versus_tier(rows, HEADLINE_TIERS)
    assert out["refused_but_labelled"] == 1
    assert out["refused_and_unlabelled"] == 0