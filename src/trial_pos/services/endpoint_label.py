"""Derive a per-trial primary-endpoint-met label from AACT posted results. Pure engine.

This is the label for the per-trial predictor. Everything upstream in this project predicts
ChEMBL "drug approved", which is a different target; that mismatch is why the existing
artifact is a prior rather than a trial predictor. The label here comes from the trial's own
posted primary-outcome analyses.

=============================================================================
THE FOUR TIERS -- read this before using any column this module produces
=============================================================================
A label is only as trustworthy as the rule that produced it, so every row carries its rule.
`TIER_DOC` below is the single source of truth for these definitions; the puller prints it,
the spec document quotes it, and no analysis should report a pooled number across tiers
without saying which tiers it pooled.

  TIER_A  superiority analysis with a usable p-value.
          met := p <= alpha. The clean case. Headline analyses use A (+B).

  TIER_B  non-inferiority or equivalence analysis with a usable p-value.
          met := p <= alpha, meaning non-inferiority was demonstrated. Direction of the
          comparison differs from A, the mapping to met/not-met does not. AACT does not
          carry the non-inferiority margin, so this is taken as the sponsor reported it.

  TIER_C  no p-value, but a confidence interval and an interpretable parameter type.
          met := the interval excludes the null (0 for differences, 1 for ratios).
          Weaker than A/B and EXCLUDED from headline numbers. Use it to test whether
          conclusions survive the wider label, not to make them.

  TIER_D  no decidable analysis: single-arm trials, descriptive-only postings, trials with
          primary outcomes but no analysis rows. endpoint_met is UNKNOWN here. These trials
          are NOT dropped from the project -- they keep a phase-advancement label, which
          needs no statistical structure. Different coverage per target is expected.

=============================================================================
KNOWN LIMITATION -- direction of effect is not structurally encoded
=============================================================================
A p-value below alpha says the comparison reached significance. It does not say the
experimental arm was the favoured one. AACT has no structured field for direction, so
"met" here means "the pre-specified comparison reached its threshold", which is what the
sponsor was testing but is not identical to "the drug won". Sponsors rarely post a
significant primary that disfavours their own arm, so the practical error rate is low, but
it is nonzero and it is not measurable from AACT alone. Tier C inherits this in a stronger
form, since an interval excluding the null is directionally agnostic by construction.

=============================================================================
STRICT vs BROAD
=============================================================================
strict  labels from posted analyses only. Its negative class is "ran to completion,
        analysed, posted a null result" -- which systematically omits the most severe
        failures, the trials that stopped early or never posted.
broad   additionally reads terminated-for-futility as 0 and stopped-early-for-efficacy
        as 1, recovering that omitted failure mode at the cost of a mixed label.
Strict is the headline; broad is the sensitivity analysis. Report both, never silently
pool them.

=============================================================================
R7 -- these fields are LABEL INPUTS and must never become features
=============================================================================
`LABEL_DERIVED_FIELDS` enumerates them so a feature builder can assert against it rather
than relying on someone remembering. `why_stopped` and `overall_status` feed the broad
label; `termination_score` and `safety_termination` in pull_aact.py are derived from
`why_stopped` and inherit the contamination. They are excluded from BOTH label variants'
feature sets, not just broad, so the two models stay comparable.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# ---- tiers -----------------------------------------------------------------
TIER_A = "A_superiority_p"
TIER_B = "B_noninferiority_p"
TIER_C = "C_interval_only"
TIER_D = "D_no_analysis"

TIER_ORDER = (TIER_A, TIER_B, TIER_C, TIER_D)          # strongest -> weakest
HEADLINE_TIERS = (TIER_A, TIER_B)

TIER_DOC = {
    TIER_A: "superiority analysis, p-value present; met := p <= alpha",
    TIER_B: "non-inferiority/equivalence analysis, p-value present; met := p <= alpha",
    TIER_C: "no p-value; CI excludes null (0 for differences, 1 for ratios). NOT headline",
    TIER_D: "no decidable analysis (single-arm / descriptive-only); endpoint_met UNKNOWN",
}

# Fields that feed a label and therefore can never be features (R7).
LABEL_DERIVED_FIELDS = (
    "why_stopped", "overall_status", "why_stopped_class",
    "termination_score", "safety_termination",
    "results_first_posted_date", "results_first_submitted_date", "results_posted",
    # AACT's own results-posted flag (calculated_values.were_results_reported). Pulled by
    # the population step because the posting-bias audit needs it as the OUTCOME it is
    # analysing; registered here so it can never cross into a feature matrix.
    "were_results_reported",
)

DEFAULT_ALPHA = 0.05

# label_rule is a human-readable provenance string for one CSV cell, not data to parse.
# Capped so a trial with many primary outcomes cannot produce an unreadable row.
LABEL_RULE_MAXLEN = 300

# ---- p-value parsing -------------------------------------------------------
_NUM = re.compile(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_OPS = ("<=", ">=", "<", ">", "=")


def parse_p_value(raw, modifier: str = "") -> tuple[Optional[str], Optional[float]]:
    """AACT p_value (+ p_value_modifier) -> (operator, value); (None, None) if unusable.

    AACT stores the comparator separately in `p_value_modifier`, but it also turns up
    inline in the value string, and either can be blank. Returns the operator normalised
    to one of "<", ">", "=" so the caller never has to re-parse.
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s or s.lower() in ("na", "nan", "none", "null", "not applicable"):
        return None, None
    op = ""
    mod = str(modifier or "").strip()
    for cand in _OPS:                       # explicit modifier column wins
        if mod.startswith(cand):
            op = cand
            break
    if not op:
        for cand in _OPS:                   # else look for an inline comparator
            if s.startswith(cand):
                op = cand
                s = s[len(cand):].strip()
                break
    m = _NUM.search(s)
    if not m:
        return None, None
    try:
        val = float(m.group(1))
    except ValueError:
        return None, None
    if not 0.0 <= val <= 1.0:               # not a p-value; refuse rather than guess
        return None, None
    op = {"<=": "<", ">=": ">", "": "="}.get(op, op)
    return op, val


