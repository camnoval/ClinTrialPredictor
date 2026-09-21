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
TIER_E = "E_ni_interval"
TIER_D = "D_no_analysis"

# strongest -> weakest. E sits between C and D because such a row carries MORE information
# than tier D (there is a real interval) but no applicable decision rule. Position is
# otherwise cosmetic: tier E never yields a verdict, so it never reaches _best_tier or
# tier_min/tier_max, both of which see only decidable outcomes.
TIER_ORDER = (TIER_A, TIER_B, TIER_C, TIER_E, TIER_D)
HEADLINE_TIERS = (TIER_A, TIER_B)

TIER_DOC = {
    TIER_A: "superiority analysis, p-value present; met := p <= alpha",
    TIER_B: "non-inferiority/equivalence analysis, p-value present; met := p <= alpha",
    TIER_C: ("no p-value; CI at matching coverage excludes null (0 for differences, 1 for "
             "ratios), superiority or unstated design only. NOT headline"),
    TIER_E: ("no p-value; non-inferiority or equivalence design, so the decision rule is "
             "the margin and not the null. Margin is not a structured AACT field, so NO "
             "verdict is produced. Counted separately, never pooled with tier C"),
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


CONTRAST_RATIO = "ratio"
CONTRAST_DIFFERENCE = "difference"

# The value of no effect, per contrast family, on the UNIT scale. Ratios centred on a
# percentage scale are handled by `ratio_scale` below, not by a second entry here --
# there is no null that makes the exclusion test answer a bioequivalence question.
NULL_FOR_CONTRAST = {CONTRAST_RATIO: 1.0, CONTRAST_DIFFERENCE: 0.0}


def contrast_family(param_type) -> Optional[str]:
    """AACT param_type -> 'ratio' | 'difference' | None. Names only, no numbers.

    This is the primitive: it answers what the FIELD can tell you, which is the shape of
    the comparison. It cannot tell you the scale the sponsor used, and therefore cannot
    on its own decide the null -- see `ratio_scale`.

    None means no contrast is named: a single-arm quantity, an unrecognised type, or a
    ratio the field itself flags as a percentage (where null 100 vs 1 is undeterminable).
    """
    s = str(param_type or "").strip()
    if not s:
        return None
    if _PERCENT_RATIO_RX.search(s):
        return None
    if any(rx.search(s) for rx in _CONTRAST_RATIO_RX):
        return CONTRAST_RATIO
    if any(rx.search(s) for rx in _CONTRAST_DIFF_RX):
        return CONTRAST_DIFFERENCE
    if any(rx.search(s) for rx in _SINGLE_ARM_RX):
        # Deliberately explicit though the fallthrough agrees: lesson 3's ordering lives
        # here, and a reader needs to see that single-arm refusal is a decision taken
        # AFTER the contrast checks, not a default.
        return None
    return None


def null_value_for(param_type) -> Optional[float]:
    """Unit-scale value of no effect for an AACT param_type: 1 for ratios, 0 for
    differences, None when no contrast is named.

    Kept as the NAME-ONLY answer, and expressed through `contrast_family` so the two
    cannot drift apart. Callers labelling a real analysis row must use
    `resolve_null_value`, which also reads the interval's scale.
    """
    family = contrast_family(param_type)
    return NULL_FOR_CONTRAST.get(family) if family is not None else None


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


# ---- ratio SCALE: the null a ratio sits on is not in the param_type --------
# MEASURED, not argued. Analysis rows carrying BOTH a decidable p-value and a full
# interval form a labelled validation set for the tier-C rule, because the p-value is the
# sponsor's own verdict on the same comparison. Against the 8,061 such ratio rows in the
# 2026-09-19 dump (reproduce with scripts/validate_interval_rule.py):
#
#   unit-scaled ratios     n=7518   raw agreement 92.5%   kappa 0.849
#   percent-scaled ratios  n= 543   raw agreement 66.1%   kappa 0.000
#
# The percent-scaled 2x2 is [0,0]=0 [0,1]=184 [1,0]=0 [1,1]=359. The interval rule
# returns "met" on 543 of 543 rows: it is a CONSTANT, not a test, which is why kappa is
# exactly zero, and its disagreement with the sponsor is 100% one-directional --
# disqualifying on the same criterion Step 1b uses. Re-centring on 100 does not rescue it
# (50.5% raw agreement, kappa 0.000): bioequivalence inverts the hypothesis, testing
# whether the interval lies INSIDE 80-125% to demonstrate sameness, so no choice of null
# makes an exclusion test answer the question being asked.
#
# Therefore the scale is read from the INTERVAL and a percent-scaled ratio is REFUSED.
# Naming reaches only 130 of the 534 affected trials -- 1,868 rows say nothing more than
# 'Geometric mean ratio' -- so a regex on the param_type cannot do this job.
RATIO_SCALE_UNIT = "unit"
RATIO_SCALE_PERCENT = "percent"

# A ratio interval lying ENTIRELY at or above this floor is on a percentage scale: a
# ratio of no effect is 1, so a lower bound of 10 or more cannot be a unit-scale interval
# near the null. Exposed as a CLI flag because it produces verdicts. On the validation
# set it partitions cleanly (kappa 0.849 above it, 0.000 below); move it and watch kappa
# respond rather than trusting the default.
DEFAULT_RATIO_SCALE_FLOOR = 10.0

REFUSAL_PERCENT_SCALED_RATIO = ("ratio interval is percent-scaled; no null makes the "
                                "exclusion test meaningful (likely bioequivalence)")

# Why a trial can have NO endpoint estimate. Empty means "it has one". These are for the
# USER-facing one-line explanation, and they distinguish not-applicable from unknown:
# a bioequivalence study's endpoint verdict is not missing, it answers a different
# question. Per the standing discipline, "unknown" and "not applicable" never merge.
NA_REASON_NONE = ""
NA_REASON_PERCENT_SCALED = "percent_scaled_ratio_only"
NA_REASON_COVERAGE_MISMATCH = "ci_coverage_mismatch_only"
NA_REASON_NI_DESIGN = "ni_design_margin_unavailable"

NA_REASON_DOC = {
    NA_REASON_PERCENT_SCALED: (
        "every posted primary analysis was a percent-scaled ratio interval. Almost "
        "always a bioequivalence study, which tests formulation sameness rather than "
        "efficacy -- a different question, not a missing answer."
    ),
    NA_REASON_COVERAGE_MISMATCH: (
        "the posted intervals do not have the coverage needed to decide the endpoint at "
        "this alpha, and their coverage does not imply the verdict either way."
    ),
    NA_REASON_NI_DESIGN: (
        "this trial tested non-inferiority or equivalence, so success is defined against "
        "a margin rather than against no effect. The margin is not recorded in a "
        "structured field, so no endpoint verdict is produced."
    ),
}


def ratio_scale(ci_lower, ci_upper,
                floor: float = DEFAULT_RATIO_SCALE_FLOOR) -> Optional[str]:
    """Interval bounds -> 'unit' | 'percent' | None(no interval to read).

    Reads the NUMBERS, because the param_type does not carry the scale. None when either
    bound is absent or unparseable, which is not a refusal -- an analysis with no usable
    interval already falls to tier D on that ground alone.
    """
    lo, hi = _as_float(ci_lower), _as_float(ci_upper)
    if lo is None or hi is None:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return RATIO_SCALE_PERCENT if lo >= floor else RATIO_SCALE_UNIT


def is_percent_scaled_ratio(param_type, ci_lower, ci_upper,
                            floor: float = DEFAULT_RATIO_SCALE_FLOOR) -> bool:
    """True when this row is a ratio on a percentage scale, i.e. a refused row.

    Separate from `resolve_null_value` so the refusals can be COUNTED without parsing a
    reason string. 437,545 trials already sit in tier D; without a count these rows would
    disappear into it with no trace, which is the failure lesson 17 exists to prevent.
    """
    return (contrast_family(param_type) == CONTRAST_RATIO
            and ratio_scale(ci_lower, ci_upper, floor) == RATIO_SCALE_PERCENT)


def resolve_null_value(param_type, ci_lower, ci_upper,
                       floor: float = DEFAULT_RATIO_SCALE_FLOOR
                       ) -> tuple[Optional[float], str]:
    """(null, reason) for one real analysis row. The row-level counterpart of
    `null_value_for`, which sees only the name.

    Returns (None, reason) to REFUSE rather than guessing a null, and the reason travels
    so the tier-D verdict says which refusal it was.
    """
    family = contrast_family(param_type)
    if family is None:
        return None, "param_type names no usable contrast"
    if family == CONTRAST_DIFFERENCE:
        return 0.0, "difference; null=0"
    scale = ratio_scale(ci_lower, ci_upper, floor)
    if scale == RATIO_SCALE_PERCENT:
        return None, REFUSAL_PERCENT_SCALED_RATIO
    return 1.0, "unit-scaled ratio; null=1"


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


# ---- CI COVERAGE: an interval test is only the alpha test at matching coverage -------
# MEASURED, not assumed. `analysis_ci_percent` is in the dump and the engine never read
# it. Splitting the validation set of section 3.1 by coverage, with alpha = 0.05:
#
#   coverage        n        kappa   over-calls met   under-calls
#   matched (95%)   22,938   0.896   239              954
#   tighter (>95%)     555   0.616     2              107
#   looser  (<95%)   1,624   0.747   151               45
#
# The ASYMMETRY FLIPS with the band, which is what makes this a finding and not noise: a
# 90% interval excluding the null is two-sided p < 0.10, so it over-calls; a 97.5%
# interval is a higher bar, so it under-calls and almost never over-calls (2 in 555).
#
# The required coverage is 100*(1-alpha) for a two-sided test, and that relation was
# checked against the data rather than taken from convention. kappa over an
# alpha x coverage grid, superiority/unstated rows only:
#
#            ci=99    ci=95    ci=90    ci=80
#   a=0.05   0.647    0.944    0.856    0.730      <- peaks exactly at 95
#   a=0.10   0.580    0.849    0.876    0.933
#   a=0.20   0.511    0.714    0.702    0.934
#
# At alpha 0.05, where there is enough data to tell, agreement peaks sharply at 95 and
# degrades in both directions. The other rows are thin (99% coverage is 51 rows in total)
# and are reported rather than relied on.
#
# ONE-WAY IMPLICATION, which is better than refusing the row outright:
#   matched  -> this IS the alpha test; both verdicts stand
#   tighter  -> excluding the null at higher confidence implies a 95% interval would too,
#               so "met" is sound; "not met" is NOT (a 95% interval might have excluded it)
#   looser   -> failing to exclude at lower confidence implies failing at 95%, so
#               "not met" is sound; "met" is NOT
COVERAGE_MATCHED = "matched"
COVERAGE_TIGHTER = "tighter"
COVERAGE_LOOSER = "looser"
COVERAGE_UNKNOWN = "unknown"

# Tolerance in COVERAGE POINTS for calling a posted coverage equal to the required one.
# A flag because it produces verdicts. 0.5 admits '95.0' and rejects '95.8' and '94.0';
# the registry carries 137 distinct ci_percent values, so a whitelist is not viable and
# a numeric tolerance is.
DEFAULT_COVERAGE_TOLERANCE_POINTS = 0.5

# A ci_percent at or below this is a PROPORTION, not a percentage -- AACT carries 30 rows
# reading '0.95'. The same scale error as the ratio bug, one level up, so it is refused
# rather than multiplied by 100 on an assumption about what the sponsor meant.
CI_PERCENT_PROPORTION_MAX = 1.0

REFUSAL_COVERAGE_MISMATCH = ("interval coverage does not support this verdict at the "
                             "configured alpha")
REFUSAL_NI_MARGIN_UNAVAILABLE = ("non-inferiority or equivalence design: the decision "
                                 "rule is the margin, not the null, and the margin is "
                                 "not a structured AACT field")

# The refusal kinds, for counting. Order is the PRECEDENCE used when a trial has no
# decidable analysis and more than one kind of refusal: the NI reason comes first because
# it is a statement about what the STUDY ASKED, which is more informative to a reader than
# a statement about how the result was posted.
REFUSAL_PERCENT_SCALED = "percent_scaled_ratio"
REFUSAL_COVERAGE = "coverage_mismatch"
REFUSAL_NI_DESIGN = "ni_design"
REFUSAL_KINDS = (REFUSAL_NI_DESIGN, REFUSAL_PERCENT_SCALED, REFUSAL_COVERAGE)


def required_ci_percent(alpha: float = DEFAULT_ALPHA) -> float:
    """Coverage a two-sided interval needs to be equivalent to a test at `alpha`."""
    return 100.0 * (1.0 - alpha)


def normalize_ci_percent(raw) -> Optional[float]:
    """AACT ci_percent -> coverage in percentage points, or None when unusable.

    None for absent, unparseable, and for values at or below
    CI_PERCENT_PROPORTION_MAX. A blank is benign rather than suspicious: all 3,457 blank
    ci_percent rows in the dump have no readable interval either, so they were already
    going to tier D on that ground.
    """
    value = _as_float(raw)
    if value is None or value <= CI_PERCENT_PROPORTION_MAX:
        return None
    return value


def coverage_band(raw, alpha: float = DEFAULT_ALPHA,
                  tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS) -> str:
    """ci_percent -> 'matched' | 'tighter' | 'looser' | 'unknown', relative to alpha."""
    coverage = normalize_ci_percent(raw)
    if coverage is None:
        return COVERAGE_UNKNOWN
    required = required_ci_percent(alpha)
    if abs(coverage - required) <= tolerance:
        return COVERAGE_MATCHED
    return COVERAGE_TIGHTER if coverage > required else COVERAGE_LOOSER


def coverage_supports(band: str, met: Optional[int]) -> bool:
    """Does an interval at this coverage justify THIS verdict? The one-way implication.

    Unknown coverage supports nothing: without knowing the coverage there is no implication
    to lean on in either direction, and guessing 95% would be the same class of assumption
    that produced the ratio-scale bug.
    """
    if met is None:
        return False
    if band == COVERAGE_MATCHED:
        return True
    if band == COVERAGE_TIGHTER:
        return met == 1
    if band == COVERAGE_LOOSER:
        return met == 0
    return False


# ---- non-inferiority and equivalence are DIFFERENT TESTS ------------------------------
# MEASURED. Splitting matched-coverage tier-C rows by design:
#
#   design            n        kappa   under-calls   over-calls   ratio
#   superiority       18,386   0.942   356           180          2.0
#   NI / equivalence   2,049   0.434   563            45         12.5
#   unstated           2,503   0.960    35            14          2.5
#
# The interval rule asks the superiority question. An NI trial succeeds when the interval
# lies inside the margin, which routinely INCLUDES the null, so the rule under-calls it
# 12.5 to 1 -- the direction theory predicts. Stripping NI rows lifts superiority to 0.942
# and unstated to 0.960, well above the 0.896 pooled figure, so the refusal buys accuracy
# on what remains as well as correctness on what leaves.
#
# The margin is not recoverable today, and that was checked rather than assumed: of 11,513
# NI/equivalence primary analysis rows, 11,486 carry a description and 10,177 contain SOME
# number -- but only 2,031 (17.6%) contain the word "margin" at all and 1,877 (16.3%) have
# a number adjacent to it. The other numbers are alpha levels, power and sample sizes. So
# a text parse could reach roughly a sixth of these rows, and would itself need validating.
# Tier E exists so that sixth stays findable instead of being dissolved into tier D.
DESIGN_SUPERIORITY = "superiority"
DESIGN_NI = "ni"
DESIGN_UNSTATED = "unstated"

# Finer reading of the same field, CARRIED not decided. Equivalence is a third test --
# two-sided containment within +/- margin, against non-inferiority's one-sided bound --
# and the two differ sharply in how often a margin is even stated (26.4% for
# NON_INFERIORITY, 6.7% for EQUIVALENCE), so collapsing them would hide that. Nothing in
# the label branches on this yet; it exists so the decision can be made on evidence.
DESIGN_DETAIL_SUPERIORITY = "superiority"
DESIGN_DETAIL_NON_INFERIORITY = "non_inferiority"
DESIGN_DETAIL_EQUIVALENCE = "equivalence"
DESIGN_DETAIL_NI_OR_EQUIVALENCE = "ni_or_equivalence_unspecified"
DESIGN_DETAIL_UNSTATED = "unstated"


def analysis_design_detail(non_inferiority_type) -> str:
    """AACT non_inferiority_type -> the FINER design reading.

    Separate from `analysis_design` rather than replacing it: tier B's definition rests on
    the coarse 'ni' bucket, and changing that function's return values would move tier B
    counts as a side effect of adding a carried signal.

    The observed AACT vocabulary is nine values. 'NON_INFERIORITY_OR_EQUIVALENCE' names
    two different tests and cannot be resolved to one, so it gets its own value rather
    than being assigned to whichever is more common.
    """
    text = str(non_inferiority_type or "").strip().lower()
    if not text:
        return DESIGN_DETAIL_UNSTATED
    text = re.sub(r"[^a-z]+", " ", text)
    ni = "non inferiority" in text or "noninferiority" in text
    equivalence = "equivalence" in text
    if ni and equivalence:
        return DESIGN_DETAIL_NI_OR_EQUIVALENCE
    if ni:
        return DESIGN_DETAIL_NON_INFERIORITY
    if equivalence:
        return DESIGN_DETAIL_EQUIVALENCE
    if "superiority" in text:
        return DESIGN_DETAIL_SUPERIORITY
    return DESIGN_DETAIL_UNSTATED


def refusal_kind(row: dict, alpha: float = DEFAULT_ALPHA,
                 ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                 coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS
                 ) -> Optional[str]:
    """Which refusal, if any, withheld an interval verdict from this row. None otherwise.

    A separate function from `classify_analysis` so refusals can be COUNTED without
    parsing reason strings, and so the two cannot disagree: both delegate to the same
    helpers rather than re-implementing the decision.

    Returns None for rows a refusal never reached -- a row decided on its p-value, or one
    with no interval at all, which is tier D on its own ground.
    """
    operator, value = parse_p_value(row.get("p_value"), row.get("p_value_modifier"))
    if met_from_p(operator, value, alpha)[0] is not None:
        return None                                   # p-value decided it; no interval used
    if analysis_design(row.get("non_inferiority_type")) == DESIGN_NI:
        return REFUSAL_NI_DESIGN
    if is_percent_scaled_ratio(row.get("param_type"), row.get("ci_lower_limit"),
                               row.get("ci_upper_limit"), ratio_scale_floor):
        return REFUSAL_PERCENT_SCALED
    null, _ = resolve_null_value(row.get("param_type"), row.get("ci_lower_limit"),
                                 row.get("ci_upper_limit"), ratio_scale_floor)
    met, _ = met_from_ci(row.get("ci_lower_limit"), row.get("ci_upper_limit"), null)
    if met is None:
        return None                                   # nothing to refuse
    band = coverage_band(row.get("ci_percent"), alpha, coverage_tolerance)
    return None if coverage_supports(band, met) else REFUSAL_COVERAGE


def classify_analysis(row: dict, alpha: float = DEFAULT_ALPHA,
                      ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                      coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS
                      ) -> tuple[str, Optional[int], str]:
    """One outcome_analyses row -> (tier, met, reason).

    Precedence is p-value before interval, and design type decides A vs B. An unstated
    design with a p-value is treated as superiority and the reason records that
    assumption, so it can be counted later rather than disappearing.

    The interval branch goes through `resolve_null_value`, so a percent-scaled ratio is
    refused to tier D with the refusal named, rather than tested against a null it is not
    centred on.
    """
    design = analysis_design(row.get("non_inferiority_type"))
    op, val = parse_p_value(row.get("p_value"), row.get("p_value_modifier"))
    met, reason = met_from_p(op, val, alpha)
    if met is not None:
        tier = TIER_B if design == DESIGN_NI else TIER_A
        if design == DESIGN_UNSTATED:
            reason += "; design unstated, assumed superiority"
        return tier, met, reason
    # NI and equivalence designs leave here BEFORE the interval is consulted: the interval
    # answers the superiority question, which is not the question these trials asked.
    if design == DESIGN_NI:
        return TIER_E, None, f"{reason}; {REFUSAL_NI_MARGIN_UNAVAILABLE}"
    null, null_reason = resolve_null_value(row.get("param_type"),
                                           row.get("ci_lower_limit"),
                                           row.get("ci_upper_limit"),
                                           ratio_scale_floor)
    met_ci, reason_ci = met_from_ci(row.get("ci_lower_limit"), row.get("ci_upper_limit"), null)
    if met_ci is not None:
        band = coverage_band(row.get("ci_percent"), alpha, coverage_tolerance)
        if coverage_supports(band, met_ci):
            return TIER_C, met_ci, f"{reason_ci}; coverage {band} at alpha {alpha}"
        return TIER_D, None, (f"{reason}; {REFUSAL_COVERAGE_MISMATCH} "
                              f"(coverage {band}, verdict would have been {met_ci})")
    # Name the null refusal when that is what blocked the row; met_from_ci's own reason
    # would otherwise report "null value unavailable", which hides WHY it was withheld.
    return TIER_D, None, (f"{reason}; {null_reason}" if null is None
                          else f"{reason}; {reason_ci}")


# ---- outcome- and trial-level aggregation ---------------------------------
# Refusal kind -> the user-facing reason. Separate mappings because a refusal is a fact
# about one analysis row while an NA reason is a claim about the whole trial.
NA_REASON_FOR_REFUSAL = {
    REFUSAL_NI_DESIGN: NA_REASON_NI_DESIGN,
    REFUSAL_PERCENT_SCALED: NA_REASON_PERCENT_SCALED,
    REFUSAL_COVERAGE: NA_REASON_COVERAGE_MISMATCH,
}


def _na_reason_for(refusals: dict) -> str:
    """Refusal counts -> one NA reason, by REFUSAL_KINDS precedence.

    Iterates REFUSAL_KINDS rather than the dict, so the precedence is the declared tuple
    and not whatever order the counts happened to be built in.
    """
    for kind in REFUSAL_KINDS:
        if refusals.get(kind):
            return NA_REASON_FOR_REFUSAL[kind]
    return NA_REASON_NONE


def _best_tier(tiers: Iterable[str]) -> Optional[str]:
    # materialise first: `tiers` may be a generator, and re-consuming it inside the
    # comprehension would silently yield an empty set after the first iteration
    present_set = set(tiers)
    present = [t for t in TIER_ORDER if t in present_set]
    return present[0] if present else None


def aggregate_outcome(analyses: list[dict], alpha: float = DEFAULT_ALPHA,
                      ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                      coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS) -> dict:
    """All analysis rows for ONE primary outcome -> one verdict for that outcome.

    A single outcome often carries several analyses (timepoints, subgroups, alternative
    comparators). The rule: take the strongest tier present, then within that tier count
    the outcome as met if ANY analysis met. This is optimistic within an outcome, which is
    why n_analyses is carried through -- an outcome resting on 1 of 9 analyses is visible
    downstream rather than indistinguishable from a clean single result.
    """
    graded = [classify_analysis(a, alpha, ratio_scale_floor, coverage_tolerance)
              for a in analyses]
    # Counted through `refusal_kind` rather than by inspecting reason strings, so the
    # count and the tier assignment delegate to the same helpers and cannot disagree.
    refusals = {kind: 0 for kind in REFUSAL_KINDS}
    for a in analyses:
        kind = refusal_kind(a, alpha, ratio_scale_floor, coverage_tolerance)
        if kind is not None:
            refusals[kind] += 1
    decidable = [(t, m, r) for t, m, r in graded if m is not None]
    base = {"n_analyses": len(analyses), "refusals": refusals,
            # retained for callers written against the earlier single-refusal shape
            "n_scale_refused": refusals[REFUSAL_PERCENT_SCALED]}
    if not decidable:
        return {"tier": _best_tier(t for t, _, _ in graded) or TIER_D, "met": None,
                "n_decidable": 0,
                "reason": graded[0][2] if graded else "no analysis rows", **base}
    tier = _best_tier(t for t, _, _ in decidable)
    in_tier = [(m, r) for t, m, r in decidable if t == tier]
    met = 1 if any(m == 1 for m, _ in in_tier) else 0
    reason = next(r for m, r in in_tier if m == met)
    return {"tier": tier, "met": met, "n_decidable": len(decidable),
            "reason": reason, **base}


def aggregate_trial(outcomes: dict[str, list[dict]], alpha: float = DEFAULT_ALPHA,
                    ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
                    coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS) -> dict:
    """{outcome_id: [analysis rows]} for one trial -> the multi-endpoint label family.

    All four representations are carried, per the decision to keep every reading rather
    than collapse early: how many primary outcomes were decidable, how many were met, the
    proportion, whether at least one was met, and whether all were met.

    tier_min is the WEAKEST tier any counted outcome relied on, and it is the correct
    filter for headline analyses: a trial whose all_primary_met depends on a tier-C
    outcome is a tier-C trial no matter how clean its other outcomes were.
    """
    verdicts = {oid: aggregate_outcome(rows, alpha, ratio_scale_floor, coverage_tolerance)
                for oid, rows in outcomes.items()}
    counted = {oid: v for oid, v in verdicts.items() if v["met"] is not None}
    n_primary = len(verdicts)
    n_analyzed = len(counted)
    n_met = sum(v["met"] for v in counted.values())
    refusals = {kind: sum(v["refusals"][kind] for v in verdicts.values())
                for kind in REFUSAL_KINDS}
    n_scale_refused = refusals[REFUSAL_PERCENT_SCALED]
    tiers = [v["tier"] for v in counted.values()]
    ranked = [t for t in TIER_ORDER if t in set(tiers)]
    # When NOTHING was counted, tier_min/tier_max fall back to the weakest tier any
    # outcome reached, not to TIER_D by default. Otherwise a trial whose every primary
    # analysis was a non-inferiority interval reports as D_no_analysis -- indistinguishable
    # from a single-arm descriptive posting -- and tier E, which exists precisely so those
    # studies stay visible, would read 0 in every tier distribution while the refusal
    # counts said 1,820 trials. That contradiction was in the first widened pull's output.
    #
    # This only affects trials with NO verdict. A trial with an A outcome and an E outcome
    # keeps tier_min = A, because tier_min describes what the LABEL rests on and the label
    # rests only on the counted outcomes.
    if not ranked:
        uncounted = {v["tier"] for v in verdicts.values()}
        ranked = [t for t in TIER_ORDER if t in uncounted]
    mix = "|".join(f"{t.split('_')[0]}:{tiers.count(t)}" for t in ranked)
    out = {
        "n_primary_outcomes": n_primary,
        "n_primary_analyzed": n_analyzed,
        "n_primary_met": n_met,
        "frac_primary_met": (n_met / n_analyzed) if n_analyzed else None,
        "any_primary_met": (1 if n_met > 0 else 0) if n_analyzed else None,
        "all_primary_met": (1 if n_met == n_analyzed else 0) if n_analyzed else None,
        "n_analyses_total": sum(v["n_analyses"] for v in verdicts.values()),
        "n_analyses_scale_refused": n_scale_refused,
        "n_analyses_coverage_refused": refusals[REFUSAL_COVERAGE],
        "n_analyses_ni_design": refusals[REFUSAL_NI_DESIGN],
        # A trial with no verdict has a STATEABLE reason whenever a refusal took one away,
        # which is different from having had nothing to begin with. Precedence is
        # REFUSAL_KINDS order: the NI reason first, because it describes what the study
        # asked rather than how the result was posted.
        "endpoint_na_reason": (_na_reason_for(refusals) if n_analyzed == 0
                               else NA_REASON_NONE),
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
              broad_includes_safety: bool = False,
              ratio_scale_floor: float = DEFAULT_RATIO_SCALE_FLOOR,
              coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE_POINTS) -> dict:
    """One trial's studies row + its primary-outcome analyses -> one label record.

    `strict` uses any_primary_met, which is the most permissive of the four multi-endpoint
    readings; the others travel alongside so the modelling choice stays open. Nothing here
    filters by tier -- filtering is the caller's decision and has to be stated in the
    result, not buried in the label build.
    """
    agg = aggregate_trial(outcomes, alpha, ratio_scale_floor, coverage_tolerance)
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
        # Carried per row for the same reason alpha is: a threshold that produced a
        # verdict must travel with the verdict, or an offline re-derive can silently use
        # a different one and disagree with the live pull.
        "ratio_scale_floor_used": ratio_scale_floor,
        "coverage_tolerance_used": coverage_tolerance,
        "required_ci_percent_used": required_ci_percent(alpha),
    }
    rec.update(agg)
    return rec