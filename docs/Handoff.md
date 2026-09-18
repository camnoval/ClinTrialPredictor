# Handoff: per-trial endpoint predictor — session ending 2026-09-16 (rev 2)

Supersedes BOTH prior handoffs:
- the old `docs/Handoff.md` (the DSAI/ChEMBL-approval project — now in `archive/docs/`),
- the "per-trial endpoint predictor" handoff written earlier this session.

Written at the point where the repo has just been de-layered (three generations of code
separated, two archived) and the training population has been deliberately widened. Step 1
is done but its headline numbers are now **provisional**, for the reason given in §3.

---

## 1. The goal

**A live tool.** Paste a ClinicalTrials.gov NCT id, get back two calibrated probabilities
with the explicit factors driving each:

1. **primary endpoint met** — did the trial's own posted primary analysis clear its
   threshold,
2. **advancement to the next phase** — did the program move forward.

Tracked separately, never combined. They answer different questions and have different
failure modes: a trial can miss its endpoint and the program still advance, or meet it and
still be shelved for portfolio reasons.

**Validated retrospectively before it goes live.** Trained and tested on historical AACT
trials whose outcomes are already known, with a temporal split (§5), so the performance
claim is a back-test rather than an assertion.

### Settled product decisions

- **Thin inputs still get an answer**, flagged heavily, with a resolution path where the
  user supplies what the pipeline could not infer (drug identity, mechanism/target,
  indication mapping — all three tiers accepted). Now that the cohort restriction is gone
  this is the NORMAL path, not an edge case: far more trials will have a usable label than
  have a resolvable drug identity (§3).
- **Additive interpretable model is the deliverable**; gradient boosting is built only to
  measure what the additive restriction costs. **This justification is now provisional —
  see §3.**
- **Attribution reported at family level** (mechanism reference class, design quality,
  program context, sponsor history, disease area), which is robust to the heavy
  correlation between features.
- **Biologics stay in scope.** The small-molecule limit came from Morgan fingerprints,
  which are gone with Gen 1. The real constraint is name→ChEMBL resolution, which
  `name2chembl.csv` shows failing 67% of the time on raw intervention strings.

---

## 2. Repo lineage — read this before trusting any file

The repo contained **three generations** of code targeting three different labels. Two are
now in `archive/` (see `archive/README.md` for the full inventory and the reasoning).

| Gen | Target | Status |
| --- | --- | --- |
| 1 | ChEMBL drug-indication approval, via TOP + Morgan fingerprints | ARCHIVED |
| 2 | Same target, reconstructing the 2019 Novartis 263-feature spec | ARCHIVED |
| 3 | **This project.** Per-trial endpoint-met + advancement, from AACT | LIVE |

Why Gen 1/2 ended, in one line: ChEMBL `max_phase_for_ind` is regulatory approval of a
drug-indication pair, which is a downstream consequence of much more than one trial's
result. A model trained on it is a reference-class prior, not a trial predictor. The
reasoning is in `endpoint_label.py`'s own module docstring.

**Live code is now exactly:**

```
src/trial_pos/services/endpoint_label.py     the endpoint-met label engine
src/trial_pos/services/recon_stats.py        two-arm reconstruction statistics
scripts/pull_aact_results.py                 label pull  (needs the rewrite in §6.2)
scripts/validate_reconstruction.py           Step 1b validator
scripts/audit_label_gaps.py                  offline gap audit, reads the raw dumps
scripts/check_aact_connection.py             layer-by-layer AACT connection diagnosis
scripts/inspect_data_dir.py                  read-only data/ inventory
scripts/probe_ctgov.py                       CT.gov v2 API probe (parked, see §8)
scripts/run_checks.py                        the gate
tests/test_endpoint_label.py                 64 tests
tests/test_recon_stats.py                    32 tests
docs/EndpointLabelSpec.md                    readable version of the label spec
```

**Gate is GREEN at 96 tests** after the archive move (verified, not assumed: 64 + 32, and
no surviving script imports anything archived). If you install these files and see a
different count, something did not land.

`config.yaml` and `README.md` were NOT archived and are **stale** — they still describe
Gen 1. They need rewriting in place, not moving. `config.yaml` in particular still has
`aact: enabled: false`, which is backwards now that AACT is the primary source.

---

## 3. Decisions made this session

### 3.1 The TOP × ChEMBL cohort restriction is DROPPED