def met_from_p(op: Optional[str], value: Optional[float],
               alpha: float = DEFAULT_ALPHA) -> tuple[Optional[int], str]:
    """(met, reason) from a parsed p-value. None = indeterminate, deliberately.

    A bounded p-value only decides the question when the bound falls on the decisive side
    of alpha: "p < 0.001" is met at alpha 0.05, "p < 0.5" is not decidable at all. Forcing
    the ambiguous cases either way would inject label noise, which is worse in a target
    than in a feature.
    """
    if op is None or value is None:
        return None, "no usable p-value"
    if op == "=":
        return (1, f"p={value:g} <= alpha={alpha:g}") if value <= alpha else \
               (0, f"p={value:g} > alpha={alpha:g}")
    if op == "<":
        if value <= alpha:
            return 1, f"p<{value:g} <= alpha={alpha:g}"
        return None, f"p<{value:g} straddles alpha={alpha:g}"
    if op == ">":
        if value >= alpha:
            return 0, f"p>{value:g} >= alpha={alpha:g}"
        return None, f"p>{value:g} straddles alpha={alpha:g}"
    return None, f"unrecognised operator {op!r}"


# ---- confidence-interval fallback (tier C) ---------------------------------
# Parameter-type -> value of no effect, for the tier-C interval rule.
#
# ORDER: CONTRAST FIRST, single-arm refusal second. This ordering was wrong in the first
# version and the gap audit caught it -- undecided rows jumped 150 -> 245 because a
# single-arm-first check blocked "Difference in Percentage" (on "percentage"),
# "Proportion difference" (on "proportion"), "Difference in response rate" (on
# "response") and "Geometric Mean Ratio" (on "mean"). A string that NAMES a contrast is
# a contrast whatever noun follows; the single-arm refusal only applies when no contrast
# word is present at all.
#
# The single-arm refusal still matters and is not negotiable: an interval around a 40%
# objective response rate "excludes zero" trivially, so labelling it endpoint-met would
# manufacture positives out of descriptive statistics. Those types stay undecided.
_CONTRAST_RATIO_RX = tuple(re.compile(r, re.I) for r in (
    r"\bratio\b", r"\bhazard\b", r"\bcox\b", r"\bhr\b", r"\bodds\b",
    r"relative risk",
))
_CONTRAST_DIFF_RX = tuple(re.compile(r, re.I) for r in (
    r"\bdiffer",                      # difference, differences, differential
    r"\bcontrast\b", r"\bchange\b", r"\bslope\b", r"\bdiff\b",
    r"reduction\s+(vs\.?|versus|over|compared)",
    r"\bvs\.?\s+(placebo|control|comparator)", r"\bversus\s+(placebo|control)",
))
# A ratio expressed as a PERCENT has null 100, not 1 (bioequivalence GMR(%) intervals run
# around 100). The field does not distinguish, so refuse rather than guess wrong by 99.
_PERCENT_RATIO_RX = re.compile(r"(ratio|\bgmr\b).{0,12}(%|percent)|(%|percent).{0,12}ratio",
                               re.I)
