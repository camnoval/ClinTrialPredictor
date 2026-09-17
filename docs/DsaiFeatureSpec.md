# DSAI feature spec — 263 XGBoost features → open-data sources

Driven directly off Table S3 (mmc2.xlsx). Every feature is enumerated in the companion
`DSAI_feature_spec.csv` (feature, type, family, open_data_source, status, definition). This
file is the readable crosswalk: families, sources, and honest reconstruction status.

Status legend: VALIDATED (built + measured) · READY (source pulled, coverage confirmed) ·
BUILDABLE (join/derivation from a source we have) · APPROX (open analog, coarser than
Trialtrove) · GAP (no clean open equivalent).

| # feats | Family | Open-data source | Status |
| --- | --- | --- | --- |
| 12 | **MoA-class target encoding (crown jewel)** — moas/class_approvals/class_counts/meancll50/meanclu50 × {any, disease-type dt, therapeutic-area ta} | ChEMBL drug_mechanism/target + our approval label + time-respecting LOO | **VALIDATED** (audit AUROC 0.674, 77% cov, Beta(1/3,2.7) per spec) |
| 4 | Relative phase-2 program size — rel_log_size_{dis,ta}, rel_ph2_size_{dis,ta} | AACT accrual + phase-2 trial counts grouped by disease/TA | BUILDABLE (Table S4 top predictor) |
| 1 | Prior approval (drug, other indication) | ChEMBL max_phase history, cutoff ≤ phaseendyear | BUILDABLE (leakage-critical, R7) |
| 7 | Trial design flags — randomized/blinded/controlled/efficacy_assessed/pk_aspects/innovative_design/designscore | AACT designs | READY for 5/7 (~98% cov); APPROX for innovative_design (no basket/umbrella/adaptive tags in AACT) |
| 9 | Sponsor type — spons1–9 | AACT sponsors.agency_class (lead) | APPROX (coarser; 'top-20 pharma' needs a name list) |
| 7 | Accrual / trial size — int{target,actual}accrual, pct_accrual, duration | AACT enrollment(+type), dates | APPROX (AACT has ONE enrollment field → target-vs-actual not cleanly separable) |
| 4 | Timing — phaseendyear, years_since_1999, time_since_first_outcome, after2007 | AACT completion dates | READY (phaseendyear 100%) |
| 10 | Trial-outcome / termination score — termreason, safety, *trialendscore* | AACT why_stopped + overall_status | **GAP** — see below |
| 152 | Disease-type one-hot | condition → MONDO/MeSH → Trialtrove disease-type | APPROX (main disease-side mapping effort) |
| 9 | Therapeutic area (raw, 9) — ta1–9 | condition → MONDO → TA | BUILDABLE |
| 23 | Grouped TA — newta1–23 | derived from disease/TA mapping | BUILDABLE |
| 8 | Drug-name / novelty flags — insulin, fluvacc, is_a_generic, INN, is_a_combination, is_mab, monoclonal(_no_inn) | drug-name regex + ChEMBL molecule_type/INN | BUILDABLE (same regexes as feat_bjoern_trial.R) |
| 9 | Drug therapy-class — drugtype1–9 | Trialtrove TherapyDescription → ChEMBL molecule_type / ATC | APPROX (curated anticancer subclasses have no exact open equivalent) |
| 4 | Orphan designation — any/indication/onc/nononc | FDA Orphan Drug Designations DB (public) | BUILDABLE (join by drug/indication) |
| 1 | Derived — lcm_onc | prior_approval × oncology TA | BUILDABLE |

## The one real GAP to internalize

DSAI's **trial-outcome score** (`termreason`, `mean/max_trialendscore`, `trialendscore0–4`)
encodes Trialtrove's *structured trial OUTCOME* field — "Completed, primary endpoint(s)
met / not met / indeterminate; Terminated, safety." AACT does **not** carry endpoint-met as
a structured field; it has `overall_status` (Completed/Terminated/…) and free-text
`why_stopped`. So:
- The **termination-reason half** (why_stopped → 0–4 severity + safety flag) is reconstructable
  (already built in pull_aact).
- The **positive/negative-endpoint half** is the biggest open-data gap. Options to proxy:
  CTO's publication-linkage/GPT outcome labels, AACT results tables (sparse), or accept the
  loss. This is a top-ranked DSAI feature, so it must be flagged in every result — our
  reconstruction is partially blind here, by data availability, not by choice.

## Reconstruction priority (by DSAI importance × open-data feasibility)

1. **MoA-class target encoding** — done (validated). Upgrade to full 3-granularity with AACT phaseendyear.
2. **Relative phase-2 size** + **prior_approval** — top predictors, both BUILDABLE from AACT + ChEMBL now.
3. **Termination-reason** + drug/novelty flags + orphan + design + sponsor + TA — BUILDABLE/READY.
4. **Disease-type 152 one-hots** — mechanical but large; build via MONDO once.
5. **Trial endpoint-met outcome** — GAP; decide proxy (CTO linkage) or omit and report the omission.