The 5,128-trial cohort was never a requirement of the endpoint-met label —
`endpoint_label.py` reads only AACT's own `outcomes` / `outcome_analyses` tables. The
restriction was a Gen 1/2 leftover: ChEMBL matching was needed for the approval label, TOP
for the fingerprint features. Neither applies.

**The new training population is every interventional trial in AACT.**

This directly attacks the project's central bottleneck. The prior handoff established that
the headline negative class was 369 trials, unmoved across four label revisions, and that
growing it required "either the reconstruction (Step 1b) or leaving the TOP × ChEMBL
cohort." This is that second option.

### 3.2 Consequence: the Step 1 headline numbers are now provisional

Everything in §4 below was measured **inside the old cohort**. The label logic is
unchanged and still correct; the counts are not transportable. In particular:

**The "additive model is the deliverable" decision must be recounted, not quoted forward.**
Its justification was empirical and specific: ~369 headline negatives against ~40 features
is ~9 events per variable, below the 10–20 rule of thumb, so the additive restriction was
forced rather than chosen. If the wider pull grows that class substantially, the argument
that ruled out boosting weakens **on its own terms**. Do not re-decide this now; do
recount it after the new pull, and do not cite 369 as settled once its input has changed.

### 3.3 Consequence: label population and feature population come apart

Inside TOP × ChEMBL, "has a usable label" and "has a resolvable drug identity" were
roughly the same population. Across full AACT they are not. Expect many more trials with a
label than with a mechanism. Design the thin-input path in from the start rather than
retrofitting it.

### 3.4 FDAAA applicability is carried as a VARIABLE, never used as a filter

FDAAA (FDA Amendments Act 2007, tightened by the Final Rule effective January 2017) makes
results-posting mandatory only for certain trials. Everything else is voluntary. That is
what the 49.5% → 67.6% posting-rate shift "across the FDAAA eras" actually measures: not
sponsors becoming more forthcoming, but the legal scope changing.

ClinicalTrials.gov goes back to ~2000, so a full-population pull necessarily mixes:
- trials legally required to post that did not — the real selective-disclosure signal,
- trials never required to post at all — whose absence means nothing about bias.

Pooling those two flattens the exact signal the Step 2 audit exists to detect, and would
also skew a temporal split toward "recent = more data," which reads as a time trend in the
model when it is a policy trend. So: pull everything, carry applicability per row, and let
the audit separate "did not post because not required" from "did not post despite being
required."

### 3.5 Temporal split: completion-before / start-on-or-after, straddlers dropped

Chosen for leakage resistance, not for sample size.

- **train** = trials that COMPLETED before the boundary date. Their outcome was fully
  determined by then, whatever it was.
- **test** = trials that STARTED on or after the boundary. Nothing about them can have
  leaked backward.
- **straddlers** (started before, finished after) are DROPPED rather than forced either
  way. This costs sample size on purpose, so that a disappointing result later cannot be
  explained away as a split artifact.

**Do NOT split on `results_first_posted_date`.** It is on `LABEL_DERIVED_FIELDS` for a
reason: sponsors with bad results sometimes post late, so posting date correlates with the
outcome. Splitting on it would bake the posting-bias problem into the split itself.

**Within-train as-of discipline.** A training trial's features may use only information
that existed as of THAT TRIAL's own as-of point — not merely "before the global boundary."
A trial completing in 2015 cannot carry a mechanism prior computed from 2020 approvals.
This bug has already been caught once in this project (the MoA prior's `leak_future`
mode); it will need re-verifying when the mechanism/disease families are rebuilt.

### 3.6 FDA approval records: uses B and C, never A

Three distinct uses were considered. The rejected one matters as much as the accepted ones.

- **A — approval as evidence the endpoint was met. REJECTED.** This is Gen 1/2's label
  wearing a new hat. One approval can rest on two pivotal trials, or one trial plus an
  extension, or a surrogate endpoint under an accelerated pathway. A trial can miss its
  primary and the drug still be approved on other evidence (false positive); a trial can
  succeed and the program be shelved (false negative, and right-censored besides — not yet
  approved is not the same as failed). Note that this is NOT fixed by the approval document
  naming the exact indication. The indication detail is there. The problem is that the
  trial→approval relationship is many-to-one and the causal direction is wrong. Using A
  would grow the positive class by quietly changing what the label means.