# Single-arm quantities: no contrast exists, so no null exists.
_SINGLE_ARM_RX = tuple(re.compile(r, re.I) for r in (
    r"\bresponse\b", r"\bproportion\b", r"\borr\b", r"percent(age)? of participant",
    r"retention rate", r"\bprobability\b", r"point estimate", r"\bpfs\b", r"\bos\b",
    r"survival rate", r"median time", r"exit rate", r"adverse event", r"toxicity grade",
    r"effect size", r"binomial", r"normal approximation", r"unconditional exact",
    r"\bpercent(age)?\b", r"\bmean\b", r"\bmedian\b", r"\bestimate\b",
))


def null_value_for(param_type) -> Optional[float]:
    """Value of no effect for an AACT param_type: 1 for ratios, 0 for differences.

    Returns None when the type names no contrast (single-arm quantities), when it is
    unrecognised, or when it is a ratio expressed as a percentage (null 100 vs 1 is not
    determinable from the field). Either way the analysis falls to tier D rather than
    being labelled on a meaningless or mis-centred interval test.
    """
    s = str(param_type or "").strip()
    if not s:
        return None
    if _PERCENT_RATIO_RX.search(s):
        return None
    if any(rx.search(s) for rx in _CONTRAST_RATIO_RX):
        return 1.0
    if any(rx.search(s) for rx in _CONTRAST_DIFF_RX):
        return 0.0
    if any(rx.search(s) for rx in _SINGLE_ARM_RX):
        return None
    return None


def _as_float(x) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if not s or s.lower() in ("na", "nan", "none", "null"):
        return None
    m = _NUM.search(s)
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def met_from_ci(lower, upper, null: Optional[float]) -> tuple[Optional[int], str]:
    """(met, reason) from a confidence interval. Directionally agnostic -- see the module
    docstring's limitation note. Excluding the null counts as met."""
    lo, hi = _as_float(lower), _as_float(upper)
    if lo is None or hi is None or null is None:
        return None, "interval or null value unavailable"
    if lo > hi:
        lo, hi = hi, lo
    if lo > null or hi < null:
        return 1, f"CI [{lo:g},{hi:g}] excludes null={null:g}"
    return 0, f"CI [{lo:g},{hi:g}] contains null={null:g}"


