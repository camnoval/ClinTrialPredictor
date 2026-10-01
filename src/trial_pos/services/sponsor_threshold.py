"""Did the SPONSOR apply a threshold to this endpoint? A tier-derived tri-state reference.

WHAT THIS IS FOR
================
`endpoint_type.py` decides whether "did this trial meet its primary endpoint" has an
answer by reading the endpoint's registered TEXT with a keyword rule. That rule is
unscored, and the thing it needs is a reference judged by somebody other than the rule.

For the trials that posted results, one exists and needs no hand labelling: the sponsor's
own primary analysis says whether a threshold was applied. A p-value against an alpha, or
an interval positioned against a null, means a threshold was applied. A value with an
interval around itself -- an AUC, a Cmax, a mean with its own CI -- means one was not.
That is the same distinction the keyword rule makes, judged independently of it by the
people who ran the trial.

IT IS TIER-DERIVED, AND THE COST IS STATED (section 12.9 decision 3)
====================================================================
This module does NOT re-read the raw analysis fields. It delegates every decision to
`endpoint_label.py`, because that module already encodes "did this analysis support a
verdict" and already handles the trap a fresh reader would walk into: `resolve_null_value`,
`contrast_family` and `ratio_scale` between them catch the bioequivalence case, where an
interval sits against CONTAINMENT BOUNDS rather than against a null and no choice of null
makes an exclusion test answer the question asked. A second reader would reimplement all of
that and get that case wrong.

**The accepted cost: this reference is NOT independent of the label pipeline.** It is
independent of the KEYWORD RULE, which is the thing being validated, and that is the
independence that matters here. Any future claim that this reference validates the label
machinery itself is circular and must be refused.

THE THIRD STATE IS LOAD-BEARING (section 12.9 decision 1)
=========================================================
An analysis carrying neither a p-value nor an interval is `THRESHOLD_UNKNOWN`, counted
separately and never folded into "no threshold applied". Folding it into "no" would make
this reference agree with the keyword rule everywhere the rule says pharmacokinetic -- for
a reason that has nothing to do with endpoint type, since a descriptive row is silent about
endpoint type rather than evidence about it -- and the independence the whole reference
rests on would evaporate. It is the tri-state rule of section 10 applied one level down.

WHAT MAPS WHERE, AND WHY EACH BRANCH IS NOT THE OTHER TWO
=========================================================
  p-value decidable              TESTED       the sponsor stated a verdict against alpha
  NI / equivalence design        UNKNOWN      tier E. Success is defined against a MARGIN,
                                              the margin is not a structured AACT field,
                                              so whether the endpoint cleared a threshold
                                              is unreadable -- not absent
  percent-scaled ratio           UNKNOWN      the bioequivalence shape. A threshold very
                                              likely WAS applied, just not one this
                                              project reads, so "no" would be a false
                                              claim and "yes" would assert a test nobody
                                              can confirm from the dump
  contrast named + interval      TESTED       an interval positioned against a null. This
                                              INCLUDES the coverage-mismatch refusal: the
                                              label pipeline withholds the direction of
                                              the verdict, but a threshold was still
                                              applied, and the direction is not the
                                              question this module asks. The two things
                                              section 12.9 decision 3 sends to UNKNOWN are
                                              tier E and the percent-scaled ratio; the
                                              coverage refusal is neither, and it cannot
                                              be "no" when a contrast interval is sitting
                                              right there
  no contrast + interval         NOT_APPLIED  a value with an interval around itself. The
                                              one branch that positively asserts no
                                              threshold was applied
  neither p-value nor interval   UNKNOWN      decision 1. Nothing was reported either way

Note what is NOT here: a verdict read off `tier_min`. Tier D is reached by four distinct
routes -- no analysis at all, the percent-scaled refusal, the coverage refusal, and a value
with no contrast -- and those routes carry three DIFFERENT answers to this module's
question. A mapping keyed on the tier alone would collapse them, which is why every
function here classifies the ANALYSIS ROW and the tier is never the input.

PRECEDENCE WHEN ANALYSES DISAGREE: ANY, THEN THE INFORMATIVE ONE
================================================================
Section 12.9 decision 2 fixes the first step: several analyses on one outcome that
disagree resolve by ANY. If any analysis carries a threshold test, the endpoint was
threshold-tested -- the alternative says an endpoint stops being threshold-tested because
the sponsor also posted a descriptive summary of it. Note this is a DIFFERENT level from
section 8.4's outstanding any/all choice, which is outcomes within a trial.

That leaves the order of the other two, which decision 2 does not reach, so it is stated
here: `VERDICT_PRECEDENCE` puts NOT_APPLIED above UNKNOWN. An outcome with one row
reporting a value-with-interval and another reporting nothing HAS a readable sponsor
statement, and deferring to the silent row would throw it away. UNKNOWN is the verdict for
an outcome where nothing readable was posted at all, not for one where something readable
was outvoted.

THE SELECTION BIAS IS SEVERE AND IS NOT A CAVEAT
================================================
This reference exists only for trials that posted, which is 16.4% overall, with phase 3
over-represented 2.31x and phase 1 at 0.18x. **It is therefore strongest exactly where
endpoint type matters least.** A phase 1 trial with nothing posted is the case the gate
exists for and this reference says nothing about it. `selection_report` exists so that
fact is printed as a table rather than written as a sentence somebody skips.
"""
from __future__ import annotations

