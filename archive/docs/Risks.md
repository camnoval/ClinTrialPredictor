# Risks

This document lists the assumptions that can break the project. Each risk has a tag
(R1, R2, ...). Code comments use these tags. A reader can go from a line of code to
the assumption behind it.

## R1 - Temporal leakage (Severity: Existential)
- Assumption: the split gives no future information to training. No record dated on or
  after the boundary informs training.
- Threat: leakage inflates every metric. It makes the result false.
- Counter-case: the guard in `services/temporal_split.py` is pure and has tests. The
  risk stays low while every split uses `make_split`.
- Mitigation: run `test_temporal_split.py` after you add a source or a date field.
  Check that `leakage_report` shows sensible counts.

## R2 - Cross-dataset comparison (Severity: Quality-Trust)
- Assumption: you compare the result to a public baseline on its own split and metric.
  You do not compare it to the Novartis score of 0.202.
- Threat: a comparison between two different tests is meaningless.
- Counter-case: TOP and CTO both give a baseline and a fixed split.
- Mitigation: state the benchmark, the metric, the phase, and the split with every
  result. Reject a result that cannot state these.

## R3 - TOP license (Severity: Safety/Legal)
- Assumption: the use of the TOP data and code stays non-commercial.
- Threat: commercial use breaks the license.
- Mitigation: keep the data out of git. Record the license in each release. Check the
  terms before any commercial use.

## R4 - Public labels (Severity: Quality-Trust)
- Assumption: the IQVIA labels (TOP) and the derived labels (CTO) are good enough.
- Threat: noisy labels set a ceiling on accuracy. The public ceiling is lower than the
  private-data ceiling.
- Counter-case: CTO reports 91% F1 agreement with expert annotation on its recent set.
- Mitigation: carry the label provenance. Prefer measured labels for the test split.
  Flag a derived label when it drives a decision.

## R7 - Leakage through outcome-derived features (Severity: Existential)
- What happened: the first TOP run scored AUROC 0.86 / 0.82 / 0.74 (phases I/II/III) --
  far above HINT (~0.77/0.61/0.62 PR-AUC) and the published XGBoost baseline (~0.5-0.6
  AUROC). Too good to be true, and it was.
- Cause: two features were derived from fields only known at/after the trial ends.
  `was_terminated` comes from `status`; a terminated status is essentially the failure
  label. `has_why_stop` comes from `why_stop`, populated only when a trial is stopped
  early -- a near-direct readout of the label. Realized `duration_days` is the same class
  of problem (only known at completion; terminated trials run shorter).
- Fix: `train_top.py` uses PRE_TRIAL_FEATURES only by default (phase_ordinal, the drug/
  disease/icd/smiles counts, criteria_len). LEAKY_FEATURES are off unless `--with-leaky`,
  which exists solely to demonstrate the inflation. Confirmed on synthetic data: honest
  0.52 vs leaky 0.86 -- the same jump seen on real TOP.
- Rule: any feature must be knowable at trial start. Audit every new feature against that
  before adding it. Do not report `--with-leaky` numbers anywhere.

## R6 - TOP has no dates in the outcome files (Severity: Quality-Trust)
- Assumption: the split gives no future information (this is R1). The scaffold's default
  guard is date-based (`make_split`).
- Fact: the TOP per-phase outcome CSVs carry no start/completion dates. TOP keeps dates
  in a separate `data/nctid_date.txt`. Run naively, the date-based `make_split` drops
  every TOP row (all dates None), which would look like an empty dataset, not an error.
- Counter-case: TOP ships its own `phase_{I,II,III}_{train,valid,test}.csv` split. R2
  already requires using the baseline's own split, so for TOP we evaluate on that split
  (`scripts/train_top.py`) and R1 is satisfied by TOP's construction, not by our date
  guard.
- Mitigation: state "split = TOP official" with every TOP result. If you join
  `nctid_date.txt` to get dates, then also run the R1 date guard as an independent check.
  The loader raises `KeyError` if the `nctid` column is missing, so a header mismatch
  fails loud rather than silent.

## R8 - Molecule recognition vs generalization (Severity: Quality-Trust)
- What happened: with Morgan fingerprints the Phase-2 spine reached AUROC 0.741, but
  fingerprints carry 0.90 of feature importance and ICD only 0.07. Splitting the temporal
  test set by whether the molecule was seen in training: seen 0.752 (1,076 units), novel
  0.589 (210 units).
- Reading: most of the lift is the model recognizing a drug and reusing its known approval
  history across the drug's other indication-units, not learning structure->approval that
  generalizes to new chemistry. It also barely uses the indication, so it cannot separate
  a drug approved for one disease from the same drug failing for another.