# ---- analysis design detection --------------------------------------------
def analysis_design(non_inferiority_type) -> str:
    """AACT non_inferiority_type -> 'ni' | 'superiority' | 'unstated'.

    AACT ships this as an uppercase underscored enum ('SUPERIORITY_OR_OTHER',
    'NON_INFERIORITY_OR_EQUIVALENCE'), and older extracts use hyphens or spaces. All
    non-letters are collapsed to single spaces first so every spelling matches the same
    patterns -- without that, 'NON_INFERIORITY' misses every non-inferiority pattern and
    silently lands in tier A.

    Checks non-inferiority/equivalence FIRST, because the combined values also contain
    other words and a value naming non-inferiority must not read as plain superiority.
    """
    s = str(non_inferiority_type or "").strip().lower()
    if not s:
        return "unstated"
    s = re.sub(r"[^a-z]+", " ", s)
    if "non inferiority" in s or "noninferiority" in s or "equivalence" in s:
        return "ni"
    if "superiority" in s:
        return "superiority"
    return "unstated"


def classify_analysis(row: dict, alpha: float = DEFAULT_ALPHA) -> tuple[str, Optional[int], str]:
    """One outcome_analyses row -> (tier, met, reason).

    Precedence is p-value before interval, and design type decides A vs B. An unstated
    design with a p-value is treated as superiority and the reason records that
    assumption, so it can be counted later rather than disappearing.
    """
    design = analysis_design(row.get("non_inferiority_type"))
    op, val = parse_p_value(row.get("p_value"), row.get("p_value_modifier"))
    met, reason = met_from_p(op, val, alpha)
    if met is not None:
        tier = TIER_B if design == "ni" else TIER_A
        if design == "unstated":
            reason += "; design unstated, assumed superiority"
        return tier, met, reason
    null = null_value_for(row.get("param_type"))
    met_ci, reason_ci = met_from_ci(row.get("ci_lower_limit"), row.get("ci_upper_limit"), null)
    if met_ci is not None:
        return TIER_C, met_ci, reason_ci
    return TIER_D, None, f"{reason}; {reason_ci}"


# ---- outcome- and trial-level aggregation ---------------------------------
def _best_tier(tiers: Iterable[str]) -> Optional[str]:
    # materialise first: `tiers` may be a generator, and re-consuming it inside the
    # comprehension would silently yield an empty set after the first iteration
    present_set = set(tiers)
    present = [t for t in TIER_ORDER if t in present_set]
    return present[0] if present else None


def aggregate_outcome(analyses: list[dict], alpha: float = DEFAULT_ALPHA) -> dict:
    """All analysis rows for ONE primary outcome -> one verdict for that outcome.

    A single outcome often carries several analyses (timepoints, subgroups, alternative
    comparators). The rule: take the strongest tier present, then within that tier count
    the outcome as met if ANY analysis met. This is optimistic within an outcome, which is
    why n_analyses is carried through -- an outcome resting on 1 of 9 analyses is visible
    downstream rather than indistinguishable from a clean single result.
    """
    graded = [classify_analysis(a, alpha) for a in analyses]
    decidable = [(t, m, r) for t, m, r in graded if m is not None]
    if not decidable:
        return {"tier": TIER_D, "met": None, "n_analyses": len(analyses),
                "n_decidable": 0, "reason": graded[0][2] if graded else "no analysis rows"}
    tier = _best_tier(t for t, _, _ in decidable)
    in_tier = [(m, r) for t, m, r in decidable if t == tier]
    met = 1 if any(m == 1 for m, _ in in_tier) else 0
    reason = next(r for m, r in in_tier if m == met)
    return {"tier": tier, "met": met, "n_analyses": len(analyses),
            "n_decidable": len(decidable), "reason": reason}