from typing import Iterable, Optional

from trial_pos.services.endpoint_label import (
    DEFAULT_ALPHA, DEFAULT_COVERAGE_TOLERANCE_POINTS, DEFAULT_RATIO_SCALE_FLOOR,
    DESIGN_NI, analysis_design, contrast_family, is_geometric_ratio,
    is_percent_scaled_ratio, met_from_ci, met_from_p, parse_p_value,
)
from trial_pos.services.endpoint_type import (
    GATE_APPLICABLE, GATE_NOT_APPLICABLE, GATE_UNDETERMINABLE, GATE_VERDICTS,
)

# ---- the three states ------------------------------------------------------
THRESHOLD_TESTED = "threshold_tested"
THRESHOLD_NOT_APPLIED = "threshold_not_applied"
THRESHOLD_UNKNOWN = "threshold_unknown"

SPONSOR_VERDICTS = (THRESHOLD_TESTED, THRESHOLD_NOT_APPLIED, THRESHOLD_UNKNOWN)

SPONSOR_VERDICT_DOC = {
    THRESHOLD_TESTED: ("the sponsor applied a threshold to this endpoint: a p-value "
                       "against an alpha, or an interval positioned against a null"),
    THRESHOLD_NOT_APPLIED: ("the sponsor reported a value with an interval around "
                            "itself. No threshold was applied, so there is no verdict "
                            "for the endpoint-met label to read"),
    THRESHOLD_UNKNOWN: ("nothing readable was posted either way, or the threshold that "
                        "was applied is one this project does not read (a "
                        "non-inferiority margin, or bioequivalence containment bounds). "
                        "NEVER folded into 'no threshold applied'"),
}

# Which verdict wins when the analyses on one outcome disagree. ANY-tested comes from
# section 12.9 decision 2; NOT_APPLIED above UNKNOWN is this module's stated reading, on
# the ground that a readable statement beats a silent row. Iterated as a tuple so the
# precedence is the declared order rather than whatever order the counts were built in --
# the same construction as `REFUSAL_KINDS` in endpoint_label.
VERDICT_PRECEDENCE = (THRESHOLD_TESTED, THRESHOLD_NOT_APPLIED, THRESHOLD_UNKNOWN)

# ---- reasons, countable ----------------------------------------------------
# A refusal that is not counted vanishes into the population (section 10). Every branch of
# `analysis_threshold` names itself, so the cross-tab's cells can be decomposed by the
# route that produced them rather than guessed at.
REASON_P_VALUE = "p_value_posted"
REASON_NI_DESIGN = "ni_or_equivalence_design_margin_unreadable"
REASON_PERCENT_SCALED = "percent_scaled_ratio_containment_bounds"
REASON_GEOMETRIC_RATIO = "geometric_ratio_containment_bounds"
REASON_CONTRAST_INTERVAL = "contrast_interval_against_null"
REASON_VALUE_WITH_INTERVAL = "value_with_interval_around_itself"
REASON_NOTHING_POSTED = "neither_p_value_nor_interval"