- **B — approval as a terminal advancement event. ACCEPTED.** Advancement is already
  framed as multi-window lookahead with explicit competing-risk language. Approval is the
  last rung of that ladder; for a phase 3 trial "advanced" plausibly means "approved." The
  same censoring caveats already planned apply, with no new machinery.
- **C — approval as external validation, never a training input. ACCEPTED.** After the
  endpoint-met model is trained, check whether trials it calls "met" go on to be approved
  at a meaningfully higher rate than trials it calls "not met." A face-validity check on
  both label and model, reported alongside results, structurally unable to leak.

`fetch_chembl_labels.py` and `join_chembl.py` are in `archive/scripts/` but are **parked
for revival**, not dead: they already pull `max_phase_for_ind`, which is what B and C need.

---

## 4. Step 1: the label. DONE. (numbers are old-cohort, see §3.2)

`scripts\pull_aact_results.py` → `data\aact\trial_labels.csv`, from
`src\trial_pos\services\endpoint_label.py` (pure, test gate green).
Spec: `docs\EndpointLabelSpec.md`.

Four tiers, carried per row as `tier_min` / `tier_max` / `tier_mix` / `label_rule`:
A superiority p-value, B non-inferiority p-value (both HEADLINE), C interval-only (not
headline), D no decidable analysis (unknown). Two variants: `endpoint_met_strict`
(analyses only, headline) and `endpoint_met_broad` (adds futility stops as 0, efficacy
stops as 1; sensitivity only).

### Numbers on 5,128 trials (TOP × ChEMBL cohort) — HISTORICAL

| quantity | value |
| --- | --- |
| results posted | 2,986 (58.2%) |
| ≥1 primary outcome row | 2,986 (58.2%) |
| **≥1 primary ANALYSIS row** | **1,395 (27.2%)** ← the coverage cliff |
| strict labelled | 1,346 (26.2%), 65.9% positive |
| **headline (A/B) labelled** | **1,195 (23.3%), 69.1% positive** |
| broad labelled | 1,463 (28.5%), 60.8% positive |
| tier D | 3,782 (73.8%) |

### The finding that drove the design

The headline negative class was **369 trials, exactly, across four label revisions** —
not approximately; positives oscillated by 8 as trials moved between tiers A/B and C while
the negative count did not budge. Everything the keyword and parameter-type work recovered
landed in tier C or the broad variant, both non-headline.

Conclusion that still holds: **label-rule refinement cannot grow the headline negative
class.** Conclusion that is now being acted on: the two remaining routes were the
reconstruction and leaving the cohort — and §3.1 takes the second.

### Also settled by data, not argument (old cohort)

- Strict vs broad moves 117 trials. Worth keeping, not the rescue for thin coverage.
- Multi-endpoint ambiguity affects 70 of 1,346 trials (5.2%); any_met and all_met agree
  94.8% of the time. All four readings are carried; the choice barely matters.
- Tier D is **not** mostly single-arm: of 1,641 no-verdict trials, only 727 are
  single-arm. 884 had a comparator and posted no analysis. That is selective disclosure
  *inside* the results-posting population — a second bias layer beneath the one Step 2
  was built to measure.

---

## 5. Step 1b: reconstruction validation. CODED, NOT YET RUN CLEAN.

`scripts\validate_reconstruction.py` + `src\trial_pos\services\recon_stats.py`.

Question: can the 884 multi-arm no-analysis trials be labelled by recomputing the
comparison from posted group measurements? Decision was **validate on the overlap first** —
measure agreement against sponsor verdicts where both exist, then decide.

This is a question about METHOD AGREEMENT, not cohort breadth, so it runs on the data
already on disk and does not wait for the wider pull.

Run order: `--probe-only`, then without `--from-raw` (it must pull all four tables in one
session). Read output sections in order: **coverage → 2×2 and kappa → symmetry**. Raw
agreement is inflated by the 69% positive base rate (a constant "met" guess scores ~57%),
so kappa is the number to quote. One-directional disagreement is disqualifying even at
high agreement. `--include-gap` sizing is read LAST and only if the first three hold.

Last run, before the snapshot fix: 3,772 outcomes with measurements, 1,796 sponsor
verdicts, 1,050 reconstructed, **overlap 0** — see lesson 1 below. Gap sizing said 243
trials (14.8%) reachable under the strict two-arm rule; expect higher now that comparator
pairing exists.