def aggregate_trial(outcomes: dict[str, list[dict]], alpha: float = DEFAULT_ALPHA) -> dict:
    """{outcome_id: [analysis rows]} for one trial -> the multi-endpoint label family.

    All four representations are carried, per the decision to keep every reading rather
    than collapse early: how many primary outcomes were decidable, how many were met, the
    proportion, whether at least one was met, and whether all were met.

    tier_min is the WEAKEST tier any counted outcome relied on, and it is the correct
    filter for headline analyses: a trial whose all_primary_met depends on a tier-C
    outcome is a tier-C trial no matter how clean its other outcomes were.
    """
    verdicts = {oid: aggregate_outcome(rows, alpha) for oid, rows in outcomes.items()}
    counted = {oid: v for oid, v in verdicts.items() if v["met"] is not None}
    n_primary = len(verdicts)
    n_analyzed = len(counted)
    n_met = sum(v["met"] for v in counted.values())
    tiers = [v["tier"] for v in counted.values()]
    ranked = [t for t in TIER_ORDER if t in set(tiers)]
    mix = "|".join(f"{t.split('_')[0]}:{tiers.count(t)}" for t in ranked)
    out = {
        "n_primary_outcomes": n_primary,
        "n_primary_analyzed": n_analyzed,
        "n_primary_met": n_met,
        "frac_primary_met": (n_met / n_analyzed) if n_analyzed else None,
        "any_primary_met": (1 if n_met > 0 else 0) if n_analyzed else None,
        "all_primary_met": (1 if n_met == n_analyzed else 0) if n_analyzed else None,
        "n_analyses_total": sum(v["n_analyses"] for v in verdicts.values()),
        "tier_max": ranked[0] if ranked else TIER_D,
        "tier_min": ranked[-1] if ranked else TIER_D,
        "tier_mix": mix or "D:0",
        "label_rule": "; ".join(
            sorted({v["reason"] for v in counted.values()}))[:LABEL_RULE_MAXLEN]
                      or "no decidable primary analysis",
    }
    return out