SPONSOR_REASONS = (REASON_P_VALUE, REASON_NI_DESIGN, REASON_PERCENT_SCALED,
                   REASON_GEOMETRIC_RATIO, REASON_CONTRAST_INTERVAL,
                   REASON_VALUE_WITH_INTERVAL, REASON_NOTHING_POSTED)

# Every reason resolves to exactly one verdict, declared here rather than inferred from
# the branch it was returned by, so `test_every_reason_maps_to_its_verdict` can check that
# the function and this table agree.
REASON_VERDICT = {
    REASON_P_VALUE: THRESHOLD_TESTED,
    REASON_NI_DESIGN: THRESHOLD_UNKNOWN,
    REASON_PERCENT_SCALED: THRESHOLD_UNKNOWN,
    REASON_GEOMETRIC_RATIO: THRESHOLD_UNKNOWN,
    REASON_CONTRAST_INTERVAL: THRESHOLD_TESTED,
    REASON_VALUE_WITH_INTERVAL: THRESHOLD_NOT_APPLIED,
    REASON_NOTHING_POSTED: THRESHOLD_UNKNOWN,
}


# ---- one analysis row ------------------------------------------------------
def has_interval(ci_lower, ci_upper) -> bool:
    """Are both interval bounds present and parseable?

    Delegates to `met_from_ci` with a null SUPPLIED, which pins the meaning of its None:
    that function documents None as "interval or null value unavailable", so with the null
    given, None can only mean the interval is unreadable. Written this way rather than
    with a private float parser so there is exactly one place in the project that decides
    whether an AACT interval bound is readable.
    """
    return met_from_ci(ci_lower, ci_upper, 0.0)[0] is not None


def analysis_threshold(row: dict, alpha: float = DEFAULT_ALPHA,
                       ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                       coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS,
                       ) -> tuple[str, str]:
    """One outcome_analyses row -> (sponsor verdict, reason).

    Every branch delegates its reading to `endpoint_label`. `coverage_tolerance` is
    accepted and deliberately unused in the body: the coverage band decides the DIRECTION
    of a tier-C verdict, and this module asks only whether a threshold was applied, which
    a mismatched coverage does not change. It stays in the signature because the caller
    threads one settings bundle through every tier-derived function, and because a reader
    checking whether coverage was considered should find it named and explained rather
    than absent.
    """
    operator, value = parse_p_value(row.get("p_value"), row.get("p_value_modifier"))
    if met_from_p(operator, value, alpha)[0] is not None:
        return THRESHOLD_TESTED, REASON_P_VALUE
    if analysis_design(row.get("non_inferiority_type")) == DESIGN_NI:
        return THRESHOLD_UNKNOWN, REASON_NI_DESIGN
    lower, upper = row.get("ci_lower_limit"), row.get("ci_upper_limit")
    if is_percent_scaled_ratio(row.get("param_type"), lower, upper, ratio_scale_floor):
        return THRESHOLD_UNKNOWN, REASON_PERCENT_SCALED
    # A ratio of geometric means is an EQUIVALENCE statistic: the decision rule is
    # containment inside an acceptance window, not exclusion of the null. So the sponsor
    # did apply a threshold and it is not one this project can read -- `threshold_unknown`,
    # by the same argument section 12.9 decision 3 makes for the percent-scaled ratio and
    # for tier E. Decision 3 already covers this case in principle; the predicate simply
    # did not exist when it was written.
    #
    # NOT a cosmetic correction. Without it these rows fell through to
    # REASON_CONTRAST_INTERVAL and were counted as "the sponsor threshold-tested this
    # endpoint", which inflated the over-refusal cell by 714 of 2,359 -- 30% -- and made
    # the keyword rule look wrong for refusing endpoints the reference could not actually
    # adjudicate either. The same misreading was fixed in `endpoint_label` and not
    # propagated here, which is how one correction became two bugs.
    if is_geometric_ratio(row.get("param_type")):
        return THRESHOLD_UNKNOWN, REASON_GEOMETRIC_RATIO
    if not has_interval(lower, upper):
        return THRESHOLD_UNKNOWN, REASON_NOTHING_POSTED
    if contrast_family(row.get("param_type")) is None:
        return THRESHOLD_NOT_APPLIED, REASON_VALUE_WITH_INTERVAL
    return THRESHOLD_TESTED, REASON_CONTRAST_INTERVAL


