"""Endpoint TYPE: does "did this trial meet its primary endpoint" even have an answer?

WHY THIS EXISTS
===============
The endpoint-met label is read from a trial's own posted primary ANALYSIS: a p-value
against an alpha, or an interval against a null. That presupposes the primary endpoint was
something a threshold was applied to. Much of early-phase research reports a VALUE instead
-- an AUC, a Cmax, a maximum tolerated dose -- and for those trials the question has no
answer to estimate, so a probability is not a cautious estimate, it is a category error.

This module supplies the endpoint half of the user-facing flag; `fdaaa.trial_flag` applies
it. It reached the flag once `GATING_CRITERIA` passed on hand labels
(`build_confirm_sample.py --score`). A keyword rule that decides what the tool refuses to
answer is a verdict-producing threshold like any other.

IT GATES PER TRIAL, NOT PER PHASE
=================================
Phase is a PROXY for endpoint type and it is a leaky one in both directions: a phase 1
trial with an efficacy-shaped primary endpoint is a legitimate target, and a phase 3 trial
with a pharmacokinetic primary endpoint is not, however late its phase. A phase-based gate
waves the second group through and refuses the first. Endpoint type is the variable that
actually decides whether the question applies, so the gate reads the endpoint.

THE INPUT IS REGISTERED TEXT, NOT RESULTS TEXT, AND THAT IS LOAD-BEARING
========================================================================
Two fields in AACT carry primary endpoint text:

  design_outcomes.measure   what the sponsor REGISTERED. Present for essentially every
                            trial, whatever it later posted. Mirrored by the CT.gov v2
                            API as primaryOutcomes[].measure, which is what the live tool
                            will read when somebody pastes an id.
  outcomes.title            what the sponsor posted WITH RESULTS. Exists only for the
                            75,434 trials that posted at all -- 12.2% of phase 1 drug
                            trials -- and is edited at posting time.

The classifier is built on the FIRST. A gate trained on results-side titles could not be
evaluated on, or deployed against, the trials it exists to serve: a phase 1 trial with no
posted results is exactly the case the flag is for, and it has no results-side title at
all. That the two fields are similar is not enough -- the deployed field and the validated
field have to be the same field.

`--titles` on the sample builder accepts either file so the two can be COMPARED on the
overlap, which is a real agreement check and not a substitute for this one.

WHAT MUST NOT HAPPEN TO THESE OUTPUTS (R7)
==========================================
`GATE_ONLY_FIELDS` names every field this module emits, and all of them are GATE-ONLY: they
may decide whether an endpoint estimate is shown, and they may never enter the endpoint-met
feature matrix. Two reasons, and the second is the one that bites later:

  1. the class is derived from the same primary-outcome text the label's analysis rows hang
     off, so it sits adjacent to the label rather than upstream of it;
  2. the trial-level gate uses the SAME any/all roll-up as the label itself (see
     `DEFAULT_GATE_ROLLUP`). A feature that shares a roll-up with its label is one revision
     away from being a label input, and the revision would not look like one.

When the per-target leakage registry in section 1.1 of the handoff is built, these fields
go in as endpoint-met exclusions. `test_every_emitted_field_is_registered` fails if a new
output is added without registering it.

THE CLASS ORDER IS A JUDGMENT AND IT IS COUNTED
===============================================
A title can match more than one class -- "Safety, tolerability and pharmacokinetics of X"
matches two, "Number of Participants With Dose-Limiting Toxicities" matches dose-finding
and safety. `CLASS_PRECEDENCE` resolves those, and the resolution is a reading rather than
a fact, in the same way the PHASE1/PHASE2 reading in `fdaaa.py` is. So `classify_title`
also returns every class that MATCHED, `multi_match_rate` counts how often the order had
to decide anything, and the hand-labelling sample is stratified on the class the order
PRODUCED -- which is what puts the order's mistakes in front of a human instead of leaving
them to be assumed away.

Ordering rationale, each step:
  bioequivalence before pharmacokinetic  a bioequivalence endpoint IS a pharmacokinetic
                                         measurement; what distinguishes it is the
                                         comparison, and it gets its own refusal sentence
  dose_finding before safety             "dose-limiting toxicity" is a dose-selection
                                         endpoint that mentions toxicity; the intent is the
                                         dose, not the safety profile
  pharmacokinetic before safety          "safety and PK" studies: the PK terms are specific
                                         (AUC, Cmax) while "safety" is a generic word that
                                         appears in half of all early-phase titles
  safety before efficacy_shaped          "Number of participants with adverse events"
                                         matches the efficacy count-of-participants shape;
                                         adverse events are the more specific signal
  efficacy_shaped last                   its patterns are the broadest, so anything more
                                         specific should have claimed the title already
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# ---- the classes ----------------------------------------------------------
CLASS_BIOEQUIVALENCE = "bioequivalence"
CLASS_DOSE_FINDING = "dose_finding"
CLASS_PHARMACOKINETIC = "pharmacokinetic"
CLASS_SAFETY = "safety_tolerability"
CLASS_EFFICACY = "efficacy_shaped"
CLASS_OTHER = "other"

# Labeller-only. The classifier NEVER emits it, and the scoring step maps it to None so it
# lands in `n_incomparable` rather than in a cell. Forcing a choice on a genuinely
# ambiguous title is how a labelling sample acquires noise that then gets attributed to the
# rule; an explicit escape hatch keeps that noise countable and out of the marginals.
CLASS_UNCLEAR = "unclear"

ENDPOINT_CLASSES = (CLASS_BIOEQUIVALENCE, CLASS_DOSE_FINDING, CLASS_PHARMACOKINETIC,
                    CLASS_SAFETY, CLASS_EFFICACY, CLASS_OTHER)
LABELLER_CLASSES = ENDPOINT_CLASSES + (CLASS_UNCLEAR,)

CLASS_DOC = {
    CLASS_BIOEQUIVALENCE: (
        "a formulation-comparison endpoint: the test product's exposure against a "
        "reference product's. It IS tested against a threshold, but the threshold is "
        "containment within conventional bounds rather than exclusion of a null, and the "
        "handoff rejects implementing that rule (the bounds move for highly variable and "
        "narrow-therapeutic-index drugs and AACT does not record which regime applied). "
        "Separate from pharmacokinetic because it earns its own refusal sentence"),
    CLASS_DOSE_FINDING: (
        "the endpoint is a dose selected from the data -- maximum tolerated dose, "
        "recommended phase 2 dose, dose-limiting toxicity rate used to pick one. The "
        "result is a dose, not a verdict on a hypothesis"),
    CLASS_PHARMACOKINETIC: (
        "the endpoint is a concentration-derived quantity: AUC, Cmax, half-life, "
        "clearance. Reported as a value with a confidence interval around itself, not "
        "tested against a threshold"),
    CLASS_SAFETY: (
        "the endpoint is a count or rate of adverse events, toxicity or tolerability. "
        "Usually descriptive; where it IS compared between arms the trial's own posted "
        "analysis will carry the comparison and the label reads it directly"),
    CLASS_EFFICACY: (
        "the endpoint is a between-group comparison of a clinical or surrogate outcome -- "
        "survival, response, change from baseline, event incidence, a scored scale. This "
        "is the shape the endpoint-met label was built for"),
    CLASS_OTHER: (
        "no pattern matched, or the endpoint is something else entirely (feasibility, "
        "recruitment, adherence, process). NOT the same as 'not applicable': the rule "
        "cannot tell, so the gate returns undeterminable and the flag says nothing about "
        "endpoint type rather than asserting the question does not apply"),
    CLASS_UNCLEAR: (
        "labeller-only: the title does not carry enough to decide. Excluded from kappa as "
        "incomparable, and counted, because an ambiguous item is not a disagreement"),
}

# What a LABELLER is told each class means. Separate from CLASS_DOC on purpose: CLASS_DOC
# explains the rule and the gate, which is the wrong frame for the person labelling. A
# labeller told that `other` means "no pattern matched" is being asked to guess what the
# regex did, which would make the reference a copy of the candidate and the agreement
# figure meaningless. These say what the ENDPOINT is, and nothing about the rule.
LABELLER_CLASS_DOC = {
    CLASS_BIOEQUIVALENCE: (
        "the endpoint compares a test formulation against a reference one -- same drug, "
        "different product. Usually an exposure measure with 'test' and 'reference' in "
        "the text."),
    CLASS_DOSE_FINDING: (
        "the endpoint exists to pick a dose: maximum tolerated dose, recommended phase 2 "
        "dose, dose-limiting toxicities counted in order to choose one."),
    CLASS_PHARMACOKINETIC: (
        "the endpoint is how much drug is in the body and for how long: AUC, Cmax, "
        "half-life, clearance, plasma or serum concentration."),
    CLASS_SAFETY: (
        "the endpoint counts adverse events, toxicities or tolerability -- a count or "
        "rate of things going wrong."),
    CLASS_EFFICACY: (
        "the endpoint measures whether the treatment worked, in a form that can pass or "
        "fail: survival, response rate, change from baseline, event incidence, a scored "
        "scale."),
    CLASS_OTHER: (
        "the endpoint is genuinely something else -- feasibility, recruitment, "
        "adherence, a process or manufacturing measure. Use this when it is clearly none "
        "of the above, NOT when you cannot tell."),
    CLASS_UNCLEAR: (
        "you cannot tell from the text in front of you. Use it rather than guessing: "
        "these rows are counted and set aside, which is the honest handling."),
}

# Resolution order for a title matching more than one class. A READING, not a fact; see the
# module docstring for the rationale on each step and `multi_match_rate` for how often it
# decides anything. CLASS_OTHER is not here: it is what remains when nothing matched.
CLASS_PRECEDENCE = (CLASS_BIOEQUIVALENCE, CLASS_DOSE_FINDING, CLASS_PHARMACOKINETIC,
                    CLASS_SAFETY, CLASS_EFFICACY)

# ---- the gate -------------------------------------------------------------
# Tri-state, like every other verdict in this project. "undeterminable" is not folded into
# "not applicable": the first says the rule could not read the endpoint, the second says
# the rule read it and the question does not apply. Collapsing them would let the tool tell
# a user "this question does not apply to your trial" on the strength of a regex that
# simply failed to match (lesson 47, and the tri-state rule in section 10).
GATE_APPLICABLE = "applicable"
GATE_NOT_APPLICABLE = "not_applicable"
GATE_UNDETERMINABLE = "undeterminable"

GATE_VERDICTS = (GATE_APPLICABLE, GATE_NOT_APPLICABLE, GATE_UNDETERMINABLE)

GATE_DOC = {
    GATE_APPLICABLE: ("at least one primary endpoint is the shape the endpoint-met label "
                      "was built for, so the estimate has something to be about"),
    GATE_NOT_APPLICABLE: ("the endpoints read leave nothing threshold-tested to estimate: "
                          "under ANY every primary is value-reporting, under ALL at least "
                          "one is. The tool DECLINES and says which mechanism"),
    GATE_UNDETERMINABLE: ("the rule could not read some or all of the endpoint text. The "
                          "estimate is shown; the endpoint clause is SILENT unless some "
                          "primaries WERE read as value-reporting, and then says so"),
}

CLASS_GATE = {
    # ALLOWED, changed from not_applicable. A bioequivalence study DOES have a pass/fail
    # primary endpoint -- its interval either sits inside the acceptance window or it does
    # not -- so "did this trial meet its primary endpoint" is a meaningful question here
    # and refusing it at the gate was answering the wrong objection. What the project
    # cannot do is COMPUTE that answer, and that belongs one layer down: a ratio of
    # geometric means now refuses to tier E in endpoint_label, per analysis, with the
    # reason recorded. A refusal at the label layer is per-row and recoverable if the
    # acceptance window ever becomes readable; a refusal here was per-trial and permanent.
    #
    # It was also barely doing anything: measured on the posted corpus, this class caught
    # 6 of 862 bioequivalence-shaped analysis rows, because the gate reads registered TEXT
    # while the analysis shape lives in the posted STATISTICS -- two fields that need not
    # agree. The 856 it missed were getting inverted tier-C labels, which is the failure a
    # text-layer mask cannot prevent and a statistic-layer refusal does.
    CLASS_BIOEQUIVALENCE: GATE_APPLICABLE,
    # FLIPPED 2026-10-01 from not_applicable, on the confirming sample recorded beside
    # CANDIDATE_APPLICABLE_FLIPS: shipped mapping false-refusal 84.1% against a
    # pre-registered ceiling of 25%, candidate 18.2%. Allowed WITH A MANDATORY CLAUSE --
    # see QUALIFIED_CLASSES -- never as a bare number.
    CLASS_DOSE_FINDING: GATE_APPLICABLE,
    # NOT flipped, and the only class still refusing. The hand labels called it answerless
    # 8 of 8 in the ratification and 15 of 17 in the confirming sample, on disjoint
    # endpoints. That replication is the strongest single finding in the exercise.
    CLASS_PHARMACOKINETIC: GATE_NOT_APPLICABLE,
    CLASS_SAFETY: GATE_APPLICABLE,
    CLASS_EFFICACY: GATE_APPLICABLE,
    CLASS_OTHER: GATE_UNDETERMINABLE,
}

# ---- roll-up: per-outcome classes -> one per-trial verdict -----------------
# A trial can register several primary outcomes (36% of drug trials that posted primaries
# have two or more), so the per-outcome class has to become one per-trial verdict, and the
# choice is the SAME choice section 8.4 has outstanding for the label itself.
#
# `endpoint_met_strict` is currently set from `any_primary_met`, so the default here is ANY
# and `test_default_rollup_matches_the_label_rollup` fails if the two ever diverge. If 8.4
# moves the label to `all_primary_met`, this moves with it -- otherwise the tool refuses
# trials whose labels it happily computed in training, or returns a number claiming every
# endpoint was met for a trial where two of them were never tested.
ROLLUP_ANY = "any"
ROLLUP_ALL = "all"
GATE_ROLLUPS = (ROLLUP_ANY, ROLLUP_ALL)
DEFAULT_GATE_ROLLUP = ROLLUP_ANY

ROLLUP_DOC = {
    ROLLUP_ANY: ("applicable when AT LEAST ONE primary endpoint is threshold-testable. "
                 "Matches a label defined as any_primary_met"),
    ROLLUP_ALL: ("applicable only when EVERY primary endpoint is threshold-testable. "
                 "Matches a label defined as all_primary_met"),
}

# Reasons, countable. A refusal that is not counted vanishes into the population.
REASON_NO_ENDPOINT_TEXT = "no_primary_endpoint_text"
REASON_ALL_UNTESTABLE = "no_primary_endpoint_is_threshold_tested"
REASON_MIXED_UNDER_ALL = "some_primary_endpoints_are_not_threshold_tested"
REASON_UNREADABLE = "endpoint_text_not_classifiable"
REASON_UNTESTABLE_BESIDE_UNREADABLE = "untestable_primaries_beside_unclassifiable_ones"
REASON_TESTABLE = "at_least_one_primary_endpoint_is_threshold_tested"
REASON_ALL_TESTABLE = "every_primary_endpoint_is_threshold_tested"

GATE_REASONS = (REASON_NO_ENDPOINT_TEXT, REASON_ALL_UNTESTABLE, REASON_MIXED_UNDER_ALL,
                REASON_UNREADABLE, REASON_UNTESTABLE_BESIDE_UNREADABLE, REASON_TESTABLE,
                REASON_ALL_TESTABLE)

REASON_DOC = {
    REASON_NO_ENDPOINT_TEXT: ("the trial registered no primary outcome text this module "
                              "could read. UNDETERMINABLE, not 'not applicable'"),
    REASON_ALL_UNTESTABLE: ("every primary endpoint is a value-reporting measurement. The "
                            "question has no answer to estimate"),
    REASON_MIXED_UNDER_ALL: ("under the ALL roll-up a trial-level verdict needs every "
                             "primary endpoint tested, and at least one is not"),
    REASON_UNREADABLE: ("no pattern matched any primary endpoint, so the rule has no "
                        "reading to offer either way"),
    REASON_UNTESTABLE_BESIDE_UNREADABLE: (
        "ANY only: some primaries read as value-reporting, none read as testable, and at "
        "least one could not be classified. The unread one may be the tested endpoint, so "
        "this is UNDETERMINABLE with a clause, not a refusal"),
    REASON_TESTABLE: "at least one primary endpoint is the shape the label was built for",
    REASON_ALL_TESTABLE: "every primary endpoint is the shape the label was built for",
}

# ---- kappa thresholds, pre-registered -------------------------------------
# Stated BEFORE any hand label exists, because a number that arrives without a criterion
# gets rationalised into acceptability -- the same discipline as the market resolution gate.
#
# The gating figure is kappa on the BINARY COLLAPSE (`refuses_estimate`), not the
# multi-class kappa: the decision that reaches a user is two-valued, and confusing
# pharmacokinetic with dose-finding is wrong about the class and right about the decision.
# Multi-class kappa is reported beside it and gates nothing.
#
# 0.60 sits at the bottom of the conventional "substantial" band, so it is not an invented
# number, and the alternative to a classifier at 0.60 is not a better classifier -- it is
# the current state, which is a phase proxy that is wrong for a measurable share of phase 3
# trials and a flag with a hole in it. Revisit DOWNWARD if the operator's own
# self-agreement on the re-labelled subset comes in below 0.70: a threshold above the human
# ceiling is unreachable by construction, and that ceiling is what
# `DUPLICATE_LABEL_COUNT` in the sampler exists to measure.
# DEMOTED 2026-10-01 from gating threshold to REPORTED DIAGNOSTIC. The value is unchanged
# and pinned by a test, because lowering a pre-registered number because the rule failed it
# is the move pre-registration exists to prevent -- and the ratification did fail it, at
# 0.439.
#
# The reason for the demotion is not that figure. It is that 0.60 was never connected to
# anything that ships. Nobody has shown that a gate at kappa 0.60 produces a better model
# than one at 0.45, or than no gate at all; the number was chosen as a plausible agreement
# level before any class prevalence was known. A threshold on an intermediate quantity,
# with no demonstrated link to the outcome, should not block shipping.
#
# This is a different act from lowering it, and the difference is that the argument does
# not depend on which way the number came out: it would hold identically if the
# ratification had scored 0.85. What gates instead is `GATING_CRITERIA` below; what
# measures the gate's VALUE rather than its correctness is the downstream prediction after
# that.
#
# Kept and still reported, because `validate_interval_rule` reports kappa throughout and
# comparability across the project's agreement measurements is worth keeping.
GATE_KAPPA_MINIMUM = 0.60
REPORTABLE_KAPPA_MINIMUM = 0.40

# A verdict banner on a handful of rows is a banner that gets ignored (lesson 29). Strata
# thinner than this are printed and excluded from any verdict.
MIN_STRATUM_FOR_VERDICT = 20

# ---- PRE-REGISTERED 2026-10-01, BEFORE THE CONFIRMING SAMPLE EXISTS -------
# Written down now because the hypothesis below was DERIVED FROM the 25 endpoints that
# suggested it, and a fix scored on the rows that produced it is not a measurement. These
# constants fix what would count as confirmation before the confirming data is collected.
#
# WHAT THE 25-ENDPOINT RATIFICATION FOUND (scheme_sheet.csv, one labeller, blind)
#   The six-class carving SURVIVED: the labeller reproduced five of the six classes in
#   their own words without having seen them. That is the one question section 12.7 says
#   no automated reference can answer, and it came back clean.
#   The CLASS-TO-GATE MAPPING did not. Per class, hand answer to "does 'did this trial
#   meet its primary endpoint' have an answer here":
#     pharmacokinetic      refuse   no x8                 mapping correct
#     efficacy_shaped      allow    yes x3                mapping correct
#     bioequivalence       allow    yes x2, unclear x1    mapping correct
#     other                allow    yes x2, unclear x1    mapping correct (undeterminable
#                                                         does not refuse)
#     dose_finding         REFUSE   yes x3                MAPPING WRONG
#     safety_tolerability  REFUSE   yes x4, no x1         MAPPING WRONG
#   Binary collapse: raw 0.696, kappa 0.439, and 7 over-refusals against 0 under-refusals
#   -- entirely one-directional, which section 12.5 disqualifies at any kappa.
#
# THE HYPOTHESIS UNDER TEST
#   Mapping CLASS_DOSE_FINDING and CLASS_SAFETY to GATE_APPLICABLE raises the binary kappa
#   above GATE_KAPPA_MINIMUM and removes the one-directional bias. On the 25 ratification
#   endpoints that change scores kappa 0.907 with 0 over-refusals and 1 under-refusal --
#   but those are the rows the hypothesis was read off, so that figure confirms nothing and
#   must never be quoted as validation.
#
# RESULT, 2026-10-01, on 86 presentations / 80 fresh endpoints, one labeller, blind,
# excluding by nct_id every trial seen in the ratification (confirm_sheet.csv):
#   mapping      n   raw     kappa   allow-prec  false-refl  over  under
#   shipped     74   0.284   0.053   100.0%      84.1%         53     0
#   candidate   74   0.959   0.834    98.4%      18.2%          2     1
#   per class: dose_finding 25/26 have an answer, safety 26/28, pharmacokinetic 2/17,
#   efficacy_shaped 11/15. Both TESTED classes cleared MIN_STRATUM_FOR_VERDICT; the two
#   controls did not and carry no verdict of their own. Self-consistency 7/7 repeated
#   pairs.
#   SHIPPED FAILS on false-refusal, CANDIDATE PASSES both bars. The flips are APPLIED
#   below as a result -- in the qualified form described next, which is what was
#   pre-registered, not a bare flip.
CANDIDATE_APPLICABLE_FLIPS = (CLASS_DOSE_FINDING, CLASS_SAFETY)

# AMENDED 2026-10-01, BEFORE SCORING, and the amendment makes the test HARDER rather than
# easier: the candidate mapping is applicable WITH A MANDATORY CLAUSE, not bare applicable.
#
# Why. The gate is binary -- show a number or do not -- and that is the wrong shape for
# what the hand labels found. A dose-escalation trial's "success" means a tolerable dose
# was identified; a comparative-safety trial's means the arms differed acceptably. Both are
# real answers, and neither is what a user reading a bare "68% chance of success" will
# assume it means. Silence misleads in one direction and an unqualified number in the
# other, so the third option is a number plus an accurate statement of what success meant.
#
# This is also what makes 50 hand labels ADEQUATE evidence. A bare flip would ask those
# rows to license silently showing thousands of users a number whose meaning nobody has
# stated. The qualified flip asks them to license showing a number together with its
# meaning, which is a far smaller claim and one 50 rows can carry. The serving half of the
# gate can never be validated against outcomes (SERVING_GATE_HAS_NO_OUTCOME_REFERENCE), so
# reducing the cost of being wrong is the only available risk control.
QUALIFIED_CLASSES = CANDIDATE_APPLICABLE_FLIPS

# RECORDED AS A HYPOTHESIS, NOT APPLIED. The 26 dose-finding rows split into two kinds that
# the single class conflates:
#   the ESTIMAND   "Maximum tolerated dose (MTD)", "Recommended phase 2 dose (RP2D)" -- the
#                  trial set out to produce a dose and either did or hit the stopping rule
#                  where every dose is too toxic and no MTD can be determined. Pass/fail.
#   the INPUT      "Number of participants with a Dose Limiting Toxicity (DLT)" -- the
#                  measurement that LOCATES the dose. The phase 1 target DLT rate is
#                  conventionally 20-33%, so zero DLTs is not success (escalation stopped
#                  too low) and 60% is not drug failure (that dose is above the MTD). There
#                  is no direction in which a DLT count is "met".
# The labeller answered yes to 9 of 10 input-type rows, which on their own criterion is
# arguably wrong. NOT acted on, because the split was derived from the same 10 rows that
# would test it -- the exact circularity this block exists to prevent. A fresh sample
# stratified on the distinction is what would settle it.
DOSE_FINDING_SPLIT_UNTESTED = ("mtd_or_rp2d_is_the_estimand",
                               "dlt_count_is_the_input")

# The decision bar is TWO asymmetric rates, not one symmetric kappa, because the two
# errors do not cost the same. A false ALLOWANCE puts a success probability on a trial for
# which "success" has no defined meaning -- an authoritative-looking number that is not
# wrong so much as meaningless, and nothing downstream can detect it. A false REFUSAL
# withholds an answer that existed, which costs coverage and is visible as a refusal. So
# allowance precision carries the tighter bar. Kappa cannot express this: it is symmetric.
#
# Both are computed against HAND LABELS, not against the sponsor-analysis reference. The
# ratification showed why that distinction is load-bearing: the sponsor reference read a
# posted p-value as "this endpoint was threshold-tested", and for pharmacokinetic
# endpoints that inference is wrong -- a sponsor can test whether exposure differs between
# arms without exposure being a success criterion. It named pharmacokinetic the largest
# over-refusal class; the hand labels say that mapping is correct 8 times out of 8. The
# sponsor reference therefore overstates over-refusal in a systematic direction and cannot
# be the bar.
#
# Values chosen to DISCRIMINATE between the shipped and candidate mappings rather than to
# be passed: on the ratification rows the shipped mapping scores allowance precision 1.00
# (passes) and false-refusal share 0.47 (fails), the candidate roughly 0.93 and 0.00
# (passes both). A bar that both clear, or neither, would not be a test.
# RATIFIED BY THE PROJECT OWNER 2026-10-01, before the confirming sample was drawn.
#
# These cannot be OPTIMISED from data, and a search for the "best" pair would be a category
# error: there is no outcome to optimise against, which is precisely why kappa was demoted
# above. What a threshold sweep CAN show is whether a bar discriminates between the two
# mappings, and that has been checked on the ratification rows. A sweep run after the
# confirming sample is labelled is a diagnostic only; selecting a threshold from it would
# convert a pre-registered test into a fitted one.
MIN_ALLOWANCE_PRECISION = 0.90
MAX_FALSE_REFUSAL_SHARE = 0.25

# What actually gates, named so no reader has to infer it from which constant happens to
# have "minimum" in it. BOTH must pass; they are not averaged, because averaging two
# asymmetric criteria discards the asymmetry that motivated having two of them.
GATING_CRITERIA = ("allowance_precision", "false_refusal_share")

# Sample size, from MIN_STRATUM_FOR_VERDICT rather than from an estimate. Section 12.9's
# indicative ~100 rows and the sampler's 200 default both FAIL to produce a per-stratum
# verdict: three phase groups x six classes is 18 strata, and 18 x 20 needs 360 rows. The
# ratification narrowed the question to two classes, so the confirming sample is scoped to
# them plus controls instead of spread thin across all eighteen:
#   25 x CLASS_DOSE_FINDING and 25 x CLASS_SAFETY   -- the two mappings under test
#   15 x CLASS_PHARMACOKINETIC and 15 x CLASS_EFFICACY -- controls, to catch under-refusals
#                                                        the flips introduce
# 80 rows clears MIN_STRATUM_FOR_VERDICT on both tested classes. It does NOT support a
# six-class kappa or any per-phase figure, and must not be reported as one.
CONFIRM_SAMPLE_PER_TESTED_CLASS = 25
CONFIRM_SAMPLE_PER_CONTROL_CLASS = 15

# ---- THE DOWNSTREAM TEST, pre-registered while no model exists -------------
# The criteria above test whether the gate is CORRECT. They cannot test whether it is WORTH
# HAVING, because agreement with a human is still an intermediate quantity. The end-to-end
# test is about successes and failures:
#
#   Train with gate-refused trials INCLUDED, train with them EXCLUDED, compare held-out
#   performance. If "did this trial meet its primary endpoint" is close to meaningless for a
#   maximum-tolerated-dose trial, that trial's label is close to noise, and including it
#   should measurably hurt. If the two models are indistinguishable, the gate is not earning
#   its complexity on the training side, and that is a finding rather than a disappointment.
#
# Written down NOW, before a model exists, because that is when the direction can be fixed
# without knowing the answer -- which is most of what pre-registration buys.
TRAINING_EXCLUSION_PREDICTED_DIRECTION = "improve"

# The effect size cannot be set yet: it depends on the metric, the split and the negative
# count, none of which exist. None rather than a placeholder, per the tri-state rule of
# section 10 -- unknown is not zero, and 0.0 here would read as "no improvement required"
# rather than "not yet decidable". Fix it when the model exists, and BEFORE running the
# comparison.
TRAINING_EXCLUSION_MIN_EFFECT = None

# THE HALF THAT CAN NEVER BE TESTED THIS WAY, recorded so its absence is not mistaken for
# an oversight. The gate has two jobs:
#   TRAINING  which trials carry a usable label. Testable end-to-end, as above.
#   SERVING   which trials get a probability shown. NOT testable against outcomes, by
#             construction: if a trial's primary endpoint is a maximum tolerated dose there
#             is no ground truth for "did it meet its endpoint", so whether refusing to
#             score it was right cannot be checked by looking at what happened. There is no
#             outcome to compare against.
# For the serving half, human judgment is not a stopgap until something better arrives. It
# is the only possible reference, permanently, and GATING_CRITERIA is therefore not an
# interim measure there even once a model exists.
SERVING_GATE_HAS_NO_OUTCOME_REFERENCE = True

# ---- training exclusion, DECIDED 2026-10-04 --------------------------------
# A trial the gate refuses is excluded from endpoint-met training even when it carries a
# headline label, because the serving gate will decline every trial like it: a label the
# tool would refuse to show is a label it does not trust. "Label overrides the gate" was
# rejected because it changes nothing at serving, where no label exists yet.
#
# Measured before the REASON_UNTESTABLE_BESIDE_UNREADABLE change (audit_serving_flag.py,
# project machine): 424 refused-yet-labelled drug trials, 333 all-pharmacokinetic; that
# change moves the other 91 to undeterminable. Several genuine-PK labels in its 25-trial
# sample came from drug-drug interaction tests, where "met" means an interaction was found
# -- inverted, not noisy.
#
# KNOWN COST, accepted: read by eye, 9 of 25 in the post-change sample (all 333 now all-
# PK) measured something other than the drug -- an AUC of FEV1 (four), of pain
# intensity, of nasal cross-sectional area, of postprandial glucose; serum cortisol;
# troponin. A pattern carve-out was declined as too likely to be fitted to the sample
# that suggested it, so those labels are excluded too and those trials declined at
# serving.
#
# Excluded rows stay IDENTIFIABLE by the reason below, so the pre-registered comparison
# (TRAINING_EXCLUSION_PREDICTED_DIRECTION) can still train with them included.
TRAINING_EXCLUDED_GATE_REFUSED = "gate_refused"
TRAINING_NOT_EXCLUDED = "not_excluded"
TRAINING_EXCLUSION_REASONS = (TRAINING_EXCLUDED_GATE_REFUSED,)
TRAINING_EXCLUSION_DOC = {
    TRAINING_EXCLUDED_GATE_REFUSED: ("the serving gate declines this trial, so its label "
                                     "is not trusted for training either"),
}

# ---- fields this module emits, all gate-only (R7) -------------------------
GATE_ONLY_FIELDS = (
    "endpoint_type_class",
    "endpoint_type_matched_classes",
    "endpoint_type_gate",
    "endpoint_type_gate_reason",
    "endpoint_type_rollup_used",
    "endpoint_type_n_primary",
    "endpoint_type_n_testable",
    "endpoint_type_n_untestable",
    "endpoint_type_n_unreadable",
    "endpoint_type_mixed",
    "endpoint_type_training_exclusion",
)

GATE_ONLY_DOC = (
    "Every field here may decide whether an endpoint estimate is DISPLAYED, and whether a "
    "trial's row is ELIGIBLE for endpoint-met training, and may never enter the "
    "endpoint-met feature matrix. The class is derived from the same "
    "primary-outcome text the label's analysis rows hang off, and the trial-level gate "
    "shares the label's any/all roll-up, so a feature built from these would sit adjacent "
    "to the label rather than upstream of it."
)


# ---- the patterns ---------------------------------------------------------
# Case-insensitive throughout. Word boundaries on every acronym, because 'PK' inside
# 'PKU' and 'AE' inside 'AER' are different words. Each block says what it is for; a
# pattern added without a reason beside it is a pattern nobody can review.
_PATTERNS = {
    CLASS_BIOEQUIVALENCE: re.compile(
        r"""
        bio\s*-?\s*equivalen          # bioequivalence, bio-equivalence, bio equivalence
      | bio\s*-?\s*availab\w*\s+(?:of\s+)?(?:the\s+)?test   # relative bioavailability, test vs ref
      | relative\s+bio\s*-?\s*availab
      | \btest\b[^.]{0,20}\breference\b    # 'Cmax of Test versus Reference'
      | \breference\b[^.]{0,20}\btest\b
      | \bt\s*/\s*r\b                      # T/R ratio, the standard notation
      | geometric\s+mean\s+ratio\s+of\s+(?:the\s+)?test
        """, re.IGNORECASE | re.VERBOSE),

    CLASS_DOSE_FINDING: re.compile(
        r"""
        maximum\s+tolerated\s+dose
      | \bmtd\b
      | dose\s*-?\s*limiting
      | \bdlts?\b
      | recommended\s+(?:phase|part|dose|starting)      # recommended phase 2 dose
      | \brp2d\b
      | \brpt?d\b
      | dose\s+escalation
      | dose\s+selection
      | optimal\s+(?:biolog\w*\s+)?dose
      | \bobd\b
      | maximum\s+(?:feasible|administered|assessed)\s+dose
        """, re.IGNORECASE | re.VERBOSE),

    CLASS_PHARMACOKINETIC: re.compile(
        r"""
        pharmacokinetic
      | \bpk\b
      | \bauc\b | \bauc[_0-9]        # AUC, AUC0-t, AUC_inf. Also matches ROC 'AUC',
                                     # which is a known false positive and is exactly
                                     # what the hand labels are for
      | area\s+under\s+the\s+(?:plasma\s+|serum\s+|blood\s+)?
        (?:concentration|curve|drug)
      | \bc\s*-?\s*max\b | \bt\s*-?\s*max\b | \bc\s*-?\s*min\b
      | \bc\s*-?\s*trough\b | \bctrough\b
      | trough\s+(?:concentration|level|plasma)
      | half\s*-?\s*life | \bt\s*1\s*/\s*2\b
      | (?:apparent|plasma|renal|systemic|total\s+body)\s+clearance
      | \bcl\s*/\s*f\b
      | clearance\s+of\s+(?:the\s+)?(?:study\s+)?drug
      | volume\s+of\s+distribution
      | (?:plasma|serum|blood|urine|urinary|csf)\s+
        (?:drug\s+)?concentration
      | concentration\s*-?\s*time
      | systemic\s+exposure | drug\s+exposure
      | \bbio\s*-?\s*availab           # bioavailability, when no test/reference framing
      | pharmacodynamic\s+and\s+pharmacokinetic
        """, re.IGNORECASE | re.VERBOSE),

    CLASS_SAFETY: re.compile(
        r"""
        adverse\s+(?:event|reaction|experience|effect)
      | \baes?\b | \bteaes?\b | \bsaes?\b | \badrs?\b
      | treatment\s*-?\s*emergent
      | toxicit
      | tolerabilit
      | \bsafety\b
      | side\s+effect
      | laborator\w+\s+abnormalit
      | clinically\s+significant\s+(?:change|abnormalit)
        """, re.IGNORECASE | re.VERBOSE),

    CLASS_EFFICACY: re.compile(
        r"""
        # --- time-to-event -------------------------------------------------
        overall\s+survival | \bpfs\b | \bdfs\b | \befs\b | \bos\s+rate\b
      | progression\s*-?\s*free | disease\s*-?\s*free | event\s*-?\s*free
      | relapse\s*-?\s*free | recurrence\s*-?\s*free
      | time\s+to\s+(?:progression|event|failure|relapse|recurrence|death|
                      treatment|resolution|healing)
      | \bsurvival\s+(?:rate|at|time|probability)
        # --- response ------------------------------------------------------
      | response\s+rate | \borr\b | objective\s+response | overall\s+response
      | best\s+overall\s+response
      | complete\s+(?:response|remission|resolution|clearance|cure)
      | partial\s+response | remission | \bcure\s+rate\b
      | (?:viral|virolog\w+|bacterial|microbiolog\w+|parasit\w+|fungal)\s+
        (?:clearance|cure|eradication|suppression|response)
      | eradication
        # --- change in a measured quantity ---------------------------------
      | change\s+(?:from|in|over)\s+baseline
      | percent(?:age)?\s+change | absolute\s+change | mean\s+change
      | reduction\s+in | improvement\s+in | decline\s+in
        # --- event rates and proportions -----------------------------------
      | incidence\s+of | \bevent\s+rate\b | \brelapse\s+rate\b
      | proportion\s+of\s+(?:participants|subjects|patients)
      | (?:number|percentage)\s+of\s+(?:participants|subjects|patients)\s+
        (?:who|with|achieving|attaining|reaching|experiencing|responding)
        # --- scored instruments --------------------------------------------
      | \bscore\b | \bscale\b | \bvas\b | questionnaire | \bindex\b
      | remission\s+rate | success\s+rate | failure\s+rate
        """, re.IGNORECASE | re.VERBOSE),
}


def _text(raw) -> str:
    """Endpoint text, normalised only in whitespace.

    Nothing else is normalised. Stripping punctuation would break the acronym boundaries
    the patterns depend on, and case is already handled by the compiled flags.
    """
    if raw is None:
        return ""
    return re.sub(r"\s+", " ", str(raw)).strip()


def endpoint_text_key(raw) -> str:
    """The identity two endpoint texts share when the rule reads them as one input: its own
    whitespace normalisation, nothing more."""
    return _text(raw)


def matched_classes(title) -> tuple:
    """Every class whose pattern fires on this endpoint text, in precedence order.

    Returned rather than discarded so the ORDER's workload is measurable: a title matching
    one class needed no judgment, a title matching three had its class decided by
    `CLASS_PRECEDENCE`, which is a reading. `multi_match_rate` aggregates this.
    """
    text = _text(title)
    if not text:
        return ()
    return tuple(cls for cls in CLASS_PRECEDENCE if _PATTERNS[cls].search(text))


def classify_title(title) -> str:
    """One endpoint text -> one class. CLASS_OTHER when nothing matched.

    Empty text also returns CLASS_OTHER rather than raising: an absent endpoint measure is
    a real state in the registry, and it must travel as "the rule cannot read this" rather
    than as an exception the caller has to remember to handle. The trial-level roll-up
    distinguishes "no text at all" from "text that matched nothing", because they are
    different facts about a trial.
    """
    hits = matched_classes(title)
    return hits[0] if hits else CLASS_OTHER


def classify_titles(titles: Iterable) -> list:
    """Several endpoint texts -> their classes, in the order given."""
    return [classify_title(t) for t in titles]


def gate_for_class(endpoint_class: str) -> str:
    """One class -> its gate verdict. Raises on a class outside the vocabulary.

    Raising rather than defaulting: a class with no gate mapping is a class somebody added
    without deciding what the tool should do about it, and defaulting would pick a side
    silently.
    """
    if endpoint_class not in CLASS_GATE:
        raise ValueError(f"{endpoint_class!r} has no gate verdict; add it to CLASS_GATE "
                         f"deliberately rather than defaulting")
    return CLASS_GATE[endpoint_class]


def refuses_estimate(gate: str) -> bool:
    """The BINARY collapse of the gate: would the tool decline to give a number?

    True only for GATE_NOT_APPLICABLE. Undeterminable returns False, so an unreadable
    endpoint yields a number with a silent endpoint clause rather than a refusal -- the
    same principle that left the clause unfilled in the first place.

    This is the collapse the gating kappa is computed on, and it is defined here rather
    than in the scoring script so the number that gates the feature is produced by the same
    function that ships. Its direction is worth stating: an `other` the labeller calls
    pharmacokinetic counts as a DISAGREEMENT, while an `other` the labeller calls
    efficacy-shaped counts as agreement. That makes the collapsed figure conservative on
    refusals and blind to one class of ordinary misreading, which is why the multi-class
    matrix is printed beside it.
    """
    return gate == GATE_NOT_APPLICABLE


def collapse_to_gate(endpoint_class: str) -> bool:
    """Class -> the boolean the gating kappa is computed on."""
    return refuses_estimate(gate_for_class(endpoint_class))


def trial_gate(classes: Iterable[str], rollup: str = DEFAULT_GATE_ROLLUP) -> dict:
    """Per-outcome classes for ONE trial -> its gate record.

    Counts every category rather than returning a bare verdict, because "two of three
    endpoints are pharmacokinetic" is what the mixed-trial sentence needs and it cannot be
    recovered from the verdict afterwards.

    ANY: applicable as soon as one endpoint is testable. Not-applicable only when EVERY
    endpoint was read as untestable. An unreadable endpoint beside untestable ones makes
    the verdict undeterminable, because the unread one may be the tested endpoint; the
    clause then names the untestable ones (REASON_UNTESTABLE_BESIDE_UNREADABLE).

    ALL: applicable only when every endpoint is testable; not-applicable when every endpoint
    is untestable OR the mix contains an untestable one, since a trial-level "all endpoints
    met" cannot be decided when one endpoint has no verdict to give.
    """
    if rollup not in GATE_ROLLUPS:
        raise ValueError(f"rollup {rollup!r} is not one of {GATE_ROLLUPS}")
    ordered = list(classes)
    n = len(ordered)
    verdicts = [gate_for_class(c) for c in ordered]
    n_testable = sum(1 for v in verdicts if v == GATE_APPLICABLE)
    n_untestable = sum(1 for v in verdicts if v == GATE_NOT_APPLICABLE)
    n_unreadable = sum(1 for v in verdicts if v == GATE_UNDETERMINABLE)
    mixed = n_testable > 0 and n_untestable > 0
    record = {
        "n_primary": n,
        "n_testable": n_testable,
        "n_untestable": n_untestable,
        "n_unreadable": n_unreadable,
        "mixed": mixed,
        "rollup": rollup,
        "classes": tuple(ordered),
        "untestable_classes": tuple(c for c, v in zip(ordered, verdicts)
                                    if v == GATE_NOT_APPLICABLE),
    }
    if n == 0:
        record.update(gate=GATE_UNDETERMINABLE, reason=REASON_NO_ENDPOINT_TEXT)
        return record
    if rollup == ROLLUP_ANY:
        if n_testable > 0:
            record.update(gate=GATE_APPLICABLE,
                          reason=REASON_ALL_TESTABLE if n_testable == n
                          else REASON_TESTABLE)
        elif n_untestable > 0 and n_unreadable > 0:
            record.update(gate=GATE_UNDETERMINABLE,
                          reason=REASON_UNTESTABLE_BESIDE_UNREADABLE)
        elif n_untestable > 0:
            record.update(gate=GATE_NOT_APPLICABLE, reason=REASON_ALL_UNTESTABLE)
        else:
            record.update(gate=GATE_UNDETERMINABLE, reason=REASON_UNREADABLE)
        return record
    # ROLLUP_ALL
    if n_testable == n:
        record.update(gate=GATE_APPLICABLE, reason=REASON_ALL_TESTABLE)
    elif n_untestable == n:
        record.update(gate=GATE_NOT_APPLICABLE, reason=REASON_ALL_UNTESTABLE)
    elif n_untestable > 0:
        record.update(gate=GATE_NOT_APPLICABLE, reason=REASON_MIXED_UNDER_ALL)
    else:
        record.update(gate=GATE_UNDETERMINABLE, reason=REASON_UNREADABLE)
    return record


def trial_gate_from_titles(titles: Iterable, rollup: str = DEFAULT_GATE_ROLLUP) -> dict:
    """Endpoint texts for one trial -> its gate record. The end-to-end entry point."""
    return trial_gate(classify_titles(titles), rollup)


def training_exclusion(record: dict) -> str:
    """Gate record -> why the trial is excluded from endpoint-met training, or
    TRAINING_NOT_EXCLUDED. Never blank: "not excluded" and "not computed" differ."""
    if refuses_estimate(record.get("gate")):
        return TRAINING_EXCLUDED_GATE_REFUSED
    return TRAINING_NOT_EXCLUDED


def gate_record_fields(record: dict) -> dict:
    """Gate record -> the flat, CSV-safe fields named in GATE_ONLY_FIELDS.

    One place where the emitted names are produced, so the registry assertion in the tests
    has something to check against. A field added to the output without being registered
    fails `test_every_emitted_field_is_registered`.
    """
    classes = record.get("classes", ())
    return {
        "endpoint_type_class": classes[0] if len(classes) == 1 else "|".join(classes),
        "endpoint_type_matched_classes": "|".join(classes),
        "endpoint_type_gate": record["gate"],
        "endpoint_type_gate_reason": record["reason"],
        "endpoint_type_rollup_used": record["rollup"],
        "endpoint_type_n_primary": record["n_primary"],
        "endpoint_type_n_testable": record["n_testable"],
        "endpoint_type_n_untestable": record["n_untestable"],
        "endpoint_type_n_unreadable": record["n_unreadable"],
        "endpoint_type_mixed": record["mixed"],
        "endpoint_type_training_exclusion": training_exclusion(record),
    }


# ---- what the user reads --------------------------------------------------
# Three separate sentences for three separate situations. The handoff's section 1.1 pattern
# is a plain statement of WHY a number is missing, not a hedged paragraph, and a missing
# number and a caveated number are different outputs needing different words.
DECLINE_PREFIX = "No endpoint estimate: "

DECLINE_SENTENCE = {
    CLASS_BIOEQUIVALENCE: (
        "this is a bioequivalence study, which tests whether a formulation matches its "
        "reference rather than whether a treatment works."),
    CLASS_DOSE_FINDING: (
        "this trial's primary endpoint is a dose-finding measurement. Its result is a "
        "selected dose, not a verdict against a threshold."),
    CLASS_PHARMACOKINETIC: (
        "this trial's primary endpoint is a pharmacokinetic measurement, reported as a "
        "value rather than tested against a threshold."),
    CLASS_SAFETY: (
        "this trial's primary endpoint is a safety or tolerability measurement, reported "
        "as a count rather than tested against a threshold."),
}

# The mixed case, which the ANY roll-up creates and which neither of the two existing flag
# clauses describes. A trial with two pharmacokinetic primaries and one efficacy primary
# HAS an answer, for one endpoint out of three, and saying "the question does not apply"
# would be wrong while saying nothing would overstate what the number covers.
CLAUSE_MIXED_TEMPLATE = (
    "This trial registered {n_primary} primary endpoints; {n_untestable} of them "
    "{be_untestable} {article}{mechanism} {measurement_word} with no threshold to clear, "
    "so this estimate reflects the {n_testable} of {n_primary} that {be_testable} "
    "tested.")

# The undeterminable case where something WAS read. Without it the user would see a number
# with no sign that most of the trial's primaries are value-reporting -- the false
# allowance the gating criteria weight most heavily. Stating it lowers the cost of being
# wrong, which is what licensed the change from refusal (lesson 66).
CLAUSE_UNREAD_BESIDE_UNTESTABLE_TEMPLATE = (
    "{n_untestable} of this trial's {n_primary} primary endpoints {be_untestable} "
    "{article}{mechanism} {measurement_word} with no threshold to clear. This estimate "
    "rests on {others}, which could not be classified, and applies only if {pronoun} "
    "{be_unreadable} tested against a threshold.")

# What "met its primary endpoint" MEANS for a class that is allowed but qualified. One
# entry per QUALIFIED_CLASSES, checked total by a test, because a qualified class with no
# sentence would show a bare number -- which is precisely the thing the qualified flip was
# chosen over.
#
# Written to state what the trial was trying to do, not to hedge. "This may not be
# reliable" tells a reader nothing; "success here means a tolerable dose was identified"
# tells them what the number is about.
CLASS_SUCCESS_MEANING = {
    CLASS_DOSE_FINDING: (
        "This trial's primary endpoint is a dose: the maximum tolerated dose, the "
        "recommended phase 2 dose, or the dose-limiting toxicities used to find one. "
        "Meeting it means a tolerable dose was identified, not that a treatment effect "
        "was shown."),
    CLASS_SAFETY: (
        "This trial's primary endpoint is safety or tolerability -- a rate of adverse "
        "events, toxicities or abnormal findings. Meeting it means the rate was "
        "acceptable against the trial's own criterion, not that the treatment worked."),
}

# The CO-PRIMARY form, used when the trial registered any primary outside the qualified
# class that names the clause. CLASS_SUCCESS_MEANING says "this trial's primary endpoint
# is ...", which is false for a phase 3 trial with an efficacy primary and a safety
# co-primary. Keyed by roll-up because what success means for the whole trial depends on
# it: under ANY the qualified endpoint alone can carry a "met"; under ALL it cannot.
CLASS_KIND_WORDS = {
    CLASS_DOSE_FINDING: ("a dose-finding measurement", "dose-finding measurements"),
    CLASS_SAFETY: ("a safety or tolerability measurement",
                   "safety or tolerability measurements"),
}

CLASS_SUCCESS_SHORT = {
    CLASS_DOSE_FINDING: "only that a tolerable dose was identified",
    CLASS_SAFETY: "only that an adverse-event rate was acceptable",
}

CLAUSE_COPRIMARY_TEMPLATE = {
    ROLLUP_ANY: ("{n_kind} of this trial's {n_primary} primary endpoints {be} {kind}. "
                 "This estimate counts meeting any one primary endpoint as success, so it "
                 "may reflect {meaning}, not that the treatment worked."),
    ROLLUP_ALL: ("{n_kind} of this trial's {n_primary} primary endpoints {be} {kind}. "
                 "This estimate counts success only if every primary endpoint is met, "
                 "{ref} included."),
}

# One word per REFUSING class, for the sentence a user reads. Bioequivalence was removed
# when it became applicable: a mechanism word for a class that never refuses is a
# sentence that can never be produced, and `test_mechanism_words_cover_exactly_the
# _refusing_classes` derives the key set from CLASS_GATE so the two cannot drift again.
MECHANISM_WORD = {
    CLASS_PHARMACOKINETIC: "pharmacokinetic",
}


def _dominant_qualified(record: dict) -> Optional[str]:
    """Which QUALIFIED class names the clause, when a trial has more than one.

    Resolved by `CLASS_PRECEDENCE` rather than by count, for the reason
    `_dominant_untestable` is: the sentence a user reads must not change because one more
    safety endpoint was registered.
    """
    present = set(record.get("classes", ()))
    for endpoint_class in CLASS_PRECEDENCE:
        if endpoint_class in present and endpoint_class in QUALIFIED_CLASSES:
            return endpoint_class
    return None


def requires_clause(record: dict) -> bool:
    """Does this gate record REQUIRE a clause beside its number?

    True when the trial is allowed and at least one primary belongs to a qualified class,
    or when untestable primaries sit beside unclassifiable ones. Exposed so a caller can assert the clause is present rather than discovering it
    is missing, which is the failure the qualified flip was designed to avoid.
    """
    if record.get("reason") == REASON_UNTESTABLE_BESIDE_UNREADABLE:
        return True
    return (record.get("gate") == GATE_APPLICABLE
            and _dominant_qualified(record) is not None)


def _dominant_untestable(record: dict) -> Optional[str]:
    """Which untestable class names the mechanism, when a trial has more than one.

    Resolved by `CLASS_PRECEDENCE` rather than by count, so the sentence a user reads does
    not change because one more safety endpoint was registered. Deterministic beats
    representative here: the mechanism word is an explanation, not a statistic.
    """
    present = set(record.get("untestable_classes", ()))
    for cls in CLASS_PRECEDENCE:
        if cls in present:
            return cls
    return None


def decline_sentence(record: dict) -> Optional[str]:
    """The one-line reason shown INSTEAD of a number. None when a number is shown.

    Only ever produced for GATE_NOT_APPLICABLE. An undeterminable gate returns None, which
    is the point: the tool shows the estimate and stays silent about endpoint type rather
    than declining on the strength of a regex that failed to match.
    """
    if record.get("gate") != GATE_NOT_APPLICABLE:
        return None
    if record.get("reason") == REASON_MIXED_UNDER_ALL:
        cls = _dominant_untestable(record)
        word = MECHANISM_WORD.get(cls, "value-reporting")
        return (f"{DECLINE_PREFIX}this trial registered {record['n_primary']} primary "
                f"endpoints and {record['n_untestable']} of them are {word} "
                f"measurements, so whether it met all of them has no answer.")
    cls = _dominant_untestable(record)
    if cls is None:
        return None
    return DECLINE_PREFIX + DECLINE_SENTENCE[cls]


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def is_coprimary(record: dict, qualified: str) -> bool:
    """Did the trial register any primary outside the qualified class naming the clause?"""
    return set(record.get("classes", ())) != {qualified}


def _unread_beside_untestable_clause(record: dict) -> str:
    n_untestable = record["n_untestable"]
    n_unreadable = record["n_unreadable"]
    return CLAUSE_UNREAD_BESIDE_UNTESTABLE_TEMPLATE.format(
        n_untestable=n_untestable,
        n_primary=record["n_primary"],
        be_untestable=_plural(n_untestable, "is", "are"),
        article=_plural(n_untestable, "a ", ""),
        mechanism=MECHANISM_WORD.get(_dominant_untestable(record), "value-reporting"),
        measurement_word=_plural(n_untestable, "measurement", "measurements"),
        others=_plural(n_unreadable, "the other one", f"the other {n_unreadable}"),
        pronoun=_plural(n_unreadable, "it", "they"),
        be_unreadable=_plural(n_unreadable, "was", "were"),
    )


def _qualified_clause(record: dict, qualified: str) -> str:
    """The single-class sentence, or the co-primary form when it would be false."""
    if not is_coprimary(record, qualified):
        return CLASS_SUCCESS_MEANING[qualified]
    n_kind = sum(1 for c in record["classes"] if c == qualified)
    singular, plural = CLASS_KIND_WORDS[qualified]
    return CLAUSE_COPRIMARY_TEMPLATE[record["rollup"]].format(
        n_kind=n_kind,
        n_primary=record["n_primary"],
        be=_plural(n_kind, "is", "are"),
        kind=_plural(n_kind, singular, plural),
        meaning=CLASS_SUCCESS_SHORT[qualified],
        ref=_plural(n_kind, "that one", "those"),
    )


def endpoint_clause(record: dict) -> Optional[str]:
    """The clause passed to `fdaaa.compose_flag` BESIDE a number that is shown.

    Three cases produce one, and the order matters:

      QUALIFIED   the trial is allowed because its primaries are dose-finding or safety.
                  The clause states what success MEANT for that kind of endpoint -- in the
                  co-primary form when other kinds of primary are also registered. This is
                  MANDATORY -- the flip of those two classes in CLASS_GATE was approved on
                  the explicit condition that a clause accompanies the number, so a bare
                  number for a qualified class is a regression, not a simplification.
      MIXED       some primaries testable, some not. The existing sentence.
      UNREAD      undeterminable, some primaries read as value-reporting and the rest
                  unclassifiable. Also mandatory: see requires_clause.
      otherwise   None. A plain applicable trial has nothing to caveat and an
                  undeterminable one has nothing measured, so the flag never gains a
                  sentence the classifier did not earn.

    Qualified is checked BEFORE mixed because a trial can be both -- a dose-finding
    primary alongside a pharmacokinetic one -- and in that case what success meant is more
    useful to the reader than how many endpoints were excluded.

    Reaches the user through `fdaaa.trial_flag`, which raises if a record that
    `requires_clause` produces a flag without this text.
    """
    if record.get("reason") == REASON_UNTESTABLE_BESIDE_UNREADABLE:
        return _unread_beside_untestable_clause(record)
    if record.get("gate") != GATE_APPLICABLE:
        return None
    qualified = _dominant_qualified(record)
    if qualified is not None:
        return _qualified_clause(record, qualified)
    if not record.get("mixed"):
        return None
    cls = _dominant_untestable(record)
    word = MECHANISM_WORD.get(cls, "value-reporting")
    return CLAUSE_MIXED_TEMPLATE.format(
        n_primary=record["n_primary"],
        n_untestable=record["n_untestable"],
        be_untestable=_plural(record["n_untestable"], "is", "are"),
        article=_plural(record["n_untestable"], "a ", ""),
        mechanism=word,
        measurement_word=_plural(record["n_untestable"], "measurement", "measurements"),
        n_testable=record["n_testable"],
        be_testable=_plural(record["n_testable"], "was", "were"),
    )


# ---- coverage and audit aggregates ---------------------------------------
def class_coverage(titles: Iterable) -> dict:
    """Endpoint texts -> class counts plus how much work the precedence order did.

    `multi_match` is the number the ordering decision rests on. If almost no title matches
    two classes, the order is nearly irrelevant and a disagreement with the hand labels is
    about the PATTERNS; if many do, the order is doing the deciding and that is where to
    look. Printing the count is what makes the difference visible instead of assumed.
    """
    counts = {cls: 0 for cls in ENDPOINT_CLASSES}
    multi = 0
    pairs: dict = {}
    empty = 0
    total = 0
    for title in titles:
        total += 1
        text = _text(title)
        if not text:
            empty += 1
        hits = matched_classes(title)
        cls = hits[0] if hits else CLASS_OTHER
        counts[cls] += 1
        if len(hits) > 1:
            multi += 1
            key = hits[:2]
            pairs[key] = pairs.get(key, 0) + 1
    return {"total": total, "counts": counts, "multi_match": multi,
            "multi_match_pairs": pairs, "empty_text": empty}


def multi_match_rate(coverage: dict) -> Optional[float]:
    """Share of endpoint texts whose class was decided by the precedence order."""
    total = coverage.get("total", 0)
    if not total:
        return None
    return coverage["multi_match"] / total


def gate_coverage(records: Iterable[dict]) -> dict:
    """Trial gate records -> verdict counts, reason counts, and the mixed count.

    Audit-first: this is what a new script reports before anything downstream consumes the
    gate, and the mixed count is separate because mixed trials get a DIFFERENT user-facing
    sentence and so are a distinct product state rather than a subset of applicable.
    """
    verdicts = {v: 0 for v in GATE_VERDICTS}
    reasons: dict = {}
    mixed = 0
    total = 0
    for record in records:
        total += 1
        verdicts[record["gate"]] += 1
        reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
        if record.get("mixed"):
            mixed += 1
    return {"total": total, "verdicts": verdicts, "reasons": reasons, "mixed": mixed}


def gate_versus_tier(records_and_tiers: Iterable[tuple], headline_tiers: Iterable[str],
                     ) -> dict:
    """Cross-check the gate against the label the trial actually carries.

    [(gate_record, tier_min), ...] -> a 2x2-shaped count of gate verdict against whether
    the trial carries a headline label.

    This is a SECOND validation signal, independent of the hand labels and free: a trial the
    gate calls not-applicable which nonetheless carries a tier A/B label is a case where the
    sponsor did apply a threshold to that endpoint, so either the gate misread the endpoint
    or the endpoint really was tested. It is not ground truth -- the absence of a label
    mostly means nothing was posted, which is a disclosure fact rather than an endpoint-type
    fact -- so only the `refused_but_labelled` cell carries information, and that asymmetry
    is the reason this returns counts rather than a kappa.
    """
    headline = set(headline_tiers)
    out = {"refused_but_labelled": 0, "refused_and_unlabelled": 0,
           "allowed_and_labelled": 0, "allowed_and_unlabelled": 0,
           "undeterminable_and_labelled": 0, "undeterminable_and_unlabelled": 0,
           "total": 0}
    for record, tier in records_and_tiers:
        out["total"] += 1
        labelled = tier in headline
        gate = record["gate"]
        if gate == GATE_NOT_APPLICABLE:
            key = "refused_but_labelled" if labelled else "refused_and_unlabelled"
        elif gate == GATE_APPLICABLE:
            key = "allowed_and_labelled" if labelled else "allowed_and_unlabelled"
        else:
            key = ("undeterminable_and_labelled" if labelled
                   else "undeterminable_and_unlabelled")
        out[key] += 1
    return out