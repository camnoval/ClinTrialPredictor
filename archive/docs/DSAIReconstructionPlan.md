# DSAI reconstruction — project brief and build plan

This is the north-star document for the next phase. It supersedes the "fingerprint spine"
framing. Hand it to a fresh session to continue without drift.

## What changed, and why

We compared our pipeline to the actual winning solution
(github.com/bjoernholzhauer/DSAI-Competition-2019, Team Insight-Out). Our reconstruction
shared only the *skeleton* (per-trial XGBoost -> ridge roll-up to drug-indication, temporal
holdout, log-loss). It diverged on the two things that matter:

- **Data**: they used proprietary Informa databases (Pharmaprojects + Trialtrove). We used
  TOP + ChEMBL + MONDO. Their data is curated public facts, so it is reconstructable on
  open sources — that is this project's premise.
- **Features**: their signal is *generalizable pipeline priors* (mechanism-of-action class
  history, trial design, sponsor, termination reason, accrual, orphan status, therapeutic
  area). **There are no molecular fingerprints anywhere in their solution.** Our model was
  ~90% Morgan fingerprints — molecule *recognition*, which cannot transfer to novel drugs.

The forward test proved the consequence: on CTO 2020-2024 our fingerprint model scored
AUROC ~0.52 (chance), log-loss worse than base rate, ECE ~0.15. That is evidence our
fingerprint approximation does not generalize — NOT that DSAI's method fails, because
DSAI's method is not fingerprint-based. The whole 2020-24 cohort is dominated by novel/coded
chemistry (only ~50% even has a public structure), which is exactly where recognition dies.

**North star: rebuild DSAI's actual method — pipeline-prior features on open data — and
re-run the forward test on that.** Fingerprints stay only to the extent they earn their
place beside the real features.

## DSAI's actual protocol (from their repo)

- **Target**: drug-indication approval (PoS) predicted from Phase-2 data. == our
  ChEMBL-derived label (label (i)). CTO gold (label (ii)) is demoted to a secondary check;
  it is a different target (trial success, weak-supervision-derived; R9).
- **Model**: ensemble of (a) two XGBoost trial-level models, each rolled up to
  drug-indication by *relaxed ridge* (glmnet), + (b) one Bayesian logistic regression
  (rstanarm) on project-level features with *covariate-balancing propensity (CBPS)* weights,
  + post-processing. Past-vs-future CV throughout. Private LB log-loss 0.202 / F1 0.739.
- **Trial-level features** (`feat_bjoern_trial.R`, read in full):
  - **MoA-class historical priors** (`drugclass2/3/4`) — CROWN JEWEL. For a drug's mechanism
    of action, the historical approval rate of *other* drugs sharing that MoA, at MoA,
    MoA x disease-type, MoA x therapeutic-area granularity; Beta(1/3, 2.7)-smoothed to logit
    intervals; averaged over the drug's MoAs. Time-respecting (`phaseendyear.x>=phaseendyear.y`)
    and own-outcome-masked (leave-one-out).
  - Trial **design** flags (randomized/blinded/controlled/efficacy-assessed/PK/innovative +
    a designscore), **termination-reason** score (0 good -> 4 bad, + safety flag), **accrual**
    ratio (actual/target) and **relative trial size** within disease/TA, **sponsor-type**
    dummies, **therapeutic-area / disease-type** dummies, **drug-type** flags (monoclonal via
    -mab stem, gene therapy, vaccine, combination, generic, insulin/flu), **orphan**
    designation, **prior_approval**, **time-since-first-outcome**.
  - **Censoring correction**: per-year `logitoffset`s (phase-end 2018+ gets +0.89 non-onc /
    +1.38 onc added to the prediction logit) and `casewgt`s from `glm(outcome ~ years_since_1999)`
    that upweight recent positives — because recent trials are right-censored (not approved
    *yet* != failed).
- Still to read (blocked on GitHub nested-link fetch; pull via per-file search when needed):
  `new_cv_splits.R` (exact past-vs-future CV), `xgb_cv_new_cv_split_plus_ridge.R` (XGB
  hyperparameters + ridge), `features_bjoern.R` (project-level features). Needed for the
  model/eval stage, NOT for the MoA feature.

## Open-data source map