def resolve_verdicts(verdicts: Iterable[str]) -> str:
    """Several verdicts -> one, by VERDICT_PRECEDENCE. UNKNOWN when there are none.

    Public because callers rolling up at a level this module does not model -- a phase
    stratum, a sponsor, an arbitrary grouping in an audit script -- would otherwise
    re-derive the precedence inline, and an inlined precedence is one that drifts from
    VERDICT_PRECEDENCE without any test noticing.
    """
    present = set(verdicts)
    for verdict in VERDICT_PRECEDENCE:
        if verdict in present:
            return verdict
    return THRESHOLD_UNKNOWN


_resolve = resolve_verdicts


# ---- one outcome -----------------------------------------------------------
def outcome_threshold(analyses: list, alpha: float = DEFAULT_ALPHA,
                      ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                      coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS,
                      ) -> dict:
    """All analysis rows for ONE primary outcome -> one sponsor verdict for it.

    Carries the per-reason counts through rather than returning a bare verdict, because
    the over-refusal cell is only interpretable once it is decomposed by the route that
    produced it: an over-refusal driven by `REASON_CONTRAST_INTERVAL` on geometric-mean
    ratios is the reference's own artefact, while one driven by `REASON_P_VALUE` is the
    keyword rule genuinely refusing an endpoint the sponsor tested against an alpha. Those
    two demand opposite responses and a count of the cell cannot tell them apart.
    """
    graded = [analysis_threshold(a, alpha, ratio_scale_floor, coverage_tolerance)
              for a in analyses]
    reasons = {reason: 0 for reason in SPONSOR_REASONS}
    for _, reason in graded:
        reasons[reason] += 1
    verdict = _resolve(v for v, _ in graded)
    return {"verdict": verdict, "n_analyses": len(analyses), "reasons": reasons,
            "n_tested": sum(1 for v, _ in graded if v == THRESHOLD_TESTED),
            "n_not_applied": sum(1 for v, _ in graded
                                 if v == THRESHOLD_NOT_APPLIED),
            "n_unknown": sum(1 for v, _ in graded if v == THRESHOLD_UNKNOWN),
            "disagreed": len({v for v, _ in graded}) > 1}


def trial_threshold(outcomes: dict, alpha: float = DEFAULT_ALPHA,
                    ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                    coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS,
                    ) -> dict:
    """{outcome key: [analysis rows]} for one trial -> one trial-level sponsor verdict.

    Rolls up by the SAME precedence, which makes it the ANY roll-up on the tested state --
    matching `DEFAULT_GATE_ROLLUP` in endpoint_type and `endpoint_met_strict`'s
    `any_primary_met`. If section 8.4 moves the label to ALL, this moves with it, for the
    reason stated there: otherwise the reference would disagree with the gate about trials
    on which neither of them was actually consulted.
    """
    per_outcome = {key: outcome_threshold(rows, alpha, ratio_scale_floor,
                                          coverage_tolerance)
                   for key, rows in outcomes.items()}
    reasons = {reason: 0 for reason in SPONSOR_REASONS}
    for record in per_outcome.values():
        for reason, count in record["reasons"].items():
            reasons[reason] += count
    return {"verdict": _resolve(r["verdict"] for r in per_outcome.values()),
            "n_outcomes": len(per_outcome),
            "outcomes": per_outcome,
            "reasons": reasons,
            "verdicts": {v: sum(1 for r in per_outcome.values()
                                if r["verdict"] == v)
                         for v in SPONSOR_VERDICTS}}