- Not leakage: the transfer uses a drug's earlier approvals to predict later ones, the
  temporal split is intact, and no unit's own label crosses sides. It is legitimate for a
  temporal/forward setting -- but it must be reported WITH the seen/novel decomposition.
- Implication for the 2024 forward test: performance there will track the novel-molecule
  number to the extent 2024 introduces new chemistry. Report seen/novel separately.
- Mitigation/next: finer indication features (3-char ICD or MONDO embedding) to lift the
  indication signal; consider a molecule-disjoint split as a stricter generalization test.

## R5 - Drug-indication roll-up (Severity: Quality-Trust) [BUILT]
- Assumption: the code can roll trials up to the drug-indication level, as the Novartis
  solution did.
- Status: built. The unit key is (regimen = sorted ChEMBL molecule-id set, indication).
  `audit_rollup.py` measured coverage first (per R5): most units are single-trial, ~28%
  have >=2 trials, so the ridge aggregates for a minority and is near-identity otherwise
  (the DSAI shape). Multi-drug trials (72%) make the regimen key fragment; a
  primary-molecule keying is a possible refinement.
- Residual threat: salt/parent forms with different ChEMBL ids can split one true drug
  across units. Molecule InChIKey matching + skeleton fallback mitigates most of it.

## R9 - CTO label provenance and baseline (Severity: Quality-Trust)
- Assumption: for the forward test we know what CTO's labels are and what counts as a fair
  baseline (this is R2/R4 applied to CTO).
- Fact: CTO is a weak-supervision labeling dataset. Its labels come from an ensemble of
  labeling functions (trial `status`, `results_reported`, GPT-on-linked-publications,
  linkage to a next-phase trial, stock-price slope, news sentiment, adverse-event counts),
  aggregated by a random forest whose output is `pred_proba` in `phase{1,2,3}_CTO_rf.csv`.
  `human_labels_2020_2024.csv` is a human-validated subset for 2020-2024.
- Threat A (baseline): `pred_proba` reads outcome-adjacent signals known only at/after
  completion. It is a LABELER, not a from-trial-start predictor. Comparing our pre-trial
  spine to it would be the R7 error in reverse (crediting the baseline with the answer).
  Do NOT treat `pred_proba` as the baseline to beat.
- Threat B (label): a weak-supervision label is noisier than a measured one (R4). It sets
  a ceiling and can reward the wrong thing.
- Mitigation: use `human_labels_2020_2024` as the gold forward target. Also report against
  the ChEMBL-derived label (identical definition to training) so generalization is not
  confounded by a label-definition switch. Compare predictively to base rate and to a
  published pre-trial predictor (HINT / TOP-XGBoost), not to `pred_proba`. Carry provenance.

## R10 - Calibration and deployment (Severity: Quality-Trust)
- Assumption: the product returns a probability a user can act on ("72% chance of
  success"), so the number must be calibrated, not merely well-ranked.
- Threat: a model can have good AUROC and still be badly miscalibrated; a confident wrong
  probability is worse than an honest uncertain one for a funding decision. The ridge
  roll-up uses `class_weight="balanced"`, which deliberately distorts the probability
  scale and must be recalibrated before any probability is shown.
- Counter-case: reliability curve, Brier score, and ECE make miscalibration visible and
  fixable (e.g. Platt / isotonic on a held-out slice).
- Mitigation: report reliability + Brier + ECE beside AUROC/log-loss on every eval.
  Calibrate on the fit side only (never on the forward holdout, R1). Do not surface a
  probability the calibration analysis does not support. The novel-molecule slice (R8) is
  the case most likely to be both miscalibrated and most consequential -- report it
  separately and gate the go/no-go use case on it.

## R11 - Featurization parity across datasets (Severity: Quality-Trust)
- Assumption: CTO trials are featurized the same way as the TOP trials the spine trained
  on, so a performance drop reflects generalization, not a change in how features are made.
- Fact: CTO ships no SMILES, no ICD, and no condition features. Per-trial drug and
  condition text lives only in CTTI.zip (the AACT dump). SMILES and ICD must be
  reconstructed -- drug-name -> SMILES/ChEMBL, condition -> MONDO/ICD via `mondo_xref` --
  exactly as they were for TOP.
- Threat: a different name->SMILES dictionary, a different ICD mapping, or a different
  fingerprint recipe would make CTO features a distribution shift of our own making, and
  the forward number would measure our pipeline drift instead of the model.
- Mitigation: `cto_source.py` reuses the TOP feature construction and the same maps.
  Audit CTO's molecule-match rate and indication-match rate the way `join_chembl.py` /
  `label_indication.py` did for TOP, and report them with the forward result. Dedupe any
  NCT ids shared between TOP (train) and CTO (forward) so the holdout is truly unseen.