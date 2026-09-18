# archive/ — superseded code from Gen 1 and Gen 2

Nothing in this folder is on the live code path. It is kept, not deleted, because parts of
it are **parked for revival** (marked below) and because the reasoning that ended it is
worth being able to check rather than re-derive.

Archived 2026-09-16. The live project is described in `docs/Handoff.md`.

---

## The three generations

This repo accumulated three projects targeting three different labels. Confusion between
them is the single most expensive thing a fresh session can walk into, because Gen 1/2
docs read as authoritative and describe a pipeline that no longer exists.

| Gen | Target | Data | Status |
| --- | --- | --- | --- |
| 1 | ChEMBL drug-indication approval (`max_phase_for_ind`) | TOP + ChEMBL + MONDO, Morgan fingerprints | ARCHIVED |
| 2 | Same target, reconstructing the 2019 Novartis 263-feature spec | + AACT trial features, MoA target encoding, TA-9, disease-type-152 | ARCHIVED |
| 3 | **Per-trial primary-endpoint-met + phase advancement** | AACT posted results | **LIVE** |

### Why Gen 1 and Gen 2 ended

ChEMBL `max_phase_for_ind` is regulatory approval of a drug-indication pair. That is not
"this trial met its primary endpoint." The relationship is many-to-one and the causal
direction is wrong: one approval can rest on two pivotal trials, or one trial plus an
extension study, or a surrogate endpoint under an accelerated pathway. So a model trained
on it is a **reference-class prior** ("drugs like this tend to get approved eventually"),
not a **trial predictor** ("this trial's result").

The full argument is in the live `src/trial_pos/services/endpoint_label.py` module
docstring. Gen 3 replaced the label with one taken from the trial's own posted primary
analysis.

Gen 2 additionally superseded Gen 1's own framing — `docs/DSAIReconstructionPlan.md` says
so explicitly ("supersedes the fingerprint spine framing"). So Gen 1 was already dead
before Gen 3 started; this archive holds two layers, not one.

---

## Inventory

### `archive/src/trial_pos/`

Four whole subpackages, never touched by Gen 3:

- `domain/records.py` — `TrialRecord`, `DrugIndicationRecord`, `Label` with provenance.
- `foundation/identity.py` — `NCTId`, `DrugIndication` keys.
- `foundation/contracts.py` — the `Source` base class (`fetch`/`normalize`/`to_domain`/`run`).
- `sources/top_source.py` — TOP benchmark loader.
- `orchestration/pipeline.py` — `build_records` + `make_split`.

### `archive/src/trial_pos/services/`

- `features_pure.py` — **note:** contains `was_terminated`, derived from trial status. Safe
  in Gen 1 (TOP's status is not AACT's `overall_status`) but `overall_status` is on
  `LABEL_DERIVED_FIELDS`. If this file's shape is ever reused against AACT, that function
  is an R7 leak. It is inert only because the file is dead.
- `models.py` — XGBoost factory; ridge roll-up and Bayesian logistic were never implemented.
- `temporal_split.py` — completion-before / start-on-or-after split with a leakage assert.
  The *shape* is right and was adopted by Gen 3's split decision; the implementation is
  keyed to `TrialRecord`, which is archived.
- `moa_encoding.py` — **PARKED FOR POSSIBLE REVIVAL.** The DSAI "crown jewel" MoA-class
  target encoding, with time-respecting leave-one-out and three deliberate leak modes for
  verification. Gen 3 wants a mechanism attribution family, and this computes one — but
  keyed on drug-indication units, targeting ChEMBL approval. Re-keying to NCT id and
  re-targeting to endpoint-met is a real job, not a rename.
- `disease_ta.py`, `disease_type.py`, `obo_edges.py` — **PARKED FOR POSSIBLE REVIVAL.**
  TA-9 assignment, Trialtrove-152 disease-type → MONDO crosswalk, MONDO `is_a` edge
  parsing. Same caveat: the disease-type-152 input does not exist for plain AACT trials
  outside the TOP-matched cohort.

### `archive/scripts/`

Gen 1 training and TOP/ChEMBL plumbing:
`train_top.py`, `train_rollup.py`, `train_forward.py`, `audit_rollup.py`,
`inspect_top.py`, `join_chembl.py`, `label_indication.py`, `build_units.py`,
`fetch_chembl_labels.py`, `fetch_mondo.py`, `build_mondo_edges.py`

Gen 2 feature-reconstruction plumbing:
`pull_aact.py`, `audit_moa_prior.py`, `build_moa_target_encoding.py`,
`assign_disease_ta.py`, `assign_disease_type.py`, `build_disease_type_anchors.py`,
`build_cto.py`, `inspect_cto.py`, `audit_cto_coverage.py`

**Parked for revival specifically:**
- `fetch_chembl_labels.py` and `join_chembl.py` already pull `max_phase_for_ind`, which is
  what the live project needs for the advancement target (approval as a terminal
  advancement event) and for the post-hoc validation check. See `docs/Handoff.md` §3.6,
  uses B and C.
- `build_units.py` holds the (drug, indication) rollup key, which the advancement label
  needs some version of.

**Magic number to fix on revival:** `max_phase == 4` means "approved" as a bare literal in
`join_chembl.py`, `label_indication.py`, and `build_units.py`. Give it a shared named
constant if any of those comes back.

### `archive/tests/`

35 tests covering the archived modules:
`test_top_source.py` (2), `test_features_pure.py` (4), `test_identity_and_labels.py` (4),
`test_temporal_split.py` (5), `test_moa_encoding.py` (6), `test_obo_edges.py` (3),
`test_disease_ta.py` (4), `test_disease_type.py` (7).

The gate went from 131 → **96** as a result. `tests/_run_stdlib.py` discovers `test_*.py`
by glob, so moving the files out was sufficient — no runner change was needed. If these
tests are ever revived, they must be moved back into `tests/` (or the runner pointed at
this folder), not left here to rot silently.

### `archive/docs/`

- `Handoff.md` — the Gen 2 handoff. Actively contradicts the live project; replaced.
- `Architecture.md`, `Risks.md`, `Changelog.md`, `CodebaseReference.md`,
  `PlainLanguageGuide.md` — all Gen 1/2 era.
- `DSAIReconstructionPlan.md`, `DsaiFeatureSpec.md`, `dsai_feature_spec.csv` — the Gen 2
  north-star documents and the 263-feature crosswalk.
- `disease_type_aliases.csv` — curation input for the disease-type-152 crosswalk.

The `Risks.md` register (R1–R11) is worth reading before reinventing any of its entries.
Several are still live concerns under different numbering in `docs/Handoff.md` — notably
R7 (leakage) and R10 (calibration is first-class).

### `archive/run_all.ps1`

End-to-end Gen 1 driver: venv, install, TOP schema check, gate, `train_top.py`. Contains
hardcoded absolute paths to one machine.

---

## What was deliberately NOT archived

- `config.yaml` and `README.md` are stale Gen 1 artifacts but were left in place, because a
  repo needs a live registry and a live README. They need **rewriting**, not moving.
- `probe_ctgov.py` was written during Gen 2 but probes the live CT.gov v2 API, which is the
  fetch path the Gen 3 live tool eventually needs. Kept, marked parked.
- `inspect_data_dir.py` is a generic read-only inventory tool. Its file-description strings
  mention archived scripts, which is cosmetic.
- The root-level `.txt` run logs (`label_gaps*.txt`, `relabel_*.txt`,
  `recon_validation*.txt`, `pull_results_log.txt`, `data_inventory.txt`) are pasted output
  from **current** Gen 3 work. Clutter, but evidence.