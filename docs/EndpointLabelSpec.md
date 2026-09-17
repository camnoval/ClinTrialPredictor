# Endpoint-met label specification

The per-trial predictor's target. Authoritative definitions live in
`src/trial_pos/services/endpoint_label.py`; this document is the readable version and
must be updated alongside it. `scripts/pull_aact_results.py` prints the tier legend on
every run, and every output row carries `tier_min`, `tier_max`, `tier_mix` and
`label_rule`, so a tier claim can always be checked against the row that made it.

## Why this label exists

Everything upstream in this project predicts ChEMBL `max_phase_for_ind`, which is
regulatory approval of a drug-indication pair. That is not "this trial met its primary
endpoint". The mismatch, plus the 0.47 base rate that ChEMBL's approved-drug bias
produces, is why the existing artifact is a reference-class prior rather than a trial
predictor. This label comes from the trial's own posted results.

## The four tiers

| Tier | Rule | Headline? |
| --- | --- | --- |
| **A** `A_superiority_p` | superiority analysis with a usable p-value; met := p <= alpha | yes |
| **B** `B_noninferiority_p` | non-inferiority or equivalence analysis with a usable p-value; met := p <= alpha, meaning non-inferiority was demonstrated | yes |
| **C** `C_interval_only` | no p-value; confidence interval excludes the null (0 for differences, 1 for ratios, from `param_type`) | **no** |
| **D** `D_no_analysis` | no decidable analysis: single-arm trials, descriptive-only postings, primary outcomes with no analysis rows. `endpoint_met` is UNKNOWN | n/a |

`tier_min` is the weakest tier any counted outcome relied on, and it is the correct filter
for headline analyses. A trial whose `all_primary_met` depends on a tier-C outcome is a
tier-C trial regardless of how clean its other outcomes were.

Tier D trials are not dropped from the project. They carry no endpoint-met label but they
do carry a phase-advancement label, which is derived from subsequent trial starts and needs
no statistical structure. Coverage differs per target, and that is expected once there are
two targets.

## Rules that are deliberately indeterminate

A bounded p-value only decides the question when the bound falls on the decisive side of
alpha. "p < 0.001" is met at alpha 0.05; "p < 0.5" is not decidable at all and returns
unknown rather than being forced either way. Label noise is more damaging than feature
noise, so the engine abstains.

`alpha` is a build parameter, not a constant. Group-sequential designs and alpha-splitting
across several primary outcomes use other thresholds. The raw p-value and its modifier are
preserved in the raw dump so the choice can be revisited without re-querying.

## Tier C only applies to contrasts

`null_value_for` checks single-arm quantities FIRST and returns no null for them. A 40%
objective response rate with an interval of 25% to 55% excludes zero trivially, so
applying the interval rule there would manufacture positives out of descriptive
statistics. The live gap audit found 48 trials with unrecognised parameter types and
almost all were of this kind: "percentage of participants", "objective response rate",
"Proportion responding", "6-month PFS". They are deliberately left undecided, and the
exclusion list in the engine says so, because the obvious "improvement" is to add them.

Only unambiguous contrasts were added after that audit: the hazard-ratio family
(including "Cox Proportional Hazard" and the bare "HR" variants), "Treatment Contrast",
and reduction-versus-placebo phrasings. "Relative Vaccine Effectiveness" was
deliberately left out, since it is reported both as a ratio and as a percentage and the
field does not record which.

## Known limitation: direction of effect

A p-value below alpha says the pre-specified comparison reached its threshold. It does not
say the experimental arm was the favoured one, and AACT has no structured field for
direction. So "met" means "the comparison the sponsor specified reached significance",
which is close to but not identical with "the drug won". Sponsors rarely post a significant
primary that disfavours their own arm, so the practical error rate is low; it is nonzero and
it is not measurable from AACT alone. Tier C inherits this in a stronger form, since an
interval excluding the null is directionally agnostic by construction.