# ---- the cross-tab ---------------------------------------------------------
# The two cells that decide whether the six-class scheme survives. Named, because a cell
# referred to by its coordinates in a printout is a cell whose meaning has to be
# reconstructed by every reader.
CELL_OVER_REFUSAL = "over_refusal"
CELL_UNDER_REFUSAL = "under_refusal"
CELL_BOTH_REFUSE = "both_refuse"
CELL_BOTH_ALLOW = "both_allow"

# The five labelling cells of section 12.9 decision 7. `SPONSOR_UNREADABLE` is the frame
# that keeps the original phase x class stratification, and the two agreement cells get a
# small audit sample rather than none -- two wrong readings agreeing with each other is
# the failure mode that looks exactly like success.
CELL_SPONSOR_UNREADABLE = "sponsor_threshold_unknown"
CELL_RULE_UNREADABLE = "rule_undeterminable"

CELL_DOC = {
    CELL_OVER_REFUSAL: ("the rule refuses an endpoint the sponsor threshold-tested. THE "
                        "STOP CELL: if it is large the six-class scheme is carving "
                        "endpoints wrongly and no amount of hand labelling rescues it"),
    CELL_UNDER_REFUSAL: ("the rule allows an endpoint the sponsor did not threshold-test, "
                         "so the tool would return a probability for a question with no "
                         "answer. The failure the gate exists to prevent"),
    CELL_BOTH_REFUSE: "both refuse. Agreement, and agreement is not proof",
    CELL_BOTH_ALLOW: "both allow. Agreement, and agreement is not proof",
    CELL_SPONSOR_UNREADABLE: ("the sponsor reference has no verdict. NOT a disagreement, "
                              "and it must never be counted as one"),
    CELL_RULE_UNREADABLE: ("the keyword rule could not read the endpoint text. Also not a "
                           "disagreement: undeterminable is not a refusal"),
}

DECISIVE_CELLS = (CELL_OVER_REFUSAL, CELL_UNDER_REFUSAL)
AGREEMENT_CELLS = (CELL_BOTH_REFUSE, CELL_BOTH_ALLOW)
UNREADABLE_CELLS = (CELL_SPONSOR_UNREADABLE, CELL_RULE_UNREADABLE)
CELLS = DECISIVE_CELLS + AGREEMENT_CELLS + UNREADABLE_CELLS


def cell_for(gate: str, sponsor: str) -> str:
    """(gate verdict, sponsor verdict) -> which of the six labelling cells this is.

    Raises on an unknown verdict on either side rather than defaulting, for the reason
    `gate_for_class` raises: a verdict with no cell is a verdict somebody added without
    deciding what it means for the frame, and defaulting would pick a side silently.

    The rule's `undeterminable` is checked FIRST, so a rule-unreadable endpoint never
    lands in a disagreement cell whatever the sponsor said. The gate does not refuse on
    undeterminable (`refuses_estimate` is False there), so calling it a disagreement with
    a sponsor who tested would count a silence as an error.
    """
    if gate not in GATE_VERDICTS:
        raise ValueError(f"{gate!r} is not one of {GATE_VERDICTS}")
    if sponsor not in SPONSOR_VERDICTS:
        raise ValueError(f"{sponsor!r} is not one of {SPONSOR_VERDICTS}")
    if gate == GATE_UNDETERMINABLE:
        return CELL_RULE_UNREADABLE
    if sponsor == THRESHOLD_UNKNOWN:
        return CELL_SPONSOR_UNREADABLE
    if gate == GATE_NOT_APPLICABLE:
        return (CELL_OVER_REFUSAL if sponsor == THRESHOLD_TESTED
                else CELL_BOTH_REFUSE)
    if gate == GATE_APPLICABLE:
        return (CELL_BOTH_ALLOW if sponsor == THRESHOLD_TESTED
                else CELL_UNDER_REFUSAL)
    # Unreachable while GATE_VERDICTS has three members, and written anyway: as a
    # fallthrough this branch would silently treat a NEW gate verdict as "applicable",
    # which is the one direction that puts a trial in a disagreement cell it was never
    # judged into. `test_every_gate_verdict_has_an_explicit_branch` pins it.
    raise ValueError(f"{gate!r} is in GATE_VERDICTS but has no cell branch; add one "
                     f"deliberately rather than letting it default to applicable")


