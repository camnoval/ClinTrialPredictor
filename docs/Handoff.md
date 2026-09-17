# Handoff

This document gives the next conversation the context to continue the project. Read it
first. Then read `Architecture.md` and `Risks.md`.

## What the project does
The project predicts clinical trial approval from public data. It ports the method from
the 2019 Novartis competition solution (`bjoernholzhauer/DSAI-Competition-2019`). The
goal is to beat a public baseline.

## Mental model
- The job: predict trial or drug-indication approval. Use only features known before the
  outcome.
- The spine: the trial record. Its key is the NCT id. Trials roll up to a
  (drug, indication) key.
- The flows: build (sources give records, the code adds features, this makes the dataset)
  and train/eval (the code splits by date, trains models, and reports metrics).

## Data
- TOP (Fu et al., 2022): the primary benchmark. It has an XGBoost baseline and an HINT
  baseline. Repo: https://github.com/futianfan/clinical-trial-outcome-prediction
  The license is non-commercial (R3).
- CTO (Gao et al., 2024): the forward-test set (recent trials, 2020-2024). Repo:
  `chufangao/CTO` on HuggingFace, ~2.4 GB, 11 files (audited session 8 via
  `scripts/inspect_cto.py`). It is a weak-supervision LABELING dataset, not a featurized
  trial table:
  - `human_labels_2020_2024.csv` (10 MB): AACT `studies` fields for 2020-24 + `labels`
    (0/1) + `completion_year`. The human-validated gold target for the forward test (this
    is the R4 "91% F1 vs expert" recent set).
  - `Merged_all_trial_linkage_outcome_df.csv` (20 MB): nctid -> `outcome` + phase-linkage.
  - `phase{1,2,3}_CTO_rf.csv`: per-trial labeling-function votes + `pred_proba`. The
    `pred_proba` is a LABELER (uses status/results/linkage/news), not a pre-trial
    predictor -- do not use it as a predictive baseline (R9).
  - `pubmed_gpt_outcomes.csv` (296 MB), `news_lfs.csv`, `labels_and_tickers.csv`: more
    labeling-function signals.
  - `CTTI.zip` (2 GB): the AACT/CTTI dump. The ONLY place per-trial drug/condition text
    lives. No SMILES or ICD anywhere in CTO -- rebuild them with the TOP-style maps (R11).
- AACT: the public mirror of ClinicalTrials.gov. It holds trial status, not approval
  labels. Use it for extra features only.

## The comparison rule (important)
Do not compare the result to the Novartis score of 0.202. That score uses private data,
a different level, and a different metric. Compare the result to a public baseline on the
baseline's own metric and split. See `Risks.md` R2.

## Current state
The gate is green (15 tests, package imports). The full DSAI-2019 spine now runs end to
end on open data, with a real ChEMBL/MONDO-derived approval label and temporal evaluation.

Pipeline (scripts, in run order; all read/write under `data/`, which stays out of git):
1. `fetch_chembl_labels.py` -> `data/chembl/drug_indication.csv` (60,055 rows) and
   `molecules.csv` (10,382 molecules, 3,544 approved, 7,265 with InChIKey). Pulls via the
   ChEMBL web API (`pip install chembl_webresource_client`); no bulk download.
2. `fetch_mondo.py` -> `data/ontology/mondo_xref.csv` (32,102 MONDO terms with EFO/MeSH/
   ICD-10 xrefs + exact synonyms). Downloads mondo.obo (cached).
3. `build_units.py --xref` -> `data/chembl/units.csv`. Joins TOP molecules to ChEMBL by
   InChIKey (90.6% of molecule instances match), resolves indications through MONDO
   (ICD-10 + name -> shared ontology id), and explodes each trial into (regimen,
   indication) units labelled by `max_phase_for_ind` (4->approved, {1,2}->fail, 3 excluded).
   Result: 5,782 units, 51.2% positive (name-matched 2,992 / MONDO-matched 2,790).
4. `train_rollup.py --max-phase II` -> the spine: trial XGBoost (pre-trial features +
   Morgan fingerprints + ICD chapter) with unit-grouped OOF, ridge roll-up, log-loss at
   the unit level on TOP's temporal holdout.

Headline result (Phase-2 framing, the honest/DSAI-comparable one):
- ridge roll-up: log-loss 0.604, AUROC 0.741, PR-AUC 0.714 (base rate log-loss 0.711).
- BUT it is molecule-recognition-dominated (R8): fingerprints carry 0.90 of importance,
  ICD 0.07. Split by molecule: seen-in-training units AUROC 0.752 (1,076), truly novel
  molecules AUROC 0.589 (210). The lift is mostly recognizing known drugs and reusing
  their approval history (legitimate for temporal/forward use, not structural
  generalization). Always report this decomposition with the number.
- Full-phase (`--max-phase III`) reads higher (AUROC 0.794) but is inflated by phase
  progression acting as a label proxy (R7-adjacent); prefer the Phase-2 number.