# ---- why_stopped classification (broad label input only) ------------------
# Rewritten after the live gap audit found 105 candidate misses in 1,055 stated reasons.
# The failure mode was rigid literals: "lack of efficacy" matched but "lack of DRUG
# efficacy" did not, "failed to meet" matched but "failure to meet" did not. These are
# regexes with a word or two of slack between qualifier and object.
#
# PRECEDENCE, and why it is in this order:
#   efficacy_success   a stop FOR efficacy is a positive and shares vocabulary with
#                      futility, so it must be tested first
#   external_evidence  "another trial showed inferior activity" -- this trial's own
#                      endpoint was never assessed, so it is not this trial's result
#   benefit_risk       "overall benefit to risk profile was not favourable" mixes
#                      efficacy and safety and cannot be assigned to either
#   futility           the efficacy-failure class, and the only negative broad reads
#   safety / business / operational / other
_EFFICACY_SUCCESS_RX = (
    r"\b(met|achieved|reached)\b(?:\W+\w+){0,2}\W+(primary|endpoint|objective|efficacy)",
    r"overwhelming efficacy", r"positive interim", r"efficacy demonstrated",
    r"demonstrated efficacy", r"stopped for efficacy", r"early efficacy",
    r"success at interim", r"efficacy boundary",
)
_EXTERNAL_RX = (
    r"\b(another|other|similar|different|separate)\s+(trial|study|studies|program)",
    # the gap audit showed real text naming the other study obliquely: "in the main study
    # T-Force GOLD", "another associated study" -- qualifier and noun are not adjacent
    r"\b(main|pivotal|parent|companion|associated|related)\s+(study|trial)\b",
    r"\bexternal\s+(data|evidence|trial|study)",
    r"results\s+of\s+(a|an|another|other)\b",
    # real AACT text is sometimes malformed ("data from a similar did not show efficacy")
    # and drops the noun, so match the qualifier alone when it follows an article
    r"\b(a|another|the)\s+similar\b",
)
_BENEFIT_RISK_RX = (
    # separator class includes ':' -- real text writes "Unfavourable benefit:risk"
    r"benefit[\s/\-:]*(to|vs\.?|versus)?[\s/\-:]*risk",
    r"risk[\s/\-:]*(to|vs\.?|versus)?[\s/\-:]*benefit",
)
_FUTILITY_RX = (
    # (qualifier) (0-2 words) (efficacy-ish object). Qualifiers match as PREFIXES, so
    # "insufficiently" counts as well as "insufficient"; "not" is included because real
    # text says "the treatment was not effective".
    r"\b(lack|absence|insufficien|inadequa|poor|low|no|without|not|little)\w*\b"
    r"(?:\W+\w+){0,3}\W+(efficac|effect|response|benefit|activity|improvement|differ)",
    # (negated verb) (0-3 words) (achievement verb)
    r"\b(did not|does not|do not|failed to|failure to|unable to|not)\b(?:\W+\w+){0,3}"
    r"\W+(meet|met|achiev|demonstrat|show|reach|support|merit|improv)",
    r"\binefficac", r"\bineffective", r"\bfutil",
    r"\b(failed|missed)\b(?:\W+\w+){0,2}\W+primary",
    r"\bno\s+(statistical|significant|clinical)",
    r"\bnot\s+(statistically\s+)?significan",
    r"\b(discouraging|disappointing|negative)\s+(result|outcome|data|effect|finding)",
    r"\bno\s+(response|benefit|improvement|impact|positive)",
    r"\bunlikely\s+to\b",
    r"\breduced\s+efficacy", r"\binferior\b",
    r"\btreatment failure\b",
    # "the decision to terminate was completely related to efficacy"
    r"\b(related to|due to|because of|based on)\s+(the\s+)?(lack of\s+)?efficac",
)
# These VETO futility and efficacy_success only. Flexible patterns would otherwise read
# "no safety or efficacy concerns" as futility -- the exact inversion the fixtures caught.
# A veto costs a negative; a false positive corrupts the target. The asymmetry is chosen.
# Each pattern must match the WHOLE negated span, object included, because the span is
# deleted before classification. "[^.;]*" runs to the end of the sentence: a disclaimer
# names what it disclaims ("not due to ... lack of efficacy"), and leaving the object
# behind would turn the disclaimer into a positive match for the very thing it denies.
_NEGATION_GUARD = (
    r"\bno\b[\w\s/,\-]{0,40}\bconcerns?\b[^.;]*",
    r"\bnot\s+(due to|related to|because of|attributable to|caused by|prompted by)\b[^.;]*",
    r"\bunrelated to\b[^.;]*",
    r"\bno\s+(new\s+)?safety\s+(signal|issue|finding)[^.;]*",
    r"\bno\s+need\b[^.;]*",
    r"\bno longer\s+(necessary|needed|required)\b[^.;]*",
)
_SAFETY_RX = (
    r"adverse event", r"\bsafety\b", r"toxicit", r"serious adverse", r"unacceptable risk",
    r"\bdeath", r"side effect", r"tolerat", r"tolerabilit",
)
_BUSINESS_RX = (
    r"\bbusiness\b", r"strategic", r"funding", r"financ", r"sponsor decision",
    r"portfolio", r"company decision", r"commercial", r"prioriti", r"budget",
    r"discontinued the (manufactur|development)",
)
_OPERATIONAL_RX = (
    r"enroll", r"accru", r"recruit", r"covid", r"pandemic", r"supply", r"logistic",
    r"investigator", r"site clos", r"\bstaff", r"feasibilit", r"infeasib",
    r"registration", r"administrativ",
)

STOP_CLASSES = ("none", "efficacy_success", "external_evidence", "benefit_risk",
                "futility", "safety", "business", "operational", "other")

# Only these two feed the broad label. external_evidence and benefit_risk are recorded
# and then deliberately left out: the first is not this trial's result, the second cannot
# be attributed to efficacy rather than safety.
_BROAD_NEGATIVE_CLASSES = ("futility",)
_BROAD_POSITIVE_CLASSES = ("efficacy_success",)

