# Architecture

## Purpose
This project predicts clinical trial approval from public data. It compares the
result to a public baseline.

## Mental model
Read this section first.
- The job: predict trial or drug-indication approval. Use only features known
  before the outcome.
- The spine: the trial record. Its key is the NCT id. Trials roll up to a
  (drug, indication) key.
- The flows: there are two.
  1. Build flow: a source gives records. The code adds features. This makes the dataset.
  2. Train/eval flow: the code splits the dataset by date. It trains models. It reports metrics.

## The comparison problem
Do not compare the result to the Novartis score of 0.202. The two are not comparable.
These are the reasons:
- The Novartis score uses private Informa data. This project uses public data.
- The Novartis score predicts at the drug-indication level.
- The Novartis score uses a different metric and a different date split.

Compare the result to a published public baseline. Use the baseline's own metric and split.

Two benchmarks apply:
1. TOP (Fu et al., 2022). This is the primary benchmark. It has an XGBoost baseline
   (about 0.60 AUROC in Phase II) and stronger baselines (HINT about 0.645). The goal
   is to beat the XGBoost baseline, then measure the gap to HINT.
2. CTO (Gao et al., 2024). Recent trials, 2020 to 2024, used as the forward-test set. It
   is a weak-supervision LABELING dataset: it supplies outcome labels (including a
   human-validated 2020-24 gold set) but no SMILES/ICD/condition features -- those are
   rebuilt from the CTTI/AACT dump with the same maps used for TOP (R11). Its own
   `pred_proba` is a labeler, not a pre-trial predictive baseline (R9).

Do TOP first. Add CTO after TOP works.

## Product goal (endgame)
The research question ("can the Novartis 2019 method be reconstructed on public data and
made to generalize?") serves a product. The destination is a decision tool:

- Input: a ClinicalTrials.gov NCT id.
- The tool pulls the trial, featurizes it with the SAME pipeline used in training, and
  returns a CALIBRATED probability of success (e.g. "72%").
- Use: a sponsor deciding which of two candidate drugs to fund a trial for ranks the
  candidates and backs the stronger one.

Two design consequences follow, and they shape evaluation:
- Calibration is a first-class metric. A ranking metric (AUROC) is not enough; a "72%"
  must mean 72% for the claim to be usable. Report a reliability curve, Brier score, and
  ECE alongside AUROC/log-loss. See `Risks.md` R10.
- The most valuable case (novel drug vs novel drug) is the current model's weakest, because
  the lift is drug recognition, not structure->approval generalization (R8). Always report
  the seen/novel-molecule split with any headline number, and treat the novel-molecule
  figure as the honest ceiling for the go/no-go use case until indication-aware features
  close the gap.

The forward test on CTO (2020-2024) is the first real measurement of this: fit on all of
TOP, predict CTO as a single forward holdout, and read generalization + calibration there.

## Layer table
A layer depends only on the layers above it in this list. A layer does not import a
layer below it.

| Layer | Depends on | Job | Location |
|---|---|---|---|
| Foundation | nothing | Identity keys and the Source contract | `foundation/` |
| Domain | Foundation | Records and labels | `domain/` |
| Services | Domain | Features, the date split, model stubs | `services/` |
| Sources | Foundation, Domain | One file per dataset | `sources/` |
| Orchestration | all above | Connect the flows | `orchestration/` |
| Scripts | any | Inspect and gate drivers | `scripts/` |
| Tests | any | One test file per module | `tests/` |

## Pure code and impure code
- Pure code does no input or output. It is fast. It has tests. Examples:
  `features_pure.py`, `temporal_split.py`, and the `normalize` and `to_domain` methods.
- Impure code touches files, the network, or the database. It is thin. Examples: each
  source's `fetch` method, and the model `fit` calls.
- Import a heavy package (xgboost, pymc) inside the method that needs it. Do not import
  it at the top of the module.

## Model plan
The Novartis solution uses an ensemble of three parts. Port each part to Python.
- Two XGBoost models at the trial level. (R package: xgboost)
- One ridge model. It combines trial predictions into one drug-indication prediction.
  (R package: glmnet)
- One Bayesian logistic regression at the drug-indication level. (R packages: rstanarm
  and CBPS)

The stubs are in `services/models.py`.

Read `Risks.md` with this document.