Also settled earlier:
- TOP schema confirmed; loader parses list columns, fails loud on missing id (R6: TOP
  outcome CSVs carry no dates; TOP's own split is the 2014 temporal split, so R1 holds).
- Leakage caught and fixed (R7): `was_terminated`/`has_why_stop` are outcome-derived and
  excluded; `train_top.py --with-leaky` reproduces the inflation for demonstration only.
- `train_top.py` reports the TOP benchmark itself (AUROC/PR-AUC + log-loss/F1) on TOP's
  official per-phase split. Honest Phase-2-ish TOP numbers land ~0.51-0.58 AUROC with the
  thin pre-trial features (before fingerprints).

New external dependencies (not in core pyproject so the gate stays bare-importable):
`chembl_webresource_client`, `rdkit`. Install into the venv when running the pipeline.

## Next steps (pick up here): the 2024 forward test (CTO)
The direction is set: run the trained DSAI spine forward on CTO's 2020-2024 trials, and
judge robustness by how well it generalizes (seen/novel molecule, R8) and how well it is
calibrated (R10). This is the number the product depends on.

### Product endgame (why this matters)
The destination is a decision tool. A user pastes a ClinicalTrials.gov NCT id; the tool
pulls the trial, featurizes it the SAME way as training, and returns a calibrated
probability of success (e.g. "72%"). A sponsor choosing which of two candidate drugs to
put a trial behind can then rank them and fund the stronger bet. Two consequences drive
the work:
- Calibration is first-class, not an afterthought. A "72%" is only honest if ~72% of
  72%-bucket trials actually succeed. Report reliability curve + Brier + ECE next to
  AUROC/log-loss (R10).
- The highest-value case -- a novel drug vs a novel drug -- is exactly where the spine is
  weakest (R8: novel-molecule AUROC ~0.59, because fingerprints carry 0.90 of importance
  and the model recognizes known drugs). So today the tool is most trustworthy for
  known-molecule / new-indication questions; the indication-aware features are the lever
  on the novel case. State this honestly to any user.

### Settled evaluation decisions (session 8)
- Fit set: train the spine on ALL of TOP (train+valid+test); CTO 2020-24 is a SINGLE
  forward holdout. CTO trials post-date TOP's 2014 boundary, so R1 holds by construction;
  CTO also carries real dates, so additionally run the `make_split` date guard as an
  independent check (this closes R6 for CTO, unlike TOP).
- Labels: report BOTH. (i) ChEMBL-derived unit label (identical definition to the TOP
  training label) = the clean DSAI generalization number, lead with this. (ii) CTO's
  human-validated `labels` (human_labels_2020_2024) = the independent, product-relevant
  trial-success number. See R9.
- Framing: carry the Phase-2 (`--max-phase II`) framing; ALWAYS report the seen/novel
  molecule split (R8).
- Baseline: CTO's `pred_proba` is a weak-supervision LABELER, not a fair pre-trial
  predictive baseline -- do not compare against it as one (R9). Compare to base rate and,
  if available, a published pre-trial predictor (HINT / TOP-XGBoost) on a comparable
  split/metric (R2). Check arxiv 2406.10292 for the paper's reported prediction numbers.
- ChEMBL is treated as a fixed current snapshot.

### Concrete plan
1. `python scripts/inspect_cto.py --zip data/cto/CTTI.zip` -- confirm the AACT tables and
   columns (interventions name/type; conditions or browse_conditions mesh terms;
   eligibilities criteria).
2. `sources/cto_source.py` (per "how to add a data source"): join studies + interventions
   + conditions, rebuild SMILES via drug-name -> ChEMBL/TOP dictionary and ICD/MONDO via
   condition -> `mondo_xref`, and emit records with the SAME feature construction as
   `TopSource` (R11). Add a fixture test.
3. `build_units.py` on CTO (reuse) to make ChEMBL-derived units for label (i).
4. `scripts/train_forward.py`: fit spine on all TOP, predict CTO; report
   log-loss/AUROC/PR-AUC + calibration (reliability/Brier/ECE) + seen/novel split,
   against both labels.
5. Gate green (`scripts/run_checks.py`); update Changelog + this file.

### Diagnostic scripts already built (re-audit if data changes)
`join_chembl.py` (molecule-level match audit), `label_indication.py` (exact-vs-crosswalk
label coverage), `audit_rollup.py` (roll-up key coverage), `inspect_cto.py` (CTO dataset
audit, three modes: HF / --local-dir / --zip).

### Deferred / parallel
- Optional immediate robustness read, no download needed: a molecule-disjoint split on
  the TOP data already on disk, to measure the novel-chemistry ceiling directly (the
  product's hard case).
- Indication-aware features (3-char ICD or a MONDO embedding) -- the lever on the novel
  number and the scientifically harder target.
- Still stubbed: the Bayesian logistic (`bayesian_logistic`) and CBPS weighting. The ridge
  roll-up lives inside `train_rollup.py`, not yet in `services/models.py`.

## How to add a data source
1. Run an inspect script on the raw input. Learn the real column names.
2. Write the pure `normalize` and `to_domain` methods. Add a test with a fixture.
3. Implement `Source` in one new file in `sources/`.
4. Add one block in `config.yaml`.
5. Run `python scripts/run_checks.py`.

## How to add a model
1. Add a factory function in `services/models.py`. Import the heavy package inside the
   function.
2. Keep the decision logic in pure code. Keep the model call thin.
3. Train only on the split from `make_split`. Never fit on data after the boundary (R1).

## Invariants to keep
- R1: no future information reaches training. Every split goes through `make_split`.
- Provenance: every label carries its source. A measured label beats a derived label.
- Run the gate (`scripts/run_checks.py`) before each commit.