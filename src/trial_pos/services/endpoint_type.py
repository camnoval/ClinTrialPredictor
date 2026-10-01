"""Endpoint TYPE: does "did this trial meet its primary endpoint" even have an answer?

WHY THIS EXISTS
===============
The endpoint-met label is read from a trial's own posted primary ANALYSIS: a p-value
against an alpha, or an interval against a null. That presupposes the primary endpoint was
something a threshold was applied to. Much of early-phase research reports a VALUE instead
-- an AUC, a Cmax, a maximum tolerated dose -- and for those trials the question has no
answer to estimate, so a probability is not a cautious estimate, it is a category error.

`fdaaa.compose_flag` already takes the endpoint clause as a PARAMETER and has been left
unfilled on purpose, because inventing the clause would assert a measurement nobody made.
This module is what fills it, and it does not reach the flag until its agreement against
hand labels clears the threshold in `GATE_KAPPA_MINIMUM` (see `scripts/` for the scoring
step). A keyword rule that decides what the tool refuses to answer is a verdict-producing
threshold like any other.

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
    GATE_NOT_APPLICABLE: ("every readable primary endpoint is reported as a value rather "
                          "than tested against a threshold. The tool DECLINES and says "
                          "which mechanism"),
    GATE_UNDETERMINABLE: ("the rule could not read the endpoint text. The estimate is "
                          "shown with the flags it would otherwise carry, and the "
                          "endpoint clause is SILENT rather than invented"),
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
    CLASS_DOSE_FINDING: GATE_NOT_APPLICABLE,
    CLASS_PHARMACOKINETIC: GATE_NOT_APPLICABLE,
    CLASS_SAFETY: GATE_NOT_APPLICABLE,
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
REASON_TESTABLE = "at_least_one_primary_endpoint_is_threshold_tested"
REASON_ALL_TESTABLE = "every_primary_endpoint_is_threshold_tested"

GATE_REASONS = (REASON_NO_ENDPOINT_TEXT, REASON_ALL_UNTESTABLE, REASON_MIXED_UNDER_ALL,
                REASON_UNREADABLE, REASON_TESTABLE, REASON_ALL_TESTABLE)

REASON_DOC = {
    REASON_NO_ENDPOINT_TEXT: ("the trial registered no primary outcome text this module "
                              "could read. UNDETERMINABLE, not 'not applicable'"),
    REASON_ALL_UNTESTABLE: ("every primary endpoint is a value-reporting measurement. The "
                            "question has no answer to estimate"),
    REASON_MIXED_UNDER_ALL: ("under the ALL roll-up a trial-level verdict needs every "
                             "primary endpoint tested, and at least one is not"),
    REASON_UNREADABLE: ("no pattern matched any primary endpoint, so the rule has no "
                        "reading to offer either way"),
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
GATE_KAPPA_MINIMUM = 0.60
REPORTABLE_KAPPA_MINIMUM = 0.40

# A verdict banner on a handful of rows is a banner that gets ignored (lesson 29). Strata
# thinner than this are printed and excluded from any verdict.
MIN_STRATUM_FOR_VERDICT = 20

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
)

GATE_ONLY_DOC = (
    "Every field here may decide whether an endpoint estimate is DISPLAYED and may never "
    "enter the endpoint-met feature matrix. The class is derived from the same "
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

    ANY: applicable as soon as one endpoint is testable. Not-applicable only when at least
    one endpoint was READ as untestable and none was readable as testable -- if the only
    thing we have is unreadable text, the verdict is undeterminable, not a refusal.

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

# One word per REFUSING class, for the sentence a user reads. Bioequivalence was removed
# when it became applicable: a mechanism word for a class that never refuses is a
# sentence that can never be produced, and `test_mechanism_words_cover_exactly_the
# _refusing_classes` derives the key set from CLASS_GATE so the two cannot drift again.
MECHANISM_WORD = {
    CLASS_DOSE_FINDING: "dose-finding",
    CLASS_PHARMACOKINETIC: "pharmacokinetic",
    CLASS_SAFETY: "safety or tolerability",
}


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


def endpoint_clause(record: dict) -> Optional[str]:
    """The clause passed to `fdaaa.compose_flag` BESIDE a number that is shown.

    None in every case except a mixed trial. There is deliberately no clause for the plain
    applicable case (nothing to caveat) or the undeterminable case (nothing measured), so
    the flag never gains a sentence the classifier did not earn.

    `compose_flag` is unchanged: it already takes this as a parameter. Nothing wires this
    function into it until the scoring step reports a kappa clearing
    `GATE_KAPPA_MINIMUM` -- until then the clause stays visibly missing rather than
    silently invented.
    """
    if record.get("gate") != GATE_APPLICABLE or not record.get("mixed"):
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