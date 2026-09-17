"""Model slots. Import heavy deps inside each factory, not at module top.

Model plan (Architecture.md): the Novartis ensemble is two trial-level XGBoost models,
a ridge roll-up that combines trial predictions into one drug-indication prediction
(glmnet -> sklearn), and a Bayesian logistic at the drug-indication level
(rstanarm -> pymc/bambi). Wire XGBoost first (the current step); the roll-up and the
Bayesian model wait until the drug-indication roll-up exists (R5) and TOP reports a
real metric.
"""
from __future__ import annotations


def make_xgboost(**overrides):
    """Trial-level gradient-boosted classifier.

    Returns an unfitted xgboost.XGBClassifier. XGBoost handles NaN natively, so the
    feature matrix may leave missing features (e.g. duration_days when TOP has no dates)
    as NaN without imputation. Import is inside the function so the package imports in a
    bare environment (see run_checks.py gate).
    """
    from xgboost import XGBClassifier  # heavy; keep local

    params = dict(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        objective="binary:logistic",
        eval_metric="logloss",
        n_jobs=0,
        random_state=0,
    )
    params.update(overrides)
    return XGBClassifier(**params)


def relaxed_ridge_rollup():
    raise NotImplementedError(
        "port the ridge roll-up (glmnet -> sklearn) after the drug-indication roll-up "
        "exists and its key coverage is audited (R5)"
    )


def bayesian_logistic():
    raise NotImplementedError(
        "port the Bayesian logistic (rstanarm -> pymc/bambi) after the roll-up and after "
        "TOP reports AUROC/PR-AUC on its own split"
    )