_FAMILIES = tuple(
    (name, tuple(re.compile(r, re.I) for r in pats)) for name, pats in (
        ("efficacy_success", _EFFICACY_SUCCESS_RX),
        ("external_evidence", _EXTERNAL_RX),
        ("benefit_risk", _BENEFIT_RISK_RX),
        ("futility", _FUTILITY_RX),
        ("safety", _SAFETY_RX),
        ("business", _BUSINESS_RX),
        ("operational", _OPERATIONAL_RX),
    )
)
_GUARD_RX = tuple(re.compile(r, re.I) for r in _NEGATION_GUARD)


def _strip_negated(text: str) -> str:
    """Delete negated spans before classification.

    Two earlier designs failed on real AACT text. A document-wide veto threw away genuine
    futility whenever a later clause disclaimed safety ("Lack of efficacy of the drug; no
    safety concern"). Splitting into clauses then vetoing per clause broke the opposite
    way: splitting on "and" tore "No safety and/or efficacy concerns" into "No safety"
    plus a fragment, and the orphaned word matched the safety family, misfiling five
    business-reason terminations.

    Deleting the span keeps the negation attached to its object and leaves the rest of
    the sentence intact, so both cases resolve correctly with one pass and no splitting.
    """
    for rx in _GUARD_RX:
        text = rx.sub(" ", text)
    return text


def classify_stop_reason(why_stopped) -> str:
    """Free-text why_stopped -> one of STOP_CLASSES.

    Conservative by construction: text matching no family becomes "other", never
    "futility". A miss costs coverage in the broad variant; a false positive corrupts the
    target, which is strictly worse.
    """
    s = str(why_stopped or "").strip()
    if not s or s.lower() in ("na", "nan", "none", "null"):
        return "none"
    residue = _strip_negated(s)
    for name, patterns in _FAMILIES:
        if any(rx.search(residue) for rx in patterns):
            return name
    return "other"


def broad_label(strict_value: Optional[int], stop_class: str,
                include_safety: bool = False) -> tuple[Optional[int], str]:
    """(value, source) for the broad variant. A posted analysis always wins.

    include_safety is off by default: a safety termination is a program failure but says
    nothing about whether the efficacy endpoint would have been met, so folding it into an
    endpoint-met target mixes two different questions. The flag exists so the effect of
    that choice can be measured rather than argued about.

    external_evidence and benefit_risk are recorded by the classifier and deliberately
    NOT read here. A stop citing another trial's results is not this trial's endpoint, and
    an unfavourable benefit-risk profile cannot be attributed to efficacy rather than
    safety. Both stay unknown, which keeps the broad negative class interpretable.
    """
    if strict_value is not None:
        return strict_value, "analysis"
    if stop_class in _BROAD_NEGATIVE_CLASSES:
        return 0, "futility_stop"
    if stop_class in _BROAD_POSITIVE_CLASSES:
        return 1, "efficacy_stop"
    if include_safety and stop_class == "safety":
        return 0, "safety_stop"
    return None, "unknown"


def label_row(trial: dict, outcomes: dict[str, list[dict]],
              alpha: float = DEFAULT_ALPHA,
              broad_includes_safety: bool = False) -> dict:
    """One trial's studies row + its primary-outcome analyses -> one label record.

    `strict` uses any_primary_met, which is the most permissive of the four multi-endpoint
    readings; the others travel alongside so the modelling choice stays open. Nothing here
    filters by tier -- filtering is the caller's decision and has to be stated in the
    result, not buried in the label build.
    """
    agg = aggregate_trial(outcomes, alpha)
    stop_class = classify_stop_reason(trial.get("why_stopped"))
    strict = agg["any_primary_met"]
    broad, broad_src = broad_label(strict, stop_class, broad_includes_safety)
    rec = {
        "nct_id": trial.get("nct_id"),
        "why_stopped_class": stop_class,
        "endpoint_met_strict": strict,
        "endpoint_met_broad": broad,
        "label_source_strict": "analysis" if strict is not None else "unknown",
        "label_source_broad": broad_src,
        "alpha_used": alpha,
    }
    rec.update(agg)
    return rec