| Feature family | Open source |
| --- | --- |
| MoA / target, approval label | ChEMBL (`drug_mechanism`/`mechanism_refs`, `target_dictionary`; `max_phase_for_ind`) |
| design, sponsor class, accrual, termination, dates, enrollment | **AACT** (free cloud Postgres `aact-db.ctti-clinicaltrials.org:5432`, db `aact`, free account; `psycopg2`) |
| therapeutic area / disease type | condition -> MONDO crosswalk (`mondo_xref.csv`) |
| orphan designation | FDA Orphan Drug Designations (public download) |
| trial dates (phaseendyear) for TOP | AACT `studies.completion_date` joined by nctid (TOP itself has no dates, R6) |
| trial dates for CTO | already in `cto_trials.csv` (CT.gov v2) |

## Key decisions (locked)

- **MoA class key = ChEMBL `target_chembl_id`** (cleaner than free-text), with
  `mechanism_of_action` string as fallback when no target. A molecule may map to several.
- **Censoring**: build BOTH — (B) settled-outcomes holdout (phase-2 end old enough that
  non-approval is a true failure) as the *scientific* headline, and (A) DSAI-style
  logit-offset + case-weight correction, re-estimated on open data, as the *recent-window
  product* estimate with its assumption stated. Lead with B.
- **Leakage discipline (R7)**: every historical prior is built strictly past->future
  (fit-set outcomes only) and masks the index drug-indication's own outcome. Every prior
  feature ships with a leakage check (recompute the wrong way; AUROC must inflate).
- **Coverage is measured before use** (as with molecule coverage): a feature's open-data
  coverage caps its value; report it every time.

## Build sequence

1. **MoA-class-prior audit** (`audit_moa_prior.py`) — coverage + own-signal AUROC on TOP's
   settled holdout + leakage check. Decides whether MoA carries signal on open data before
   we invest. START HERE (runnable now: ChEMBL client + units.csv only).
2. **AACT feature batch** — design, sponsor, accrual, termination-reason, TA/disease,
   drug-type, dates; + FDA orphan. (Needs AACT account.)
3. **Censoring** — implement A and B, re-estimate offsets/weights on open data.
4. **Reassemble** the trial -> ridge spine on the new features (fingerprints only if they
   help beside them); then add the Bayesian-logistic + CBPS leg for the full 3-model ensemble.
5. **Forward test** on the faithful reconstruction: settled-outcomes headline (AUROC/log-loss
   vs base rate and any published baseline) + recent-window product estimate + calibration
   (Brier/ECE/reliability) + seen/novel and small-mol/biologic strata.
6. **Docs reset** — retire the fingerprint-spine framing across Handoff/Architecture/Risks.

## Risks specific to this phase

- **R7 (leakage) is now the dominant risk**: the MoA/history priors trivially leak future
  and own outcomes if the temporal LOO is not exact. Non-negotiable leakage checks.
- **Right-censoring on recent data** makes the ChEMBL "not approved" label ambiguous for
  2020-24; handled by censoring option A/B above. This is *the* threat to the forward test.
- **MoA coverage cap**: coded/biologic compounds often lack a ChEMBL mechanism; MoA-prior
  coverage will be partial, just as fingerprint coverage was ~50%. Measure it first.
- **Proprietary->open gaps**: a few Trialtrove-specific fields may not reconstruct exactly
  (e.g. their curated termination reasons); note divergences rather than paper over them.

## What "done" looks like

A reproducible open-data pipeline whose feature set matches DSAI's families (MoA priors +
design + sponsor + termination + accrual + TA + drug-type + orphan + censoring correction),
a trial->ridge (+ Bayesian/CBPS) ensemble, and a forward test reporting an honest
settled-outcomes generalization number against base rate, with the recent-window estimate
and calibration clearly caveated — plus docs that describe *this*, not the fingerprint spine.

---

# EXACT PROTOCOL (from paper + supplement S1–S3, mmc1/mmc2) — authoritative

## Task, label, split, base rate
- Target: drug–indication regulatory approval (PoS) from Phase-2 data. Success = registration/
  launch in ≥1 country; failure = suspension/termination/lack of development.
- Failure date defined as **1 year after the last Ph2/Ph3 end date**; approval date = earliest
  approval in any market.