def crosstab(pairs: Iterable[tuple]) -> dict:
    """[(gate verdict, sponsor verdict), ...] -> the full 3x3 plus the named cells.

    Both the raw 3x3 and the named cells are returned. The 3x3 is what makes the named
    cells auditable -- a reader can add up the grid and check the collapse -- and
    `test_named_cells_partition_the_grid` asserts exactly that, so the two can never
    drift apart.
    """
    grid = {(g, s): 0 for g in GATE_VERDICTS for s in SPONSOR_VERDICTS}
    cells = {cell: 0 for cell in CELLS}
    total = 0
    for gate, sponsor in pairs:
        total += 1
        grid[(gate, sponsor)] += 1
        cells[cell_for(gate, sponsor)] += 1
    return {"total": total, "grid": grid, "cells": cells}


def decisive_total(table: dict) -> int:
    """How many units the sponsor reference and the rule BOTH gave a verdict on.

    This is the denominator the two disagreement rates are quoted against, and it is not
    the total: the `threshold_unknown` and `undeterminable` cells are units where one side
    declined, so including them would shrink every rate by the amount of silence in the
    corpus and make the rule look better the less the sponsors posted.
    """
    cells = table["cells"]
    return sum(cells[cell] for cell in DECISIVE_CELLS + AGREEMENT_CELLS)


def cell_rate(table: dict, cell: str) -> Optional[float]:
    """One cell as a share of `decisive_total`. None when that denominator is zero.

    None rather than 0.0: a rate with no denominator is not a rate of zero, and returning
    0.0 would let an empty corpus print as a passing result.
    """
    if cell not in CELLS:
        raise ValueError(f"{cell!r} is not one of {CELLS}")
    denominator = decisive_total(table)
    if not denominator:
        return None
    return table["cells"][cell] / denominator


def over_refusal_rate(table: dict) -> Optional[float]:
    """The share of both-decided units where the rule refused and the sponsor tested.

    Quoted against `decisive_total` rather than against the refusals alone. Both
    denominators are defensible and they answer different questions -- "how often is the
    rule wrong in this direction" against "how often is a refusal wrong" -- so the choice
    is stated instead of assumed: this is the one that moves when the rule refuses MORE,
    and over-refusal is a claim about the rule's appetite for refusing, not just about the
    purity of the refusals it already made. `refusal_precision` below reports the other.
    """
    return cell_rate(table, CELL_OVER_REFUSAL)


def under_refusal_rate(table: dict) -> Optional[float]:
    """The share of both-decided units where the rule allowed and the sponsor did not test."""
    return cell_rate(table, CELL_UNDER_REFUSAL)


def refusal_precision(table: dict) -> Optional[float]:
    """Of the units the rule REFUSED and the sponsor decided, the share the sponsor also
    refused.

    **This is the statistic that answers section 12.9 decision 4**, and the reason is a
    denominator argument. "Is the six-class scheme carving endpoints wrongly?" is a
    question about the classes marked REFUSE, so the denominator has to be the refusals.
    `over_refusal_rate` divides by every decided unit instead, which means it falls
    whenever the rule refuses LESS OFTEN -- a rule that refused nothing would score a
    perfect 0.0 on it while gating nothing at all. Both are reported, and a stop rule
    keyed only on the pooled rate can be passed by a rule with no appetite for refusing
    rather than by a rule that refuses accurately.

    None when the rule refused nothing the sponsor decided -- which is itself a finding
    and must not print as 1.0.
    """
    cells = table["cells"]
    refused = cells[CELL_BOTH_REFUSE] + cells[CELL_OVER_REFUSAL]
    if not refused:
        return None
    return cells[CELL_BOTH_REFUSE] / refused


def false_refusal_share(table: dict) -> Optional[float]:
    """1 - refusal_precision: of decided refusals, the share the sponsor contradicts.

    The same quantity the other way up, provided because a stop rule reads more plainly
    as "stop when the false-refusal share exceeds X" than as "stop when precision falls
    below 1-X", and a threshold that has to be mentally inverted is a threshold that gets
    misread.
    """
    precision = refusal_precision(table)
    return None if precision is None else 1.0 - precision


