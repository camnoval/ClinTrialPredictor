# Changelog

This is the project history. The newest entry is first.

## Session 8 - CTO audit + forward-test plan + product endgame
- Direction set: the 2024 forward test (CTO) and pipeline robustness, in service of a
  deployable go/no-go tool (paste an NCT id -> calibrated P(success)). Recorded the
  product endgame and the settled evaluation decisions in Architecture/Handoff.
- Built `scripts/inspect_cto.py` (audit-before-build). Audited chufangao/CTO on HF: it is
  a weak-supervision LABELING dataset (~2.4 GB, 11 files), not a featurized trial table.
  human_labels_2020_2024.csv = the human-validated 2020-24 gold set (target). phaseX_CTO_rf
  `pred_proba` = a labeler (status/results/linkage/news signals), NOT a pre-trial predictor.
  No SMILES/ICD/condition anywhere in the CSVs; interventions/conditions live in CTTI.zip
  (2 GB AACT dump). Added R9 (CTO label provenance / baseline), R10 (calibration +
  deployment), R11 (featurization parity: rebuild SMILES/ICD with the TOP-style maps).
- Settled: fit on all TOP, CTO 2020-24 as a single forward holdout (dates present -> real
  R1 guard, closes R6 for CTO); report BOTH labels (ChEMBL-derived for generalization,
  CTO gold for the product number); carry Phase-2 framing; always report seen/novel (R8);
  add calibration (reliability/Brier/ECE) as a first-class metric.
- Next: `--zip` audit of CTTI.zip, then `cto_source.py`, then `train_forward.py`.

## Session 7 - fingerprints + honest decomposition
- Added Morgan fingerprints (from smiless) + ICD-chapter features to the trial model.
  Phase-2 AUROC 0.641 -> 0.741, log-loss 0.678 -> 0.604. phase_ordinal importance fell to
  ~0.005 (progression proxy gone); fingerprints carry 0.90, ICD 0.07.
- Added seen-vs-novel-molecule breakdown. Seen-molecule units AUROC 0.752 (1,076), novel
  0.589 (210): the lift is drug recognition, not structural generalization. Added R8.
- Added `--max-phase` (Phase-2 = DSAI framing) and grouped feature-importance reporting.

## Session 6 - the DSAI spine
- `train_rollup.py`: trial XGBoost with unit-grouped OOF -> ridge (logistic) roll-up ->
  log-loss/F1/AUROC at the unit level on TOP's temporal holdout. Ridge beats mean-pred.
- Found the first (dense-feature) number was driven by phase_ordinal as a label proxy;
  added the Phase-2 framing to strip it.

## Session 5 - MONDO crosswalk (maximize matches)
- `fetch_mondo.py`: parse mondo.obo into an ICD-10 <-> MeSH <-> EFO + synonym table.
- `build_units.py --xref`: resolve both TOP (ICD + name) and ChEMBL (MeSH + EFO + name)
  to shared MONDO ids and match on identity. Units 2,818 -> 5,782; balance 54.8% -> 51.2%
  positive; ~half of new coverage came from the ontology bridge (no sibling inflation).

## Session 4 - ChEMBL labels + indication join
- Reframed as an open-data reconstruction of DSAI: real drug-indication approval label
  from ChEMBL `max_phase_for_ind`, not a TOP-internal proxy.
- `fetch_chembl_labels.py` (web API -> CSV). `join_chembl.py`: molecule match by InChIKey
  = 90.6%. Found molecule-level approval is degenerate (~91% of trials contain an approved
  drug) -> must be indication-specific. `label_indication.py`: exact name match = 28%
  labelable at 63% pos; loose token-subset inflates positives (dropped). `build_units.py`
  explodes trials into (regimen, indication) units.

## Session 3 - first real run + leakage caught
- Ran on real TOP (phase_{I,II,III} files, 1787/6102/4576 trials). End to end clean.
- First numbers were AUROC 0.86/0.82/0.74 -- too high. Traced to leakage: `was_terminated`
  (from status) and `has_why_stop` (from why_stop) are outcome-derived. Added R7.
- `train_top.py` now uses pre-trial features only by default; `--with-leaky` reproduces
  the inflation for demonstration (honest 0.52 vs leaky 0.86 on synthetic data).
- Honest numbers are the real floor (~0.5-0.6 range expected); molecule/disease/criteria
  features are the way up (Handoff step 3). Gate green: 15 tests.

## Session 2 - TOP loader, xgboost, train driver
- Confirmed the TOP schema against the benchmark repo and set `_COL`:
  nctid, status, why_stop, label, phase, diseases, icdcodes, drugs, smiless, criteria.
  Found the outcome CSVs carry no dates (dates live in a separate nctid_date.txt).
- Rewrote `top_source.py`: parse list-valued columns, carry count features, fail loud on
  a missing id column. Added `tests/test_top_source.py` (inline fixture, stdlib only).
- `build_records` now merges source-provided features instead of discarding them.
- Wired `make_xgboost()` (heavy import inside the factory).
- Added `scripts/train_top.py`: trains on TOP's own per-phase split and reports
  AUROC + PR-AUC (R2/R6). Verified the load -> matrix -> metric path end to end.
- Added R6. Gate green: 15 tests pass. No real metrics yet (needs xgboost + the CSVs).

## Session 1 - scaffold
- Built the layer structure: foundation, domain, services, sources, orchestration,
  scripts, and tests.
- Defined the `Source` contract and the identity keys (`NCTId`, `DrugIndication`).
- Added domain records with label provenance. A measured label beats a derived label.
- Built and tested the temporal split guard (R1). Added the pure feature functions.
- Added the TOP source stub, `inspect_top.py`, `run_checks.py`, `config.yaml`, and the
  doc set.
- The gate is green. 13 tests pass. The package imports. There is no data and there are
  no trained models yet.