## Multi-endpoint trials

All four readings are carried and none is chosen at label-build time:
`n_primary_analyzed`, `n_primary_met`, `frac_primary_met`, `any_primary_met`,
`all_primary_met`. `endpoint_met_strict` currently uses `any_primary_met`, the most
permissive reading; changing that is a modelling decision to be made against measured
coverage, not baked into the label.

Within a single outcome carrying several analyses (timepoints, subgroups, alternative
comparators), the rule is: take the strongest tier present, then count the outcome as met
if any analysis in that tier met. This is optimistic within an outcome, which is why
`n_analyses_total` travels alongside — an outcome resting on one of nine analyses stays
visible rather than looking like a clean single result.

## Strict and broad variants

**strict** labels from posted analyses only. Its negative class is "ran to completion,
analysed, posted a null result", which systematically omits the most severe failures: the
trials that stopped early and the trials that never posted. Strict is the headline.

**broad** additionally reads terminated-for-futility as 0 and stopped-early-for-efficacy
as 1. It recovers the omitted failure mode at four costs: the label becomes a mixture of
two different events; classification depends on free text; the base rate shifts downward
unevenly across therapeutic areas; and there is no symmetric source of extra positives.
Broad is the sensitivity analysis. Never pool the two without saying so.

### why_stopped classification

Nine classes: `none`, `efficacy_success`, `external_evidence`, `benefit_risk`,
`futility`, `safety`, `business`, `operational`, `other`. Only `futility` (negative) and
`efficacy_success` (positive) feed the broad label. `safety` is opt-in via
`--broad-includes-safety`, since a safety stop says nothing about whether the efficacy
endpoint would have been met.

Two classes exist specifically to stop ambiguous text from being counted:

- `external_evidence` — "Interim results of another trial showed inferior activity". The
  index trial's own endpoint was never assessed, so this is not its result.
- `benefit_risk` — "the overall benefit to risk profile was not favorable". Mixes
  efficacy and safety and cannot be attributed to either.

Both are recorded and then left out of the broad label, which keeps the broad negative
class interpretable as efficacy failure rather than a grab bag of discontinuations.

Matching uses regexes with a word or two of slack between qualifier and object. The first
implementation used rigid literals and the live gap audit found 105 candidate misses in
1,055 stated reasons, because real text reads "lack of **drug** efficacy" and "**failure**
to meet" rather than the canonical phrasings.

A negation guard vetoes `futility`, `efficacy_success` and `safety` when the text contains
patterns such as "no ... concerns", "not due to", or "unrelated to". Without it, flexible
patterns read "no safety or efficacy concerns" as futility — an inversion the test
fixtures caught. A veto costs a negative; a false positive corrupts the target. The
asymmetry is deliberate.

Classification remains conservative overall: text matching no family becomes `other`,
never `futility`.

## R7: fields that can never be features

`endpoint_label.LABEL_DERIVED_FIELDS` enumerates them, so feature builders can assert
against the constant rather than relying on recollection:

`why_stopped`, `overall_status`, `why_stopped_class`, `termination_score`,
`safety_termination`, `results_first_posted_date`, `results_first_submitted_date`,
`results_posted`.

`termination_score` and `safety_termination` in `pull_aact.py` derive from `why_stopped`
and inherit the contamination. They are excluded from both variants' feature sets, not just
broad, so the two models remain comparable.

## The coverage cliff to expect

Sponsors post outcome measurements far more often than statistical analyses. The gap
between "has at least one primary outcome row" and "has at least one primary analysis row"
is the real constraint on this label, and the puller reports both lines adjacently for that
reason.

## What this label does not settle

`scripts/pull_aact_results.py` reports a posting-bias preview within the pulled cohort
only. That cohort is already selected, being TOP trials matched to ChEMBL, so the preview
is a smell test. The Step-2 audit comparing posters against non-posters across the full
AACT population is a separate script, and no modelling should begin before it runs.