def allowance_precision(table: dict) -> Optional[float]:
    """Of the units the rule ALLOWED and the sponsor decided, the share the sponsor tested.

    Printed beside `refusal_precision` because the two together diagnose WHERE a rule is
    wrong, which a pooled rate cannot. A rule with high allowance precision and low
    refusal precision is not uniformly noisy: it reads threshold-tested endpoints well
    and its four refusing classes are mis-drawn. Those call for different repairs.
    """
    cells = table["cells"]
    allowed = cells[CELL_BOTH_ALLOW] + cells[CELL_UNDER_REFUSAL]
    if not allowed:
        return None
    return cells[CELL_BOTH_ALLOW] / allowed


def disagreement_ratio(table: dict) -> Optional[float]:
    """over-refusals per under-refusal. None when there are no under-refusals.

    `one_directional` is a BOOLEAN and follows the project's existing `one_sided`
    convention in `agreement.asymmetries`, which asks whether one side is empty. That
    convention cannot see an imbalance that is lopsided without being empty -- eight
    over-refusals for every under-refusal reports as "not one-directional" and reads as
    reassurance. The ratio is printed so the imbalance is a number rather than a flag,
    and the two are reported together rather than one replacing the other, because
    changing the established convention here would put this module's asymmetry test out
    of step with the one section 12.5 pre-registered.

    None rather than infinity when nothing errs the other way: an undefined ratio is the
    strongest form of the finding and must not be formatted as a float.
    """
    cells = table["cells"]
    under = cells[CELL_UNDER_REFUSAL]
    if not under:
        return None
    return cells[CELL_OVER_REFUSAL] / under


def one_directional(table: dict, min_pair: int = 5) -> Optional[bool]:
    """Is the disagreement one-directional? None when neither cell is big enough to say.

    Pre-registered in section 12.5 as a side condition that disqualifies at ANY kappa: a
    rule that errs one way is BIASED rather than noisy, and a bias is a rule that needs
    changing rather than a rule that needs more labels. Applied here to the same shape of
    evidence one level earlier, before any hand label exists.

    True when one decisive cell is non-trivially populated and the other is below
    `min_pair`. None when both are below it, because "no evidence of bias" and "no
    evidence" are different claims.
    """
    cells = table["cells"]
    over, under = cells[CELL_OVER_REFUSAL], cells[CELL_UNDER_REFUSAL]
    if over < min_pair and under < min_pair:
        return None
    return over < min_pair or under < min_pair


# ---- the selection bias, as a table ----------------------------------------
def selection_report(posted_by_group: dict, population_by_group: dict) -> dict:
    """Per-group posting rate and representation ratio, for printing as a TABLE.

    Section 12.7 records the blind spot in prose and section 12.9 requires it printed:
    posting is selected, so this reference is strongest exactly where endpoint type
    matters least. A representation ratio of 2.31 on phase 3 and 0.18 on phase 1 is the
    difference between a reference that covers the gate's hard cases and one that does
    not, and that is a number rather than a caveat.

    `ratio` is the group's share of the POSTED units over its share of the population, so
    1.0 means proportionally represented. None where a denominator is zero, never 0.0.
    """
    groups = sorted(set(posted_by_group) | set(population_by_group))
    posted_total = sum(posted_by_group.get(g, 0) for g in groups)
    population_total = sum(population_by_group.get(g, 0) for g in groups)
    rows = {}
    for group in groups:
        posted = posted_by_group.get(group, 0)
        population = population_by_group.get(group, 0)
        posted_share = posted / posted_total if posted_total else None
        population_share = (population / population_total if population_total
                            else None)
        rows[group] = {
            "posted": posted,
            "population": population,
            "posting_rate": posted / population if population else None,
            "posted_share": posted_share,
            "population_share": population_share,
            "ratio": (posted_share / population_share
                      if posted_share is not None and population_share
                      else None),
        }
    return {"groups": rows, "posted_total": posted_total,
            "population_total": population_total,
            "posting_rate": (posted_total / population_total
                             if population_total else None)}