- Chronological split: resolved **before 2016 = train, 2016+ = test**. Base rate **11.5%**
  approvals (796/6901); test aggregate 9.3%, but **only 1.8% for trials completed after 2018**.
- Metric: binary log-loss (primary); AUC secondary. Baseline MIT 0.78 AUC; winners 0.88 / 0.84.

## Model (top team "Insight_Out") — now fully specified
- Ensemble = **0.25·XGB1 + 0.25·XGB2 + 0.5·BLR**, on the probability scale, then post-processing.
- **XGB1** (manual tune): 810 trees, max_depth 8, eta 0.05, gamma 0, min_child_weight 4,
  subsample 1.0, colsample_bytree 0.1, binary:logistic.
- **XGB2** (differential-evolution tune, DEoptim): 3551 trees, max_depth 6, eta 0.05,
  gamma 0.024, min_child_weight 2, subsample 0.95, colsample_bytree 0.736.
- Both: final eta 0.05; #trees from CV-log-loss, then +10% for whole-data refit.
- **Roll-up** (each XGB → drug–indication): ridge (glmnet) on 3 inputs = **mean, min, max of
  trial predictions on the LOGIT scale**; ridge penalty by **one-SE rule, 10-fold CV** on the
  out-of-fold predictions; that 10-fold CV groups all records of a drug–indication together.
- **BLR**: hierarchical Bayesian logistic (rstanarm/Stan MCMC), drug–indication level, ~46
  features, **CBPS weights** toward test-set-like cases; granular TA as random effect; features
  = novelty (non-generic, not insulin/flu-vaccine), rel. Ph2 accrual vs disease, MoA success
  rates, INN, MTOS + interactions; somewhat-informative priors (Table in Fig S3).
- Trees handle missing values natively; **mean imputation** where needed (no gain from model-based).
- **No trial weighting** used (1/m and 1/√m tried, no consistent gain).

## Cross-validation — 26 past-vs-future folds
- 26 folds mixing: overlapping past-vs-future cuts (<2012 vs ≥2012, <2013 vs ≥2013, …);
  splitting the future into fifths; and "same-drug" vs "unseen-drug" variants.
- Invariant: all trials of a drug–indication stay within one side of any split (no leakage).
- Training-window floor: **XGBoost drops pre-2008 data; BLR drops pre-2010** (pre-2007
  registry gaps make old "failures" unreliable).

## Feature engineering (263 features) — see DSAI_feature_spec.{md,csv}
- MoA target encoding (crown jewel): counts + Beta(1/3,2.7)-smoothed logit CIs of prior
  approvals for a drug's MoA(s), cut at ≤ phase-2-end year, averaged over the drug's MoAs, at
  MoA / disease-type / TA granularity. **Validated on open data (AUROC 0.674).**
- Top importances (Table S4): rel_ph2_size_ta/dis, meanclu50/meancll50 (+dt/ta), class_approvals,
  class_counts — i.e., relative program size + MoA target encodings + trial-outcome score dominate.
- Second team (E2C) confirms: roll-up by **max** trial prediction (simpler alternative), and
  `drug_prior_trial_positive.pct` (drug's own historical trial-success rate, year-cut) their
  single most correlated engineered feature.

## Post-processing — a COMPETITION ARTIFACT, not predictive power
- "The part with the largest impact on the leaderboard score." Both teams independently:
  set EoPh2-2019 predictions ≤0.05%; EoPh2-2018 to [0.1%, 10%]; clip to ~[0.001, 0.8].
- The paper's own discussion: these "add little practical value for real-life application."
  E2C's gains (0.275→0.236→0.222) came largely from this + max-aggregation.
- **Our stance:** report the model WITHOUT the censoring hack as the honest number; show the
  hack only as a competition-replication footnote. A genuine 2020–24 forward test lives in this
  un-scoreable zone → settled-outcomes (pre-2016 boundary) is the only honest headline.

## What this locks for our build
- Roll-up already matches (mean/min/max logit + ridge). XGB1/XGB2 configs + 0.25/0.25/0.5
  weights are now copyable. CV = 26 past-vs-future folds, drug–indication-grouped, pre-2008
  floor. Features driven off DSAI_feature_spec.csv. Trial-endpoint OUTCOME is the one GAP.