Known open issue: **19,790 measurement rows excluded as strata against 3,772 kept.** Rows
with `category` or `classification` set are subgroups/timepoints, and comparing a subgroup
of one arm against the whole of another would be wrong. But most posted rows are strata,
and many outcomes may have no overall row. If coverage stays low, this is the next thing
to examine — and the fix is genuinely hard, because choosing which timepoint represents
the primary endpoint is a judgment the structured fields do not make.

---

## 6. Immediate next actions, in order

The old order (1b → Step 2 → advancement) had the posting-bias audit as a separate
expedition. Dropping the cohort merges it into the same pull.

### 6.0 Archive move + this document. DONE this session.
Cheap, and it stops the next session re-deriving the three-generation lineage from
scratch. `config.yaml` and `README.md` still need their in-place rewrites.

### 6.1 Run `validate_reconstruction.py` on the data already pulled.
Coded, data on disk, no dependency on anything else. Decide from coverage / kappa /
symmetry. Cheap go/no-go.

### 6.2 Re-point the label pull at full interventional AACT.
**This is a rewrite of the script's input path, not a parameter change.**
`pull_aact_results.py` currently takes its id list from `data/chembl/units.csv` and chunks
ids into SQL 1,500 at a time. At full-population scale that is hundreds of round trips
built around an id list that no longer has any reason to exist. Invert it: query AACT
directly with a `study_type` filter and let the database define the population. Carry
FDAAA applicability per row (§3.4).

Outputs: the new label population, and **the recount of the headline negative class**
(§3.2).

### 6.3 The posting-bias audit, as a READER of 6.2's output.
One script pulls and writes raw + label; a second reads it and reports coverage and bias
before anything downstream consumes it. Keeps to the thin-I/O and audit-first rules, and
lets the audit be re-run without re-pulling.

Still the project's credibility. The within-cohort preview showed posting rates of 21.8%
(phase 1) vs 69.4% (phase 3), 0% for withdrawn, and 49.5% → 67.6% across the FDAAA eras.
The labelled slice is phase-3 heavy, so a model trained on it will not transport to the
phase 1/2 questions users actually ask. This audit produces the applicability domain and
the reweighting.

### 6.4 Build the advancement label.
Derivable for all trials with no posting dependence, so it becomes the workhorse target.
Multiple lookahead windows (2/3/5 years), each with its own censoring exclusion; a trial
enters training for a window only if that window closed before the data cutoff. Competing
risk to state plainly: failure to advance can reflect portfolio deprioritisation rather
than a negative result. Futility stops (134 in the old cohort) are clean negatives here —
which is where the `why_stopped` work actually pays off, since it cannot touch the headline
endpoint-met slice. FDA approval enters here as use B (§3.6).

### 6.5 Then feature matrix, then model. Calibration is first-class, not an afterthought.
A stated "72%" is only honest if ~72% of the 72%-bucket trials succeed. Report reliability
curve + Brier + ECE next to AUROC/log-loss.

---

## 7. Hard-won lessons. Do not relearn these.

1. **AACT regenerates surrogate keys on every nightly rebuild.** Never join across
   snapshots on `outcomes.id`, `outcome_analyses.id`, `result_groups.id`. A dump from
   three days ago had `outcomes.id` in the 2626xxxxx range; the same outcomes today are
   2647xxxxx. Both well-formed integers, zero overlap on 498 shared trials, no error
   raised. `validate_reconstruction.py` now pulls all four tables in one session for this
   reason. Any future cross-table work must do the same or join on natural keys
   (`nct_id` + outcome title).
