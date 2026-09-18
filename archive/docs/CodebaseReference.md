# Codebase Reference

## Mental model
Read this section first. The job is to predict approval and beat the public baseline.
The spine is the trial record (`NCTId`), which rolls up to `DrugIndication`. The two
flows are build and train/eval. See `Architecture.md` for the layer table and the
comparison problem.

## File map

### foundation/ (depends on nothing)
- `identity.py`: `NCTId` (validates the format) and `DrugIndication` (normalizes the text).
- `contracts.py`: the `Source` base class. It has four methods: `fetch` (impure),
  `normalize` (pure), `to_domain` (pure), and `run` (the driver).

### domain/ (depends on foundation)
- `records.py`: `TrialRecord`, `DrugIndicationRecord`, `Label`, and `Provenance`.
  `Label.resolve` prefers a measured label over a derived label.

### services/ (depends on domain; pure unless stated)
- `features_pure.py`: trial-level feature functions. Add one function per feature.
- `temporal_split.py`: the date split and the leakage guard. `split_by_date`,
  `assert_no_leakage`, and `leakage_report`.
- `models.py`: three model stubs. XGBoost, the ridge roll-up, and the Bayesian model.

### sources/ (depends on foundation and domain; fetch is impure)
- `top_source.py`: the TOP benchmark loader. `_COL` is confirmed against the TOP repo
  (nctid, status, why_stop, label, phase, diseases, icdcodes, drugs, smiless, criteria).
  Parses list columns, carries count features, and KeyErrors if `nctid` is absent. The
  outcome CSVs have no dates (R6); confirm the header on your download with
  `inspect_top.py`.
- Later: `cto_source.py` and `aact_source.py`. One file each. Register each in `config.yaml`.

### orchestration/ (depends on all above)
- `pipeline.py`: `build_records` (build flow) and `make_split` (train/eval flow).
  `make_split` checks the leakage guard.

### scripts/
- `inspect_top.py`: print the real TOP schema. Run this first.
- `run_checks.py`: the gate. It runs the import check and the tests.
- `train_top.py`: train `make_xgboost()` on TOP's own per-phase split; report AUROC and
  PR-AUC (R2/R6). Usage: `python scripts/train_top.py --data-dir data/top --phase all`.

### tests/ (one file per module)
- `test_temporal_split.py`: the leakage guard. It checks a leaky train set and a leaky
  test set.
- `test_features_pure.py`: known input gives known output.
- `test_identity_and_labels.py`: id validation and label resolution.
- `test_top_source.py`: the TOP loader on an inline fixture in the real schema.
- `_run_stdlib.py`: a small test runner for an environment without pytest.

## config.yaml
The source registry. It lists each source, its role, its label type, and the date
boundary. The orchestration code and the audit tools read this file.