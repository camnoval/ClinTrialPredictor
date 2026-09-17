# trial-pos

This project ports the method from the 2019 Novartis PoS competition solution
(`bjoernholzhauer/DSAI-Competition-2019`) to public data. The goal is to beat a public
baseline.

## What it does
The project predicts trial or drug-indication approval from features known before the
outcome. The unit is a trial (NCT id). Trials roll up to a (drug, indication) key. There
are two flows: build (sources give records, then the code adds features) and train/eval
(the code splits by date, trains models, and reports metrics).

## Data
- TOP (Fu et al., 2022): the primary benchmark. It has XGBoost and HINT baselines.
  https://github.com/futianfan/clinical-trial-outcome-prediction (non-commercial use)
- CTO (Gao et al., 2024): recent trials from 2020 to 2024. Use it to test the method on
  new data.

Do not compare the result to the Novartis score of 0.202. That score uses different
data, a different level, and a different metric. Compare to a public baseline on its own
metric and split.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/inspect_top.py data/top/<file>.csv   # read the real schema first
python scripts/run_checks.py                          # import check and tests
```

## Layout
```
src/trial_pos/
  foundation/     identity keys and the Source contract
  domain/         TrialRecord, DrugIndicationRecord, Label
  services/       features, the date split guard, model stubs
  sources/        one file per dataset (top_source.py)
  orchestration/  connects the flows
scripts/  inspect_top.py, run_checks.py
tests/    one file per module
docs/     Architecture, CodebaseReference, Handoff, Risks, Changelog, PlainLanguageGuide
```

## Documents
- `docs/Handoff.md`: the state and the next steps. Read this first.
- `docs/Architecture.md`: the design, the layer table, and the comparison problem.
- `docs/CodebaseReference.md`: the file map.
- `docs/Risks.md`: the assumptions that can break the project (R1...).
- `docs/Changelog.md`: the history.
- `docs/PlainLanguageGuide.md`: a non-technical overview.