2. **Negation handling: delete the negated span; do not veto the field and do not split
   into clauses.** A document-wide veto discarded genuine futility ("Lack of efficacy of
   the drug; no safety concern"). Clause-splitting on "and" tore "No safety and/or
   efficacy concerns" apart and the orphaned word misfiled five business terminations as
   safety. Span removal, with guards running to the end of the sentence, handles both.
3. **Contrast detection must precede single-arm refusal** in `null_value_for`. The
   reverse order blocked "Difference in Percentage", "Proportion difference",
   "Geometric Mean Ratio" and cost 95 analysis rows.
4. **No hardcoded reference values in tests.** Every expected number is derived: closed
   forms (Cauchy at df=1, the df=2 form, the arcsine identity), an independent Simpson
   quadrature that shares no code with the continued-fraction betainc, structural
   properties, and a scipy sweep that skips when scipy is absent. Three transcribed
   constants were wrong on first write; a constant that matches a bug certifies the bug.
5. **`pandas` turns empty CSV cells into float NaN, which is truthy.** It once made every
   missing results-posting date read as posted. `_scalar()` guards this.
6. **A sponsor's efficacy disclaimer should be believed.** "Business Decision; No Safety
   Or Efficacy Concerns" appears as near-identical boilerplate across 8 trials. Taking it
   at face value is the conservative choice.
7. **`pip install -e .`** — `pyproject.toml` already has `where = ["src"]`. Avoids the
   `PYTHONPATH` dance. The three newest scripts also self-bootstrap `src/`.
8. **NEW: approval is not an endpoint verdict, and indication detail does not fix that.**
   See §3.6 use A. The temptation will recur every time the negative class looks thin.
9. **NEW: a cohort restriction inherited from a dead design will quietly cap the whole
   project.** TOP × ChEMBL was load-bearing for Gen 1/2 and pure cost for Gen 3, and it
   sat unexamined long enough to become the stated reason for a model-class decision
   (§3.2). When a constraint is cited as settled, check which generation it belongs to.

---

## 8. Parked, deliberately — raise again when it blocks something

- **Mechanism and disease-area attribution families.** `archive/.../moa_encoding.py`,
  `disease_ta.py`, `disease_type.py` compute exactly these, but for Gen 2: keyed on
  drug-indication units rather than NCT ids, targeting ChEMBL approval rather than
  endpoint-met, and depending on a Trialtrove-152 disease-type label that plain AACT
  trials do not carry. Fork: salvage and re-key/re-target, or build fresh from AACT's own
  `browse_conditions` / `browse_interventions` MeSH fields. **Better decided after 6.2,**
  when we know how many trials across the full population resolve to a drug identity at
  all.
- **The advancement label's linkage key.** Linking a trial to what came next needs a
  (drug, indication) notion. That is `archive/scripts/build_units.py`'s rollup key — so
  Gen 2 is not entirely dead here either. Decide explicitly rather than defaulting in.
- **`probe_ctgov.py`.** Probes the live CT.gov v2 API — the fetch path the live tool
  eventually needs, written during Gen 2. Kept out of the archive because it is
  forward-looking, but it is not on the critical path until the model exists.
- **`pyproject.toml`** still lists `rdkit` and `chembl_webresource_client`. Harmless; trim
  when convenient. Note the ChEMBL client comes back for §3.6 B/C.
- **`max_phase == 4` meaning "approved"** is an unnamed magic number in the archived
  `join_chembl.py`, `label_indication.py`, `build_units.py`. Give it a shared constant if
  any of those is revived.

---

## 9. Standing discipline

- One smoke-tested script per step; pure logic in `src/` with tests, scripts as thin I/O
  and audit printouts only. No derivations inside a script.
- `python scripts\run_checks.py` must stay green. Currently **96** (was 131 before the
  archive move removed 35 Gen 1/2 tests).
- **No hardcoded numeric reference values in tests.** Derive every expected value from a
  closed form, an independent computation, or the constant it depends on.
- **No magic numbers in code.** Name them, or expose them as CLI flags, with the reason
  written next to them. Every threshold that produces a verdict must be a flag, and the
  script must print which value it used.
- **Audit-first.** A new script reports coverage and bias before anything downstream
  consumes its output. Distinguish "unknown" from "zero" everywhere.
- R7: `why_stopped`, `overall_status`, `why_stopped_class`, `termination_score`,
  `safety_termination`, `results_first_posted_date`, `results_first_submitted_date`,
  `results_posted` are LABEL INPUTS and must never be features.
  `endpoint_label.LABEL_DERIVED_FIELDS` enumerates them. **This is currently only
  printed, never asserted** — there is no feature builder yet to assert against it. Build
  the assertion as code when Step 6.5 starts; do not rely on memory.
- Unit-level MoA priors use `unit_year = max(phaseendyear)`. Attaching that to an earlier
  trial in the same unit leaks the future. Recompute the LOO cut at each index trial's own
  readout year before using the prior in a per-trial model. See also §3.5.
- Env: Windows/PowerShell, user runs scripts and pastes output, assistant writes code,
  data local. AACT Postgres credentials in `AACT_USER` / `AACT_PASSWORD`;
  `scripts\check_aact_connection.py` diagnoses failures layer by layer.