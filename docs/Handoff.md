# Handoff: clinical-trial success predictor — rev 6

Supersedes rev 5, written in the same session. Rev 5 is superseded rather than amended
because the tier-C interval rule turned out to be wrong in THREE independent ways, not
one, and the third of them adds a tier.

**`docs/Handoff.md` in the repo was still rev 2 when rev 4 was written.** A session reading
the repo instead of the pasted document got the dead events-per-variable argument as live
guidance — lesson 11's failure mode with a shorter fuse. This file is the current revision
and must be updated IN PLACE from here on.

---

## 0. What changed, rev 4 -> rev 5 -> rev 6

| | rev 4 | rev 5 | **rev 6** |
| --- | --- | --- | --- |
| scoping (§5) | deferred | drug trials only | drug trials only |
| `genetic` / `combination_product` | unnoticed | flagged separately | flagged separately |
| tier-C ratio scale | unmeasured | refused | refused |
| tier-C **CI coverage** | unread | unread | **one-way implication rule** |
| tier-C **NI / equivalence** | unnoticed | unnoticed | **TIER E, no verdict** |
| strict labelled | 23,024 | 22,545 | **21,412** |
| headline (A/B) | 19,938 (7,530 neg) | 19,958 (7,538 neg) | **20,043 (7,568 neg)** |
| drug-only headline | — | 14,293 (5,546 neg) | **14,368 (5,571 neg)** |
| tier C | 3,086 | 2,607 | **1,369** |
| model architecture | one model | three models | three models |
| gate | 200 | 240 | **425** |

**The three bugs are one family, and the family has a name (lesson 22):** a decision rule
was being applied without checking a parameter that was sitting in the data. The null
depends on the ratio's SCALE; the interval test depends on its COVERAGE; and which test
applies at all depends on the DESIGN. Each parameter was in the dump. None was read.

## 1. The goal

**A live tool, long-term.** Anyone pastes any clinical trial id and gets calibrated
probabilities of success with the explicit factors driving each number.

**Validated retrospectively** before going live: trained and tested on historical AACT
trials whose outcomes are known, with a temporal split, so the performance claim is a
back-test rather than an assertion.

### 1.1 THREE OUTPUTS, THREE MODELS. SETTLED.

All three are displayed, tracked separately, never combined:

| # | target | status | coverage | meaning |
| --- | --- | --- | --- | --- |
| 1 | **endpoint met** | **BUILT** (§3) | 4.6% (21,412 of 460,569) | this trial's own posted primary analysis cleared its threshold |
| 2 | **advancement** | planned, not built (§8.3) | near-full, no posting dependence | the program moved to the next phase |
| 3 | **market** | not started (§1.2) | gated on drug resolution + horizon | the drug reached market **for this trial's indication** |

**Three separate models, not one model with three outputs.** This is forced by the data
rather than chosen: three targets with 5% to 100% coverage and three different
applicability domains cannot share a training population. Three consequences that must be
handled rather than discovered:

1. **One feature builder, a PER-TARGET leakage registry.** All three compute features
   through one module so a feature means the same thing everywhere and the R7 assertion
   runs once. But `LABEL_DERIVED_FIELDS` cannot stay a single flat tuple: `why_stopped`
   feeds the endpoint-met broad label AND feeds advancement (futility stops are clean
   negatives there), while for market it is an ordinary feature. There is no cross-model
   leak — leakage is within a model — but the three numbers a user sees will have been
   built under three different exclusion lists, and that must be written down rather than
   inferred later.
2. **The temporal split boundary probably cannot be shared.** Market at W=10 needs
   readouts on or before 2016, so one early boundary leaves that model almost no test set
   while a late one leaks for the others. Per-target boundaries are the honest choice, and
   the consequence is that **the three models' performance numbers are not comparable to
   each other** — different populations, different eras, different split dates. If the
   product shows three probabilities side by side, that non-comparability is a stated
   property, not a footnote.
3. **Calibration reporting multiplies.** Three models, each with a reliability curve,
   Brier and ECE; market additionally with three windows. Name ONE headline per target in
   the spec in advance, variants reported beneath it, or the honest reporting becomes
   unreadable.

**Accepted consequence: different trials get different numbers of answers.** Coverage
ranges from 5% to near-total, and market is additionally gated on whether the drug can be
identified at all and on device exclusion (§1.2). Some trials will show three
probabilities, some one. This makes "thin inputs still get an answer, flagged heavily" a
STRUCTURAL property of the output rather than an edge case.

The owner's direction: **flag it and make it understood, but realistically.** Not a wall
of caveats on every number. A missing target should read as a plain statement of why — "no
market estimate: this is a device trial", "no endpoint estimate: no analysis was posted",
"no endpoint estimate: this is a bioequivalence study, which tests formulation sameness
rather than efficacy" — rather than a blank, an error, or a hedged paragraph.
Distinguishing "unknown" from "not applicable" is already the pipeline's discipline (§10);
`endpoint_label.NA_REASON_DOC` is where the endpoint-met side of that now lives, and the
other two targets need the same.

### 1.2 THE MARKET TARGET

**Definition: FDA approval, for the trial's indication.** Specifically domestic, by
decision. `market_global_any` is retained only as a comparator so the cost of
domestic-only is measured rather than assumed.

**Devices are OUT OF SCOPE. Decided.** Market is defined on FDA DRUG approval. Device
trials have separate pathways (PMA, 510(k)) and a separate data source. A device trial
simply receives no market number, and the output must say so rather than showing a blank.

#### 1.2.1 The data source is DrugCentral, not raw label parsing

An earlier draft claimed indication matching would require parsing FDA label prose and
called it the hardest work in the project. **That was wrong, and the project owner was
right to push back.** A codified source exists:

- **DrugCentral** (`drugcentral.org/download`) focuses on drugs approved for human use,
  carries regulatory approval information, and maps indications, contra-indications and
  off-label indications to **SNOMED-CT and UMLS** concept identifiers. It also holds the
  full text of FDA labels. It ships as a **Postgres dump**, with a public read-only
  instance — the same shape as the AACT setup, so it fits the existing pattern.
- **repoDB** already built something very close to this project's market label: approved
  indications from DrugCentral, failed indications from AACT, with **AACT's indications
  annotated in MeSH, which is a subset of UMLS**.

**That MeSH-is-a-subset-of-UMLS fact is what collapses the hard problem.** AACT
`browse_conditions` carries MeSH; DrugCentral carries UMLS/SNOMED. Indication matching is
therefore **code-to-code, not text-to-text**.

**A partial crosswalk is ALREADY ON DISK and rev 4 did not know it.**
`data/ontology/mondo_xref.csv`, built by the archived `build_mondo_edges.py`, carries both
`mesh` and `umls` columns: 32,102 MONDO rows, 8,004 with a MeSH id, 21,658 with a UMLS id,
and **7,756 with both**. That is the code-to-code bridge, already built. It is also the
CEILING of that route, and its overlap with AACT's actual condition distribution is
unmeasurable until `browse_conditions` is pulled (§8.1b). Measure it; do not assume it.

**Caveats that must be measured, not assumed:**
1. **Mixed provenance across an era boundary.** DrugCentral's pre-2012 indications came
   from OMOP vocabulary v4.4; after OMOP became OHDSI that data moved to a subscription
   license, so post-2012 indications were instead extracted from labels and mapped to
   SNOMED-CT/UMLS. Two different derivations either side of 2012 — structurally the same
   hazard as the FDAAA era split, and it must be checked before pooling.
2. **Partial mapping coverage.** At publication ~67% of remaining OMOP concepts had been
   mapped to SNOMED-CT/UMLS.
3. **Mined, not curated.** A published comparison found that 40% of indications sampled
   from DrugCentral could not be accounted for in a manually curated reference set. A
   market label built on DrugCentral inherits that error rate. **Do not treat DrugCentral
   as ground truth** — measure agreement on a hand-checked sample and report it beside the
   model. `src/trial_pos/services/agreement.py` now exists for exactly this and is the
   same module Step 1b and the interval-rule audit use.
4. All of the above is from literature, not from inspecting the current dump. **Verify
   against the actual download before building on it.** `scripts/probe_drugcentral.py` is
   the audit-first probe and is not yet written (§8.1c).

**`data/chembl/drug_indication.csv` must NOT be reused as-is.** It has 60,055 rows with
`mesh_id`, `efo_id` and `max_phase_for_ind`, which looks like a ready-made
`market_global_any` — but it covers only 10,383 molecules because it was built under the
Gen-1 TOP × ChEMBL cohort. Reusing it would silently re-import the cohort cap that
lesson 11 is about.

#### 1.2.2 Three columns — and `market_fda_any` is a comparator, not a gate

| variant | label source | role |
| --- | --- | --- |
| `market_fda_any` | Drugs@FDA / DrugCentral, drug-name match only | comparator: measures what indication-specificity costs. Also a strict UPPER BOUND on the target. |
| `market_fda_indication` | DrugCentral indication codes × AACT MeSH | **the target** |
| `market_global_any` | ChEMBL `max_phase == 4` | comparator: measures what domestic-only costs |

Rev 4 framed `market_fda_any` as a feasibility gate producing one number N. **That framing
was wrong and was corrected in discussion.** A single pooled N collapses three rates that
fail for unrelated reasons:

- **drug resolution** — can this trial's intervention be mapped to a drug entity at all?
- **approval-any** — of resolved drugs, how many have any FDA approval?
- **indication match** — of those, how many approvals match this trial's condition?

A low approval-any rate is **not** a feasibility problem. Most investigational drugs never
get approved; a 15% approval rate is a 15% positive rate, which is an ordinary
classification problem, not a dead target. Reporting it inside one pooled N would make a
perfectly workable base rate read as a gap. The rates that can kill target 3 are the
**first and the third** — resolution, because it is a name-parsing ceiling, and indication
match, because it is the expensive part.

The three variants are better understood as one 2×2 with three cheap corners and one
expensive one:

| | any indication | this indication |
| --- | --- | --- |
| **FDA** | `market_fda_any` | `market_fda_indication` ← the target |
| **global** | `market_global_any` | — |

**THE GATE, STATED BEFORE THE NUMBER ARRIVES:** the decision threshold is the **drug
resolution rate**, not an absolute count. Below roughly **50%** of drug trials resolving to
a drug entity, the indication work is not worth starting, because every downstream rate
multiplies against it. This is the assistant's recommendation and **the owner has not yet
ratified the 50% figure — do that before §8.1c runs, because a number that arrives without
a criterion gets rationalised into acceptability.**

**ChEMBL cannot answer the target.** `max_phase_for_ind` is a global maximum phase across
all regulators and is not FDA-specific. It supplies `market_global_any` only.

#### 1.2.3 Censoring: why non-approved is not the same as zero

A trial reading out in 2024 whose drug has no approval yet must NOT be labelled 0. That
records "has not happened yet" as "will never happen", and the error **correlates with
time**: every recent trial gets a 0 while every old trial had time to earn its 1. A model
would learn "recent means failure" — an artifact of the snapshot date — and it would learn
it easily, because the feature set is full of era proxies (the FDAAA declaration fields
only exist post-2017, phase coding changed, the sponsor mix shifted). The result would be
respectable metrics on a model that has partly learned to read a calendar.

Dropping non-approved trials instead leaves every remaining row a positive: no negative
class, nothing to learn.

**Mechanism.** For a window W, a trial is ELIGIBLE only if its window has closed — readout
date + W <= snapshot date. Eligible trials get 1 if approved within W of readout, 0 if not.
**Trials whose window has not closed are EXCLUDED from that window's training set, never
labelled 0.** The exclusion is the whole point: it guarantees every trial in the training
set had the same amount of time to succeed, which is what makes a 0 mean anything.

**DECISION CHANGED. The market target is PIVOTAL PHASES, W=3.** Rev 5 had W=10 across
all phases with W=3 as a diagnostic. The owner proposed pivotal-only with a 3-year window,
and that is right for a reason rev 5 missed: **the window and the phase restriction are not
independent choices.** Rev 5's objection to W=3 — that most 0s would be "still in
development", so the label measures program position rather than reaching market — is
correct over ALL phases and dissolves under the restriction. A phase 3 trial IS the last
rung before filing; readout to approval inside 3 years is the normal path for a successful
pivotal study, not a fast outlier.

It is also not a sample-size sacrifice. Measured on the live labels, drug trials with an
ACTUAL primary completion and the window closed against a 2026-09-20 snapshot:

| window | market-eligible (pivotal) |
| --- | --- |
| **3y** | **25,332** |
| 5y | 22,589 |
| 10y | 15,813 |

W=3 is **60% larger than W=10** on the same population, because it stops discarding seven
years of readouts. And the transportability prize is the real one: W=3 admits readouts
through 2023, so checkpoint inhibitors, cell and gene therapy and heavy accelerated-approval
use sit INSIDE the training window rather than outside it. That was the objection to W=10
that no amount of data could fix.

**W=3 is the headline, W=5 the sensitivity check, W=10 a diagnostic.** 3 versus 5 on one
pivotal population is a like-for-like comparison and is where the owner's original "the
horizon doesn't mean much" intuition finally gets tested properly; 3 versus 10 across
different populations never was one.

**The target is named for its population: `market_fda_indication_phase3_w3`.** So nobody
later reads a phase 1 number off a model that was never fit for it. A phase 1 trial's
output line is "no market estimate: calibrated for pivotal-phase trials only", which is
§1.1's pattern. Earlier-phase market prediction does not disappear; it becomes a DIFFERENT
target with its own window, decided on evidence later.

**The redundancy worry did not survive the data.** The concern was that a pivotal-phase
market label would collapse into endpoint-met — a drug reaching phase 3 has survived two
phases, so approval risk might be dominated by whether the pivotal trial hit. Measured:
**only 5,768 of the 25,332 market-eligible trials (22.8%) carry a headline endpoint-met
label.** For the other 19,564 the market number would be the ONLY output the tool can
produce. Complementary, not redundant, and this is the strongest argument for building
target 3 anywhere in this document. The 22.8% overlap becomes the §4.5 use-C external
validation set, never a training input.

**PHASE 4 IS OUT of market and advancement, and KEPT for endpoint-met.** The owner's
reasoning, which generalises: a phase 4 trial's drug is already marketed, so
`market_fda_any` is 1 by construction and advancement has no next rung. 30,899 of the
221,887 drug trials are phase 4 — a stratum that size at a ~100% positive rate would
dominate the base rate and let a model score well by learning "is this phase 4" from the
phase column. **A stratum whose label an available feature determines must be excluded, or
the model learns the feature instead of the question.** Same family as a decision rule that
answers the same thing on every row (§3.1 bug 1), one level up.

One refinement: under `market_fda_indication` a phase 4 label-expansion study is not
trivially 1, since the new indication can be refused. But it reaches market through a
SUPPLEMENTAL approval — a different evidence bar and process from the original NDA/BLA — so
pooling it would put two regulatory mechanisms under one label. Out for both reasons, and
the flag is retained so the decision is reversible without a re-pull.

Whether a phase 4 trial cleared its OWN primary analysis is a real question, so endpoint-met
keeps it. Eligibility is therefore PER TARGET, which is what
`src/trial_pos/services/eligibility.py` exists for.

**STILL ASSERTED, NOT MEASURED: the 3-year clock itself.** The filing-to-approval reasoning
above is from literature. It becomes measurable the moment DrugCentral approval dates land:
the distribution of readout-to-approval lag for pivotal trials whose drug was approved. If
the 90th percentile sits under 3 years, W=3 is vindicated on evidence; if it is 5, most
successful programs get labelled 0 and the window is wrong. **That check should gate the
decision rather than follow it.**

**A DEFINITIONAL GAP that would silently produce a great-looking model.** "Approved within
W of readout" as written admits approvals that PRECEDED the readout. A phase 3 trial run
post-approval for label expansion may qualify, and every phase 4 trial does. Labelling
those 1 records a fact that existed before the trial reported — not leakage in the
temporal-split sense, but a target that partly encodes its own answer. The window needs a
FLOOR as well as a ceiling: approval strictly after readout, or at minimum a flag for
prior approvals so they can be counted and excluded.

**Two specifics for this dataset:**
- **Anchor on `primary_completion_date`, and only when `primary_completion_date_type` is
  ACTUAL.** A clock cannot start from a plan. Window arithmetic is calendar-year addition
  on the date, not a nominal day count, so a leap year cannot move a trial across the
  boundary; a 29 February readout falls back to 28 February, which closes the window a day
  EARLY and therefore never admits a trial short of its full term.
- **A drug approved 12 years after readout is a 0 under W=3.** Correct for the question
  "within 3 years", but the label is WINDOW-SPECIFIC rather than a truth about the drug,
  and the user-facing number must say so.

**Competing risk, stated plainly:** failure to reach market can reflect portfolio
deprioritisation, a licensing handoff, or a company folding — none of which is evidence the
drug did not work. Same caveat as advancement (§8.3), and it must reach the USER, not just
this document.

**Note on §4.5.** Rejecting approval as evidence that AN ENDPOINT WAS MET stands unchanged.
Using approval as its own openly-labelled target is a different claim and is sound. The
distinction is that target 3 does not pretend to be target 1.

### 1.3 Still open
1. **Advancement's linkage key.** "Moved to the next phase" needs a (drug, indication)
   notion to link a trial to what came next. `archive/scripts/build_units.py` holds one.
   Better decided once drug-resolution coverage is measured (§8.1c).
2. **The censoring snapshot date.** Window eligibility needs a fixed "now". It must be a
   printed CLI flag, never `date.today()` (lesson 15). Default to the AACT snapshot date
   the re-pull lands on, printed, and stated in the spec.
3. **Ratification of the 50% resolution gate** (§1.2.2).
4. **Which multi-endpoint reading is the target** (§8.4).

---

## 2. Repo state

Three generations of code existed; two are in `archive/` (inventory and reasoning in
**`archive/docs/ArchiveReadMe.MD`** — rev 4 cited `archive/README.md`, which does not
exist). Gen 1 and Gen 2 both targeted ChEMBL drug-indication approval, which is a
downstream consequence of far more than one trial's result — a reference-class prior, not a
trial predictor. Gen 3 (this project) takes the label from the trial's own posted analysis.

**Live code:**

```
src/trial_pos/services/endpoint_label.py     the endpoint-met label engine
                                             (tiers A/B/C/E/D; scale, coverage and
                                              design refusals all live here)
src/trial_pos/services/agreement.py          2x2, raw agreement, Cohen's kappa   [NEW]
src/trial_pos/services/aact_aggregates.py    one-to-many source declarations + SQL [NEW]
src/trial_pos/services/eligibility.py        per-target eligible populations      [NEW]
src/trial_pos/services/posting_bias.py       disclosure ladder + stratified rates [NEW]
src/trial_pos/services/fdaaa.py              THE applicability rule, tri-state     [NEW]
src/trial_pos/services/recon_stats.py        two-arm reconstruction statistics
src/trial_pos/services/population.py         population scope, FDAAA carriage, dates, drug signals
src/trial_pos/services/resume.py             resume guards for the pull
scripts/pull_aact_results.py                 the label pull
scripts/validate_interval_rule.py            tier-C interval rule audit           [NEW]
scripts/audit_posting_bias.py                Step 6.3, slice one                  [NEW]
scripts/validate_reconstruction.py           Step 1b validator
scripts/audit_label_gaps.py                  offline gap audit
scripts/check_aact_connection.py             layered connection diagnosis
scripts/inspect_data_dir.py                  read-only data/ inventory
scripts/probe_ctgov.py                       CT.gov v2 API probe (parked)
scripts/run_checks.py                        the gate
tests/                                       test_endpoint_label (116), test_population (102),
                                             test_recon_stats (32), test_resume (21),
                                             test_agreement (20), test_aact_aggregates (23),
                                             test_eligibility (38), test_posting_bias (25),
                                             test_fdaaa (29)
                                             [test_endpoint_label 120, test_population 108]
docs/EndpointLabelSpec.md
```

**Gate: 425.** 200 at rev 4, 240 at rev 5; +20 agreement, +20 ratio-scale, +32 coverage / tier-E / refusal-accounting. If you install
these files and see a different count, something did not land.

`config.yaml` and `README.md` remain **stale Gen-1 artifacts** and need rewriting in place.
`config.yaml` still says `aact: enabled: false`.

**Correction to rev 4 §11:** `pyproject.toml` does NOT still list `rdkit` and
`chembl_webresource_client`. Its dependencies are pandas, numpy, scikit-learn, xgboost and
pyyaml. Reviving the ChEMBL client for §4.5 B/C means ADDING a dependency, not unparking
one.

---

## 3. THE HEADLINE RESULT — after all three refusals

Full-population pull, **460,569 interventional trials (100%)**, snapshot 2026-09-19.

| quantity | rev 4 | rev 5 | **rev 6** |
| --- | --- | --- | --- |
| results posted | 75,434 (16.4%) | same | same |
| ≥1 primary ANALYSIS row | 24,228 (5.3%) | same | same ← the coverage cliff |
| strict labelled | 23,024, 61.6% pos | 22,545, 60.7% pos | **21,412, 60.7% pos** |
| **strict headline (A/B)** | 19,938 | 19,958 | **20,043** |
| **strict headline NEGATIVES** | 7,530 | 7,538 | **7,568** |
| **DRUG-ONLY headline — THE POPULATION (§5)** | — | 14,293 | **14,368: 8,797 met / 5,571 not met, 61.2% pos** |
| tier_min A | 18,304 | — | **18,391** |
| tier_min B | 1,634 | — | **1,652** |
| tier_min C | 3,086 | 2,607 | **1,369** |
| tier_min D | 437,545 | 438,024 | **439,157** |
| `endpoint_na_reason` populated | — | 479 | **1,843** |

`endpoint_na_reason` breakdown: `ni_design_margin_unavailable` 1,552,
`percent_scaled_ratio_only` 200, `ci_coverage_mismatch_only` 91. Every one of those is a
trial that now gets a STATED sentence instead of a blank, which is §1.1's requirement.

### 3.1 Three bugs, one family

The tier-C interval rule labels a trial from a confidence interval when no p-value was
posted: met := the interval excludes the null. It had never been measured. It can be,
exactly, offline: analysis rows carrying **both** a decidable p-value and a full interval
are a labelled validation set, because the p-value is the sponsor's own verdict on the
same comparison and the label engine discards those intervals anyway under p-value
precedence. 25,683 such rows exist. Reproduce everything below with
`python scripts\validate_interval_rule.py`.

**All three bugs are the same mistake:** a decision rule applied without reading a
parameter that was in the dump.

#### Bug 1 — the null depends on the ratio's SCALE
`null_value_for` assigned null = 1 to bioequivalence ratios scaled by 100. The old
`_PERCENT_RATIO_RX` guard looked for a literal "%" or "percent"; AACT writes
`'Ratio of the T/R geometric mean x 100'`. An interval of [85.08, 104.21] cannot contain 1,
so every such row read as met.

Naming was not the fix. 480 rows name their scale; **1,868 rows across 357 trials say only
`'Geometric mean ratio'`**. Measured on the validation set, superiority/unstated rows only:

| stratum | n | raw | kappa | 2×2 (sponsor × interval) |
| --- | --- | --- | --- | --- |
| difference (null 0) | 16,088 | 96.7% | 0.934 | 7453 / 217 / 312 / 8106 |
| ratio, unit-scaled | 6,764 | 95.9% | 0.918 | 3517 / 118 / 159 / 2970 |
| ratio, percent-scaled | 296 | 78.4% | **0.000** | **0 / 64 / 0 / 232** |

Degenerate: the rule answers "met" on 296 of 296. Chance agreement equals raw agreement
exactly, which is the arithmetic signature of a constant. Disagreement is 100%
one-directional — disqualifying on Step 1b's own criterion. Re-centring on 100 does not
rescue it, because bioequivalence **inverts the hypothesis**: the rule is whether the
interval lies INSIDE 80–125%, demonstrating sameness. No null makes an exclusion test
answer that.

**Fix:** `contrast_family` (what the name can say) / `ratio_scale` (what only the numbers
can say) / `resolve_null_value` (composes them and refuses). `--ratio-scale-floor`
default 10.0, a printed flag.

**Floor sensitivity, checked:** at floor 25 the percent stratum stays degenerate and unit
kappa moves 0.849 → 0.851. The 91 rows that move have a 2×2 of 0 / 3 / 0 / 88 — they score
kappa 0.000 on their own, so raising the floor does not label them, it hides them in a
stratum where they agree by base rate. **But** `'Odds Ratio (OR)'` (76 rows) leaves the
refused list at floor 25, which means those are plausibly genuine odds ratios of 10–25
being refused wrongly. Cost of 10 over 25 is 59 trials. Kept at 10 because refusing costs
a label while mislabelling corrupts the target. **Parked idea, untested:** bioequivalence
intervals are tight around 100 (bound ratio ~1.22) while genuine large ratios are wide
(30–61 is ~2.03), so a bound-RATIO test would separate them where the floor cannot.

#### Bug 2 — the interval test depends on its COVERAGE
`analysis_ci_percent` is column 19 of the dump and the engine never read it. Splitting by
coverage band, superiority/unstated rows only:

| band | n | kappa | over-calls met | under-calls | ratio |
| --- | --- | --- | --- | --- | --- |
| matched (95%) | 20,889 | **0.944** | 194 | 391 | 0.50 |
| tighter (>95%) | 468 | 0.700 | **1** | 70 | **0.01** |
| looser (<95%) | 1,474 | 0.790 | 140 | **8** | **17.50** |

**The error direction flips across three orders of magnitude**, which is what makes this a
finding rather than noise, and it is the direction theory predicts: a 90% interval
excluding the null is two-sided p < 0.10 so it over-calls; a 97.5% interval is a higher bar
so it under-calls.

**Fix is NOT blanket refusal but a ONE-WAY IMPLICATION:**
- **matched** → this IS the alpha test; both verdicts stand.
- **tighter** → excluding the null at higher confidence implies a 95% interval would too,
  so **"met" is sound and "not met" is not**.
- **looser** → failing to exclude at lower confidence implies failing at 95%, so
  **"not met" is sound and "met" is not**.
- **unknown** → supports nothing. Assuming 95% would repeat bug 1's mistake.

That keeps 11,160 of 13,054 production rows where blanket refusal would have kept 9,151,
and the 1,894 it drops are concentrated in unearned "met" (1,493 of them).

**A wrong first attempt, recorded:** my first implementation lumped coverage *equal* to
the requirement into the "tighter" branch, discarding 6,853 sound "not met" verdicts and
refusing 64% of production rows. Raw agreement rose while kappa fell — the signature of
selecting on your own output.

`required_ci_percent(alpha) = 100*(1-alpha)`, **derived and checked against the data, not
taken from convention.** `--coverage-tolerance` default 0.5 points, a printed flag, because
`ci_percent` carries 166 distinct values and a whitelist is not viable. `'0.95'` appears
30 times — a proportion entered as a percent, bug 1's error one level up — and is refused
rather than multiplied by 100 on an assumption. Blank `ci_percent` is 155,940 rows and is
**benign**: every blank row has no readable interval either, so it was already tier D.

**The alpha × coverage grid (section 4 of the audit) is honest but only decisive at 0.05**,
and that limitation is printed. The candidate side uses whatever interval the sponsor
posted and 95% dominates (20,889 rows against 66–1,134 for every other column), so the
relation is testable only in the ci=95 column, where alpha 0.05 wins at kappa 0.944 against
0.849 (a=0.10) and 0.790 (a=0.01). Off-diagonal peaks in thin columns — notably ci=97.5,
plausibly multiplicity-adjusted alpha splitting — are unexplained rather than contrary
evidence.

#### Bug 3 — WHICH TEST APPLIES depends on the DESIGN. This one adds a tier.
Splitting by `analysis_design`, matched coverage:

| design | n | kappa | under-calls | over-calls | ratio |
| --- | --- | --- | --- | --- | --- |
| superiority | 20,255 | **0.923** | 431 | 345 | 1.25 |
| unstated | 2,893 | **0.934** | 40 | 54 | 0.74 |
| **NI / equivalence** | 2,535 | **0.364** | **638** | 177 | **3.60** |

The rule asks the superiority question. A non-inferiority trial succeeds when the interval
lies inside the MARGIN, which routinely **includes** the null — so the rule under-calls it
heavily, the direction theory predicts. Stripping NI lifts superiority to 0.923 and
unstated to 0.934, so the refusal buys accuracy on what stays as well as correctness on
what leaves.

**Decision: TIER E, not refusal to tier D.** These are important studies and they rely on a
different test, so they get their own named tier, produce NO verdict, and are never pooled
with tier C. 1,552 trials carry `ni_design_margin_unavailable`.

**The margin is not recoverable today, and that was CHECKED rather than assumed.** Of
11,513 NI/equivalence primary analysis rows, 11,486 carry a description and 10,177 contain
*some* number — but only 2,031 (17.6%) contain the word "margin" at all and **1,877 (16.3%)
have a number adjacent to it**. The rest are alpha levels, power and sample sizes. My
earlier reading of "10,177 have numbers" as encouraging was wrong. So a text parse could
reach roughly a sixth of these rows and would itself need validating; tier E exists so that
sixth stays findable.

**Non-inferiority and equivalence are also different from each other**, and the finer split
shows it:

| detail | n | kappa | margin stated with a number |
| --- | --- | --- | --- |
| `non_inferiority` | 837 | 0.272 | 26.4% |
| `equivalence` | 735 | 0.564 | 6.7% |
| `ni_or_equivalence_unspecified` | 963 | 0.295 | 16.5% |

Equivalence is two-sided containment within ±margin; non-inferiority is a one-sided bound.
`analysis_design_detail` carries the distinction WITHOUT deciding anything on it —
`analysis_design` keeps its coarse 'ni' bucket so tier B's definition does not move as a
side effect. `'NON_INFERIORITY_OR_EQUIVALENCE'` names two tests and is resolved to neither.

#### What all three cost, and one thing that looks like a bug
Production impact: 1,820 trials touched by the NI refusal, 299 by coverage, 226 by scale;
2,319 in the union, of which 1,966 sat at tier C, 231 already at tier D (no change) and 122
in the headline (untouched — their labels came from p-values).

**Headline went UP while strict labelled went DOWN**, again. 23,024 → 21,412 strict but
19,938 → 20,043 headline. `tier_min` is the WEAKEST *counted* tier, so withdrawing a bogus
tier-C outcome from a trial whose other outcomes were A or B promotes that trial into the
headline. Correct, intended, and lesson 25.

**Two findings that fell out of the same audit:**
1. At matched coverage the superiority stratum still under-calls slightly (431 vs 345), so
   **tier C is conservative relative to A/B**. Pooling C with A/B would shift the positive
   rate down. Independent support for keeping C out of the headline.
2. The validation set is structurally **adjacent** to what it measures: those rows posted a
   p-value, production tier-C rows did not, and bioequivalence analyses in particular often
   post an interval with no p-value. A favourable number is a ceiling, never an estimate.
   Lesson 16, third occurrence.

### Arithmetic checks

Pre-fix identities that passed, recorded so they are not redone: tiers summed to strict
labelled (18,304+1,634+3,086 = 23,024); headline was exactly A+B (19,938); broad sources
summed to broad labelled (25,309); labelled + tier D = 460,569. Of 2,603 futility
classifications 2,244 entered the broad label, the other 359 already having an analysis
verdict, which correctly takes precedence.

**Post-fix these must be RE-CHECKED on the re-pull, not assumed to carry over** — the tier
distribution moved.

### THE EVENTS-PER-VARIABLE ARGUMENT IS DEAD. DO NOT CITE 369 AGAIN.

Rev 2 said: ~369 headline negatives against ~40 features is ~9 events per variable, below
the 10–20 rule of thumb, therefore the additive model is **forced** rather than chosen.

**Drug-only, after all three refusals, there are 5,571 headline negatives: ~139 events per variable.**
Scoping to drug trials does NOT resurrect the sample-size argument — that was checked
explicitly before §5 was decided, precisely because it was the obvious objection.

**This does not mean switch to boosting.** It means the reason changed. The additive
model's remaining justification is the **product requirement** — "the explicit factors
driving that number" — which is a design commitment, not a sample-size consequence. That is
a legitimate reason to keep it, and it must be stated as such rather than dressed up as
statistics. The boosting comparison is now genuinely informative: at this sample size, a
material gap is a real interpretability cost being chosen.

**5,571 is an upper bound.** It will shrink under the applicability domain and the temporal
split. Do not be surprised by half.

### Other numbers from the same run

- Multi-endpoint: any_met == all_met for 20,828 of 23,019 (90.5%); 2,191 differ. Mean
  `frac_primary_met` 0.567. **All four readings still carried, and the choice is now DUE** —
  see §8.4.
- `why_stopped`: none 411,786 / operational 15,402 / other 13,865 / business 6,530 /
  futility 2,599 / safety 1,246 / external_evidence 366 / benefit_risk 157 /
  efficacy_success 49.
- `results_posted` vs AACT's `were_results_reported`: **452,000/452,000 agreement.** Two
  independently derived definitions matching exactly — the check most worth having passed.
- Strict and broad headline were identical pre-fix (both 19,938), because broad's extra rows
  come from `why_stopped` and therefore sit at `tier_min = D`, which the A/B headline filter
  excludes by construction. Rev 2 predicted this and it is confirmed at 90× scale.
- Era (pooled, with fallback): pre_fdaaa 32,577 (7.2%) / fdaaa_801 117,593 (26.0%) /
  final_rule 297,742 (65.9%) / unknown 4,088 (0.9%). **Read with §6.1 in mind** — the
  implausibly high final_rule share is largely planned dates.

---

## 4. Decisions settled, with the reasoning

### 4.1 Cohort restriction DROPPED (rev 2, vindicated)
TOP × ChEMBL was a Gen-1/2 requirement and pure cost here. Population is defined **by
query** — every interventional trial, filtered through the tested predicate in
`population.py`, not by an id file. Interventional 460,569 (76.3%) of 603,488 studies;
observational 140,870; expanded_access 1,068; unknown 981. "Unknown" is `study_type` absent
or `N/A`, counted apart from observational because absent is not a claim.

### 4.2 FDAAA applicability is CARRIED, never decided in the pull
Applicability is a derivation whose inputs are partly sponsor-self-reported on a form
postdating the 2017 Final Rule. Deciding it inside a pull would bury a judgment where
nobody could audit it. `population.py` has a test that fails if an applicability function
appears in the pull path.

| component | true | false | unknown |
| --- | --- | --- | --- |
| is_fda_regulated_drug | 11.2% | 50.1% | 38.6% |
| is_fda_regulated_device | 3.6% | 57.8% | 38.6% |
| is_us_export | 3.0% | 10.3% | 86.7% |
| has_us_facility | 35.8% | 55.2% | 8.9% |
| has_expanded_access | 0.2% | 98.3% | 1.5% |

`has_us_facility` has the best coverage (8.9% unknown) because it derives from facility
records rather than a sponsor declaration — the only jurisdictional hook usable for
pre-2017 trials.

### 4.3 Temporal split: completion-before / start-on-or-after, straddlers dropped
- **train** = COMPLETED before the boundary (outcome determined by then)
- **test** = STARTED on or after the boundary (nothing can leak backward)
- **straddlers dropped**, deliberately costing sample size, so a disappointing result
  cannot be explained away as a split artefact.

**Never split on `results_first_posted_date`** — it is on `LABEL_DERIVED_FIELDS` because
sponsors with bad results post late, so posting date correlates with outcome.

**As-of discipline within train:** a training trial's features may use only information
existing as of THAT trial's own as-of point, not merely "before the global boundary". A 2015
trial cannot carry a mechanism prior computed from 2020 approvals. Already caught once (the
MoA prior's `leak_future` mode).

**Per §1.1, the boundary is now expected to DIFFER PER TARGET.** One shared boundary was the
rev-4 assumption and it does not survive W=10 market eligibility.

### 4.4 Era date fallback: KEPT, flagged per row
`primary_completion_date` → `completion_date` when the former is absent, recorded in
`era_date_source`. Median primary-to-overall gap is **1 day**, so the substitution is
usually a no-op. It recovered 10,383 trials (2.3%); pooled shift is pre_fdaaa 5.1%→7.2% and
unknown 3.2%→0.9%, concentrated in the era with no posting obligation, so it cannot inflate
the count of trials facing the tighter rule.

**Known blind spot:** the gap diagnostic is measured on rows where BOTH dates exist, which
by construction excludes the rows actually using the fallback (lesson 16).
`era_date_source` covers that — those rows stay separable, and their 91.7% pre_fdaaa
concentration is consistent with them being old trials.

### 4.5 FDA approval: uses B and C, never A
- **A — approval as evidence the endpoint was met. REJECTED.** Gen 1/2's label in new
  clothes. One approval can rest on two pivotal trials, or one plus an extension, or a
  surrogate endpoint under accelerated approval. **Not fixed by the approval document
  naming the indication** — the indication detail is there; the problem is that the
  trial→approval relation is many-to-one and the causal direction is wrong.
- **B — approval as a terminal advancement event. ACCEPTED.** Approval is the last rung of
  the advancement ladder; the censoring caveats apply unchanged.
- **C — approval as post-hoc external validation, never a training input. ACCEPTED.** Check
  whether trials the model calls "met" are approved at a higher rate. Structurally unable
  to leak.

`archive/scripts/fetch_chembl_labels.py` and `join_chembl.py` already pull
`max_phase_for_ind` and are **parked for revival** for B and C — but see §1.2.1 on the
cohort cap in the data they produced.

---

## 5. SCOPING: DECIDED. DRUG TRIALS ONLY.

**`phase` is explicitly not-applicable for 231,038 trials (51.1%).** Over half of
interventional ClinicalTrials.gov is device, behavioural, surgical or dietary. Those trials
have **no mechanism**, so they cannot carry the mechanism attribution the product promises.

**Decision: the endpoint-met model trains on drug trials only, defined by `is_drug_trial`
(intervention_type includes `drug` or `biological`).** Full stop.

The reasoning, and what would make it wrong:

- The product promises the factors behind each number, and the strongest planned factors
  are drug-specific. For a device trial the explanation would be assembled from generic
  fields (enrolment, phase, sponsor, duration) while the probability looks identical to the
  user, with nothing on screen saying so.
- **Sample size is not a counter-argument**, and this was checked rather than assumed:
  5,571 drug negatives against ~40 features is ~139 events per variable.
- The drug positive rate (61.2%) is *lower* than non-drug (64.8%), so dropping non-drug
  trials does not make the label easier — worth knowing, because the reverse would have
  been a reason for suspicion.
- **This would be wrong if** most pasted trial ids turn out to be device or behavioural.
  Then the headline should be the population users actually bring. Unresolved, and worth
  revisiting once there is usage.

**Implementation: a printed POPULATION FLAG, never a row deletion.** The 5,665 non-drug
headline trials stay in `trial_labels.csv` and stay re-derivable, so the decision can be
revisited without a re-pull.

### 5.1 `genetic` and `combination_product`: flagged separately, NOT folded in

The intervention_type vocabulary, measured (trial-level counts; types co-occur):

```
  201068 drug         92831 other        63998 device       60633 behavioral
   49131 procedure    28061 biological   17764 dietary_supplement
    9526 radiation     7395 diagnostic_test   3168 combination_product   1605 genetic
```

**3,629 trials carry `genetic` or `combination_product` with NO `drug` or `biological`**
(71 of them headline-labelled). Under `is_drug_trial` they read False and drop out of the
population entirely — yet cell and gene therapies receive BLAs, and they are exactly the
modality §1.2.3 says a W=10 window cannot see.

**Decision: `DRUG_INTERVENTION_TYPES` stays `{drug, biological}`.** `is_drug_trial` keeps
its one stated meaning. Two new tri-state signals travel beside it —
`has_advanced_therapy` (`genetic`) and `has_combination_product` — so the 3,629 stay
countable and re-scopable without changing the definition everything else rests on. Same
carry-don't-blend pattern as the three drug signals. They are candidates for INCLUSION in
the market population and are arguable for endpoint-met; decide with §8.1c's numbers in
hand.

### 5.2 The phase proxy is NOT usable, and why the totals lied

| pair | agree | disagree | agreement where comparable |
| --- | --- | --- | --- |
| is_drug_trial vs phase_is_drug_like | 410,685 | **49,759** | 89.2% |
| is_drug_trial vs is_fda_regulated_drug | 219,012 | 66,948 | 76.6% |
| phase_is_drug_like vs is_fda_regulated_drug | 225,916 | 60,043 | 79.0% |

Counts: `is_drug_trial` true 221,887 (48.2%) / false 238,682 (51.8%) / **unknown 0**.
`phase_is_drug_like` true 223,495 / false 236,949 / unknown 125.

89.2% sounds high but means **49,759 misclassified trials** — an error class seven times
larger than the entire labelled negative population. Note the near-identical totals
(221,887 vs 223,495) alongside 49,759 disagreements: the errors run roughly 25k in each
direction and cancel. A marginal comparison would have made the proxy look almost perfect.
Only the paired cross-tab exposes it (lesson 19).

**`unknown = 0` on `is_drug_trial`** means every one of the 460,569 trials has at least one
intervention row. Plausible, but a strong claim; if a future pull shows nonzero unknowns,
the subquery changed, not the registry.

---

## 6. THE DATE-TYPE COLUMNS

### 6.1 RESOLVED, and the hypothesis was confirmed
AACT carries `*_date_type` beside each date: **'Actual' or 'Estimated'**. Missing them
matters twice:

- For **era binning** it is a nuance — a planned date still says roughly when. It explains
  the implausible-looking 65.9% in `final_rule`: ongoing trials carry future-dated planned
  completions.
- For the **temporal split** it is decisive. "Train on trials that completed before the
  boundary" is meaningless if the completion has not happened.

`start_date_type`, `primary_completion_date_type`, `completion_date_type` are pulled and
carried, plus `era_date_type`. **Carried, never filtered in the pull** — the split filters.

Measured era × date_type:

| era | actual | planned or unknown |
| --- | --- | --- |
| pre_fdaaa | 27,726 | 4,851 |
| fdaaa_801 | 103,883 | 13,730 |
| final_rule | 166,500 | **139,791** |
| unknown | 0 | 4,088 |

Nearly half of `final_rule` rests on a planned date. By definition those trials carry no
endpoint label either. **Step 8.2 must not treat the era distribution as a population of
completed trials** — any posting-rate-by-era figure has to be computed on actual-dated rows,
or it divides disclosures by a denominator including trials with nothing to disclose yet.

Distribution as pulled: `primary_completion_date_type` actual 292,753 (63.6%) / estimated
153,288 (33.3%) / unknown 14,528 (3.2%). `completion_date_type` actual 281,291 (61.1%) /
estimated 163,158 (35.4%) / unknown 16,120 (3.5%). `start_date_type` actual 266,710 (57.9%)
/ estimated 46,241 (10.0%) / **unknown 147,618 (32.1%)**.

**That 32.1% unknown on `start_date_type` is a live problem for the split.** The test side
is "STARTED on or after the boundary", and for a third of the population it is unknown
whether the recorded start happened. `is_planned_date` returns None there rather than
guessing, so the split must decide explicitly: exclude them, or accept them on the grounds
that an unconfirmed start is still evidence of a start.

### 6.1a VOCABULARY BUG, found by the full run
AACT reports **'Estimated'**, not 'Anticipated' — 153,288 estimated primary completion
dates and **zero** anticipated. `DATE_TYPES` was built from older registry wording.

Nothing was mislabelled, because two design choices held: `normalize_date_type` passes an
unrecognised value through as itself instead of returning None, so 'estimated' appeared in
the audit as its own bucket; and `is_actual_date` is defined as "is actual" rather than "is
not planned", so an unknown vocabulary item can never be promoted into a completed event.

Fixed: `DATE_TYPE_ESTIMATED` is a named constant, `PLANNED_DATE_TYPES` groups it with
'anticipated' (retained for historical dumps), and `is_planned_date` gives the positive
statement the split needs. `is_planned_date` is deliberately NOT the negation of
`is_actual_date`.

**Outstanding:** `normalize_date_type`'s docstring still claims it returns
`'actual' | 'anticipated' | None`. It also returns `'estimated'` and passes unrecognised
values through. Cosmetic, but it is lesson 7's docstring-vs-behaviour drift in the module
whose whole purpose is that distinction. Fix in place.

### 6.1b Date plausibility: the bounds caught almost nothing, which is the finding
`implausible_past = 0` on all three date fields — the 1900 floor catches nothing, so
retrospective registrations are all within range. `implausible_future` is 6 / 38 / 73. The
31,777-day gap is real but comes from a handful of rows, one of those 73.

The actual data-quality problem is **490 negative gaps** (overall completion BEFORE primary
completion), which no absolute bound catches. Keep the bounds — a bound that catches nothing
still proves the population is clean — but the negative gaps are what §8.2 should exclude
or flag.

### 6.2 Date plausibility bounds (mechanism)
Bounds are flags, printed: `--min-trial-date` (default 1900-01-01), `--max-future-years`
(default 20). `--as-of` sets the reference date and **must be set explicitly**; unset, it
reads the clock and verdicts drift day to day. The pure function takes `as_of` as an
argument and never calls `date.today()`.

Implausible dates are **reported, not dropped** — dropping them would hide how much of the
population the era analysis cannot speak for.

### 6.3 One-to-many aggregates
`intervention_types` is pulled via `string_agg(DISTINCT ...)` inside the studies query
rather than as a fourth table. A plain join would multiply studies rows and quietly inflate
every count in the audit. If the columns are absent the aggregate disables itself with a
printed warning rather than failing mid-pull. **Every new one-to-many source in §8.1b
follows this pattern — subquery aggregate, never a join** (lesson 14).

---

## 7. Step 1b: reconstruction validation. CODED, NOT YET RUN CLEAN.

`scripts/validate_reconstruction.py` + `src/trial_pos/services/recon_stats.py`.

Question: can multi-arm no-analysis trials be labelled by recomputing the comparison from
posted group measurements? Decision: **validate on the overlap first.**

Run order: `--probe-only`, then without `--from-raw` (must pull all four tables in one
session). Read sections in order: **coverage → 2×2 and kappa → symmetry**. Raw agreement is
inflated by the positive base rate, so **kappa is the number to quote**. One-directional
disagreement is disqualifying even at high agreement. `--include-gap` sizing is read LAST.

Last run: **overlap 0** — the sponsor-verdict set carried `outcome_id` in the 2626xxxxx
range and the recon set 2647xxxxx, i.e. two different nightly rebuilds (lesson 1). Gap
sizing said 243 trials (14.8%) reachable under the strict two-arm rule.

**Kappa should now come from `agreement.py` rather than the inline computation at
`validate_reconstruction.py:352`** — that inline version was the §10 violation that prompted
the module, and two copies of a derivation will diverge.

Open issue: **19,790 measurement rows excluded as strata against 3,772 kept.** Rows with
`category` or `classification` set are subgroups/timepoints, and comparing a subgroup of one
arm against the whole of another would be wrong. But most posted rows are strata, and many
outcomes may have no overall row. Choosing which timepoint represents the primary endpoint
is a judgment the structured fields do not make.

**Note:** these counts predate the population widening. Not blocking.

---

## 8. Immediate next actions

### 8.1 DONE: the label fix and its audit
`endpoint_label.py` splits `contrast_family` / `ratio_scale` / `resolve_null_value`;
`agreement.py` is new; `scripts/validate_interval_rule.py` reproduces §3.1. Gate 200 → 240.

### 8.1b RUN. 460,569/460,569, and the sponsor split produced a finding.

The widened pull completed against the live server on the 2026-09-20 snapshot. Every
number predicted offline came back exactly: strict labelled **21,412**, headline
**20,043** (12,475 met / **7,568** not met), tier C **1,369**, tier A 18,391, tier B 1,652.
`were_results_reported` agreed with the independently derived `results_posted` flag on
**460,569 of 460,569**.

**The sponsor / responsible-party split was worth pulling.** They disagree substantially:

```
  coverage:  both 426,242 (92.5%)   lead_only 34,327 (7.5%)   party_only 0   neither 0
  lead=other      party=principal_investigator   157,793  34.3%
  lead=other      party=sponsor                  121,981  26.5%
  lead=industry   party=sponsor                  100,256  21.8%
  lead=other      party=sponsor_investigator      22,568   4.9%
  lead=other      party=unknown                   17,445   3.8%
```

A single `lead=other` bucket splits across three different responsible-party types, so
binning posting rate by lead sponsor alone would merge investigator-initiated trials with
institution-sponsored ones. **34,327 trials have a lead sponsor class and NO responsible
party recorded**, and none have the reverse — so lead sponsor is the more complete field
and responsible party is the more legally correct one. §8.2 must report both.

#### Two bugs in the first widened run, both self-inflicted

1. **ENTITY COVERAGE printed 0.0% for every source** while sponsor coverage reported 92.5%
   on the same page. `entity_coverage(records)` was reading the LABEL records, and the
   entity aggregates deliberately never land there — they go to the separate entity file.
   A diagnostic whose denominator excluded the thing it measured, which is lesson 16 for
   the fourth time and this one was written by the assistant rather than inherited.
   Coverage is now tallied per chunk with `merge_entity_coverage` and passed into `audit`,
   which keeps the streaming memory bound and makes addition a tested operation rather
   than an inline loop.
2. **`E_ni_interval` printed 0 trials** while the refusal counts three screens later said
   1,820. `tier_min` was derived only from COUNTED outcomes and tier E never produces a
   verdict, so a trial whose every primary analysis was a non-inferiority interval reported
   as `D_no_analysis` — indistinguishable from a single-arm descriptive posting, which is
   the exact opposite of why tier E exists. Fixed: when NOTHING was counted, tier_min and
   tier_max fall back to the weakest and strongest tier any outcome reached.
   **Live, tier E is 1,322 trials** and `D_no_analysis` fell by exactly 1,322, so the
   arithmetic closes. A trial with an A outcome and an E outcome still reports
   tier_min = A, because tier_min describes what the LABEL rests on.

   The assistant predicted 1,524 from an offline reconstruction and was wrong by 202. The
   reconstruction built its outcome map only from rows that HAD an analysis row, while the
   pull calls `by_trial[nct].setdefault(oid, [])` so a primary outcome with zero analyses
   still exists as an empty outcome. Those 202 trials have an NI-interval outcome AND a
   primary outcome with no analyses at all, so their weakest tier is D, not E. The live
   number is the correct one and the throwaway script was the thing that was wrong.

`endpoint_na_reason = ni_design_margin_unavailable` is 1,552 against 1,322 at
`tier_min = E`: the 230 extra trials also carry a tier-D outcome, so their tier_min is D
and their tier_max is E. The NA reason follows REFUSAL_KINDS precedence and reports the
NI cause, which is the more informative statement.

#### ENTITY COVERAGE, and why the headline figure was the wrong one to ask for

The fixed table reported, over all 460,569 trials:

```
  intervention_names        460,566  100.0%     condition_mesh_terms      362,677  78.7%
  intervention_other_names  139,614   30.3%     condition_mesh_ancestors  355,976  77.3%
  intervention_mesh_terms   243,618   52.9%     ANY drug-name source      460,566  100.0%
```

**The assistant framed "ANY drug-name source" as the first real evidence on whether the
market target survives. That was wrong.** It reads 100.0% and carries almost no
information: `intervention_names` is free text present on essentially every trial,
including "Placebo", "Standard of care" and "Exercise". Presence is not resolvability, and
a ceiling that admits everything bounds nothing.

Two things fix the measurement, both now in the code:

1. **Report the DRUG-TRIAL stratum.** Scoping is drug-only (§5), so a percentage over a
   population that is 51.8% device, behavioural, surgical and dietary answers a question
   nobody asked. `entity_coverage` now reports every figure twice, all trials and
   `is_drug_trial` true, with `unknown` excluded from the drug denominator per the
   tri-state rule.
2. **Count `both_mesh`, the INTERSECTION.** A curated drug MeSH term and a curated
   condition MeSH term on the SAME trial is the actual ceiling on code-to-code indication
   matching, and no downstream rate can exceed it. It cannot be inferred from the two
   marginals — two trials each carrying one side read 52.9% and 78.7% while the join
   reaches neither. That is lesson 19's shape for a third time, so it is counted rather
   than derived.

`intervention_mesh_terms` at 52.9% overall is the realistic proxy for "resolvable to a
drug entity", not the 100% figure. **The number the 50% resolution gate (§1.2.2) must be
read against is `BOTH MeSH sides` in the DRUG TRIALS column, and it is not yet measured.**
Three trials have an intervention row with a type but no name, which is why
`intervention_names` is 460,566 rather than 460,569 while `is_drug_trial` has zero
unknowns — consistent, and too small to matter.

### 8.1b(i) The pull itself

Rev 4 §8.1 claimed `trial_labels.csv` "holds every column Step 6.3 needs". **That was
false.** Six one-to-many sources are now pulled:

| source | columns | needed for |
| --- | --- | --- |
| `interventions` | `intervention_types`, `intervention_names` | scoping (§5) + drug resolution |
| `intervention_other_names` | `intervention_other_names` | synonyms and internal code names |
| `browse_interventions` | `intervention_mesh_terms` | curated drug vocabulary |
| `browse_conditions` | `condition_mesh_terms`, `condition_mesh_ancestors` | **the MeSH side of the code-to-code indication join** |
| `sponsors` | `lead_sponsor_class`, `collaborator_classes`, `lead_sponsor_name` | posting rate by sponsor class (§8.2) |
| `responsible_parties` | `responsible_party_type`, `responsible_party_affiliation` | the same, per statute |

**All three drug-name sources are pulled and resolution rate is reported per source.**
Picking one and discovering later it was the weak source is the expensive mistake.

**`browse_conditions.mesh_type` separates indexed terms from tree ancestors.** Pooling them
would make every oncology trial look like it studied "Neoplasms", which is useless for
indication matching.

**Both sponsor entities are carried, compared PAIRED.** Convention bins posting rate by
lead sponsor; the FDAAA obligation falls on the responsible party. `sponsor_agreement`
returns the joint distribution, not two summaries, because lesson 19 happened here once
already.

#### The aggregates are DECLARED, and lesson 14 is now enforced by tests
`src/trial_pos/services/aact_aggregates.py` holds the six sources as data and generates
the SQL from the declaration. Before this, "aggregate a one-to-many table in a subquery,
never a plain join" lived in a comment beside one hand-written subquery — and a rule kept
by a comment does not survive six copies. `tests/test_aact_aggregates.py` asserts it
structurally for every source including ones added later: every source groups by nct_id,
every join is LEFT, every subquery is scoped to the id block, every aggregate is DISTINCT,
aliases and output names are unique, and a disabled source contributes NO SQL fragment
rather than an empty one. The probe's table list is derived from the declaration too, so a
source added and forgotten cannot probe as absent and be silently disabled.

**Graceful disable per source.** A table or column the probe cannot find disables only its
own source, with the missing columns, the affected output columns and the reason printed.
The dependent fields then read unknown everywhere, which is weaker but honest.

#### The entity aggregates go to a SEPARATE file
`data/aact/trial_entities.csv`, keyed on nct_id (`--entities`). Two reasons: the label
file's schema stays stable for §8.2 while market matching iterates, and several hundred MB
of free-text aggregates stay out of a file every downstream step reads. It joins back on a
natural key, the only kind safe against AACT's nightly surrogate-key regeneration.

#### New columns on the label file
`*MODALITY_SIGNALS` (`has_advanced_therapy`, `has_combination_product`,
`is_drug_like_modality`), `*SPONSOR_COLS`, `endpoint_na_reason`, the three refusal counts,
and `ratio_scale_floor_used` / `coverage_tolerance_used` / `required_ci_percent_used`
beside `alpha_used`. `--ratio-scale-floor` and `--coverage-tolerance` are printed flags,
and the `--from-raw` path takes the same two, because lesson 6's near-miss was exactly a
re-derive that silently disagreed with the pull it was meant to reproduce.

**`DRUG_INTERVENTION_TYPES` was NOT widened** to include `genetic` or
`combination_product`, and a test fails if a future edit does. `is_drug_trial` has one
stated meaning and the scoping decision, the agreement cross-tabs and the population count
all rest on it; the edge cases travel beside it instead (§5.1).

#### Verified against the server
The probe reported all six aggregates enabled and every required column present; only
`outcome_analyses.groups_desc` is absent, which was already known and parked. The pull
ran 231 chunks with no interrupt and matched 460,569/460,569.

Also recoverable WITHOUT a re-pull, because they are already in `results_raw_studies.csv`
but were dropped at write time: `enrollment_type`, `number_of_groups`,
`results_first_submitted_date`, `last_update_posted_date`, `registered_in_calendar_year`,
`number_of_facilities`.

**Run it from a standalone PowerShell window, not VS Code's integrated terminal.** Two runs
died to `KeyboardInterrupt` the operator never sent: VS Code's Python extension injects
`conda activate base` and sends Ctrl+C to clear the line first. Evidence: a `CondaError`
line right after both tracebacks, and two failures at different chunks but similar elapsed
time — an external timer, not a code bug.

To re-run: `--probe-only` first, then `--as-of` set EXPLICITLY. 15–30 minutes; `--resume`
exists and refuses unless the manifest's settings match.

### 8.1c THEN: drug resolution, as the market decision
Three rates, reported separately (§1.2.2): resolution, approval-any, indication match. Pair
with a DrugCentral schema probe — `scripts/probe_drugcentral.py`, same layered audit-first
shape as `check_aact_connection.py`, read-only, reporting schema presence, row counts on the
indication tables, the pre/post-2012 provenance split, how many indications carry UMLS vs
SNOMED vs neither, and the MeSH-reachable share through `data/ontology/mondo_xref.csv`.
**Before writing any join**, since §1.2.1's caveats come from literature rather than from
the current dump.

### 8.1d STEP 6.3 SLICE ONE IS RUN. The market gate clears, and matchability is era-graded.

`scripts/audit_posting_bias.py` + `src/trial_pos/services/eligibility.py`. Eligibility is
decided BEFORE any rate, because every posting-rate figure is a fraction and the three
targets do not share a denominator.

**Eligible populations, snapshot 2026-09-20, market window 3y:**

| target | eligible | ineligible | undeterminable |
| --- | --- | --- | --- |
| endpoint met | **14,368** | 446,201 | 0 |
| advancement | 95,926 | 284,666 | 79,977 |
| market (pivotal, W=3) | **25,332** | 396,888 | 38,349 |

Largest exclusions: non-drug 238,682 (permanent, a scoping decision); not-pivotal 123,918
for market; phase 4 30,899; no-actual-readout 14,270 for market and 55,898 for advancement
(UNDETERMINABLE, not ineligible); open window only 3,389 for market. **Read the reasons, not
the totals** — a scoping exclusion will never change while an open window shrinks every
year the snapshot advances.

**THE GATE NUMBER: 75.0% both-MeSH inside the market-eligible cohort** (19,000 of 25,332),
against 63.0% over all drug trials. The pre-registered threshold was 50% (§1.2.2), so on
its own stated terms **target 3 proceeds.** Every source is better in this cohort than in
the drug population at large — MeSH drug terms 84.8% vs 77.0%, condition terms 87.7% vs
80.3% — which makes sense: pivotal trials are larger, better documented and more likely to
have been indexed.

**BUT MATCHABILITY IS ERA-GRADED, AND THE WRONG WAY ROUND:**

| era | n | both-MeSH |
| --- | --- | --- |
| pre_fdaaa | 4,765 | **80.7%** |
| fdaaa_801 | 11,919 | 76.2% |
| final_rule | 8,648 | **70.2%** |

A 10.5-point decline with recency. The likely mechanism is indexing lag — NLM assigns MeSH
after registration, so recent trials have had less time — but the direction is what matters:
**the trials most like the ones a user will paste are the least matchable.** This is the
mirror image of the W=10 problem. W=3 fixed era transportability on the LABEL side; the
matchability side is graded in the opposite direction.

Two consequences that must be carried:
1. **The market applicability domain includes "has both MeSH sides", and recent trials fail
   it more often.** This is not only a training constraint: a 2026 trial pasted into the
   tool may have no indexed condition terms yet, so the model cannot FEATURISE it. That
   needs a §1.1 output line of its own — "no market estimate: this trial has no indexed
   condition terms yet" — distinct from the device and phase reasons.
2. **Recency forces the hard path for exactly the trials that matter most.** The fallback is
   free-text condition and intervention names, which is the text-to-text matching §1.2.1
   celebrated avoiding. If the tool must serve recent trials, some of that work returns;
   the honest alternative is to decline those trials a market number.

**Target 3 is COMPLEMENTARY, not a restatement.** Of the 25,332 market-eligible trials only
5,822 (23.0%) carry a headline endpoint-met label; **19,510 (77.0%) would get a market
number and nothing else.** That is the strongest argument for building it in this document,
and it inverts the assistant's expectation that pivotal-phase market would collapse into
endpoint-met. The 23.0% overlap is the §4.5 use-C validation set, never a training input.

**Phase composition of the 221,887 drug trials:** early 55.8%, pivotal 19.4%, post-approval
13.9%, unknown 10.9%. The pivotal restriction does nearly all the exclusion work, not the
window.

**A bug in the first version of the eligibility predicate**, worth recording because it is
the exact confusion the module exists to prevent: endpoint-met eligibility had no tier
condition, so it counted 1,136 tier-C drug trials and reported 15,504 against a headline
population of 14,368. Tier C is an interval-only verdict kept as a sensitivity stratum and
it under-calls positives relative to A/B, so pooling shifts the base rate. Fixed with
`DEFAULT_ENDPOINT_HEADLINE_ONLY`, a flag rather than a constant so the stratum stays
reportable, and the audit now prints it separately. The fixture that hid it omitted
`tier_min` entirely.

**NO REWEIGHTING, deliberately.** The applicability domain is reported and the model is
left speaking for the population it was fit on. Inverse-probability weights from a posting
model would let the endpoint-met model claim it transports while importing every bias in
the posting model unstated, and a weight that cannot be checked is worse than an honest
restriction. Slice one produces the INPUTS weights would need. Reweighting is its own step
with its own validation. **Owner has not objected; revisit if that changes.**

### 8.1e SLICE TWO IS RUN. THE LABELLED SLICE IS HEAVILY SELECTED, AND THE TWO SELECTION STEPS HAVE DIFFERENT DRIVERS.

`src/trial_pos/services/posting_bias.py` + section 6 of the audit. Measured on drug trials
whose ACTUAL primary completion is at least 12 months before the snapshot — the FDAAA
allowance — because a trial still inside its window has not failed to post, and counting it
as a non-poster would mix non-disclosure with not-yet-due, a mixture that is
ERA-CORRELATED and would manufacture the gradient the section exists to detect. Denominator
140,285 of 221,887 drug trials; 4,685 not yet due and 76,917 with no actual readout date.

**DISCLOSURE IS A THREE-STATE LADDER**, not a binary, because the label needs a posted
ANALYSIS and the gap between that and posting at all is the coverage cliff. Reporting the
two together would attribute the whole effect to whichever step is larger.

| stratifier | posting rate spread | analysis rate spread | CLIFF spread (conditional) |
| --- | --- | --- | --- |
| **phase** | **35.3%** (P1 16.9% → P3 52.2%) | **24.6%** (3.2% → 27.9%) | **41.9%** (11.5% → 53.4%) |
| **is_fda_regulated_drug** | **51.1%** (10.6% → 61.8%) | **15.5%** | 3.1% |
| **has_us_facility** | **40.9%** (17.1% → 58.0%) | **11.6%** | **10.7%** |
| has_expanded_access | **36.3%** | **22.7%** | **19.2%** (n=725 true) |
| era | **23.1%** (pre 20.5% → fdaaa 43.6%) | 6.1% | 7.9% |
| is_fda_regulated_device | **19.5%** | 0.9% | **10.1%** |
| is_us_export | **20.4%** | 7.4% | 7.2% |

**Every stratifier is notable on the posting rate. The endpoint-met slice is not a random
sample of drug trials and cannot be treated as one.** The handoff has asserted this since
rev 2; it is now measured.

**THE DECOMPOSITION IS THE FINDING.** The two steps are driven by different things:
- `is_fda_regulated_drug` moves the posting rate by 51.1 points and the CLIFF by 3.1.
  **Regulatory obligation decides whether you post at all, and has almost no effect on
  whether statistics accompany it.**
- `phase` moves the posting rate by 35.3 points and the CLIFF by **41.9** — the largest
  cliff effect of any variable. **Design maturity decides whether an analysis is posted,
  conditional on posting.** A phase 1/2 trial that discloses posts an analysis 11.5% of the
  time; a phase 3 trial that discloses does so 53.4% of the time.

That matters because most published disclosure research measures the FIRST step. **The
selection acting on THIS project's label is mostly the second**, and it is phase-driven.
A model fit on the labelled slice is fit overwhelmingly on phase 3 trials — analysis rate
27.9% against 3.9% for phase 1 — and the product invites users to paste phase 1 trials.
**That is a transportability problem the reweighting question cannot fix, because at a 3.9%
analysis rate the phase 1 stratum has almost no observations to reweight TOWARD.**

**Consequence for the applicability domain, stated plainly:** the endpoint-met model speaks
for pivotal-phase, FDA-regulated, US-sited drug trials that posted a statistical analysis.
Every one of those four conditions is a measured selection axis, not a guess. For a phase 1
trial the honest output is a heavily flagged number or none at all, and §1.1's one-line
pattern is where that lands.

`has_us_facility` is worth noting separately: 58.0% posting against 17.1%, the largest
non-phase effect. It is also the only jurisdictional hook with usable coverage for
pre-2017 trials (9.1% unknown against 37.9% for the sponsor declarations), so it is both
the best-measured selection axis and a strong one.

**Still open in slice two:** `lead_sponsor_class` and `responsible_party_type` read
`unknown` for the whole denominator on the assistant's local copy, because that copy
predates the widened pull. On the operator's current `trial_labels.csv` they are populated
and those two rows of the table are the ones still to be read. The "not measurable (too few
strata)" path degraded correctly rather than printing a fabricated spread.

### 8.1f SLICE THREE: THE FDAAA RULE IS WRITTEN. IT IS ONE-DIRECTIONAL, AND IT COMPLETES THE DECOMPOSITION.

`src/trial_pos/services/fdaaa.py`, written once and nowhere else. A test asserts the PULL
does not import it — stronger than population.py's name-based tripwire, which only catches
a carelessly named function.

**THE RULE CANNOT SAY NO ON JURISDICTIONAL GROUNDS, AND THAT IS ITS MAIN FINDING.** The
statute's three hooks are IND/IDE, a US study site, and US-manufactured export. AACT
records the last two and **has no IND field at all**. So:
- a visible hook satisfies the condition → a positive verdict is available
- NEITHER visible hook does **not** mean the condition fails → UNDETERMINABLE, never
  "not applicable", because the trial may have run under an IND with no US site

A rule that read a missing US facility as a negative would be asserting the absence of
something it cannot observe, across the 55.4% of trials where `has_us_facility` is false.
Confident negatives therefore come only from conditions AACT sees fully: the era, the
phase-1 exclusion, and product scope. The test
`test_no_verdict_is_ever_not_applicable_on_jurisdiction_alone` pins this by varying only
the jurisdiction inputs and asserting no negative ever appears.

**Verdicts over 221,887 drug trials:**

| verdict | n | share |
| --- | --- | --- |
| applicable | 32,647 | **14.7%** |
| not applicable | 122,111 | 55.0% |
| undeterminable | 67,129 | **30.3%** |

Deciding conditions: no regulated product 65,136; `regulated_product_status_unknown`
**52,539 (23.7%)**; phase-1-only 31,925; US study site 29,927; completed before the statute
25,050; phase absent 8,765; no usable date 3,168; US export 2,720; no visible hook 2,657.

**The rule can speak confidently for a minority of the population, and that is the result**
— predicted before it was written. Nearly a quarter of drug trials are undeterminable purely
because both regulated-product declarations are absent, and that is an artefact of the form
postdating 2017 rather than sponsors refusing to answer.

#### DISCLOSURE INSIDE THE VERDICT: the two selection steps are now fully separated

| verdict | n | posted | analysis | CLIFF |
| --- | --- | --- | --- | --- |
| applicable | 18,730 | **80.8%** | 26.3% | 32.5% |
| undeterminable | 52,330 | 51.0% | 16.6% | 32.6% |
| not applicable | 69,225 | **16.1%** | 4.8% | 29.9% |

**Posting-rate spread 64.8% — the largest of any stratifier in the entire audit. Cliff
spread 2.7% — among the smallest.**

Set beside phase (posting spread 35.3%, cliff spread 37.4%), the decomposition is complete
and the two mechanisms are essentially orthogonal:

- **Step one, whether anything is posted, is driven by LEGAL OBLIGATION.** 80.8% against
  16.1%. Nothing else comes close.
- **Step two, whether a statistical analysis accompanies it, is driven by TRIAL DESIGN.**
  The obligation moves it by 2.7 points; phase moves it by 37.4.

**This project's label depends on step two.** So the selection acting on it is design-driven,
not compliance-driven — and most published disclosure research measures step one, where the
obligation dominates. Anyone reasoning about this label from the disclosure literature will
reach for the wrong mechanism.

**An honest caveat on the 80.8%.** That is compliance among trials the rule could CONFIRM
are applicable, which requires a recorded US site or export plus a product declaration —
i.e. the well-documented, recently registered, mostly industry trials. It is compliance
among the easiest trials to confirm and is an upward-biased estimate of compliance overall.
It should not be quoted as an FDAAA compliance rate.

#### The PHASE1/PHASE2 reading, flagged as a judgment
The statute excludes a study that is phase 1 ONLY, so a `PHASE1/PHASE2` registration is
treated as in scope. That is a reading, not a fact, and it moves **14,740 trials**: 4,396
applicable, 5,391 not applicable, 4,953 undeterminable. `PHASE1_ONLY_PHASES` is the one
place to change it and `applicability_coverage` reports the affected count every run.

### 8.1g THE USER-FACING FLAG (owner's decision: return the number, flag it heavily)

A flag saying only "few trials like yours in the training data" reads as generic hedging.
Naming the mechanism is what makes it credible, and there are TWO distinct mechanisms
needing distinct words:

1. **The statute never asked.** `FLAG_FDAAA_EXEMPT`: "Phase 1 trials are exempt from the
   FDAAA results-reporting requirement, so few of them post the statistical analysis this
   estimate is trained on."
2. **The question does not apply.** 63% of phase 1 primary endpoints are pharmacokinetic or
   dose-finding — AUC, Cmax, MTD — reported as values rather than tested against a
   threshold, so "did it meet its primary endpoint" has no answer to estimate. Only 7% of
   phase 1 primary outcomes are efficacy-shaped, against 20% for phase 3.

`compose_flag` assembles these and appends `FLAG_THIN_TRAINING`. **The endpoint clause is a
PARAMETER, not something the module derives**, because the endpoint-type classifier does not
exist yet and a flag that invented the clause would assert a measurement nobody made. It is
visibly missing rather than silently omitted.

**NEXT: the endpoint-type classifier.** And it should gate per TRIAL, not per phase:
endpoint type is the variable that actually decides whether the question applies, and phase
is a proxy for it. A phase 1 trial with an efficacy-shaped primary endpoint (~7%, roughly
2,000 trials) is a legitimate target; a phase 3 trial with a PK primary endpoint (6% of
phase 3) is not, and a phase-based gate waves it through. The keyword classification above
is CRUDE and unvalidated — "other" is 31% of phase 1 rows and 64% of phase 3 — so it is a
hypothesis with strong support, not a measurement. It needs a classifier in `src/` with
agreement measured against a hand-checked sample, because a keyword rule that decides what
the tool refuses to answer is a verdict-producing threshold like any other.

### 8.2 Step 6.3: the posting-bias audit, as a READER of 8.1b's output
One script pulls, a second reads and reports coverage and bias before anything downstream
consumes it. Lets the audit be re-run without re-pulling.

Still the project's credibility. Must produce **THREE applicability domains, not one** —
endpoint-met's is about disclosure selection, advancement's about censoring and linkage,
market's about drug resolution, indication matching and censoring. A single pooled
"applicability domain" is meaningless across targets with 5% to 100% coverage.

Order within it — cheapest and most decision-relevant first:
1. posting rate by phase / era / sponsor class / FDAAA component, **on ACTUAL-dated rows
   only** (§6.1)
2. ~~phase × headline label~~ **DONE, see §5** — 5,571 of the 7,568 negatives are drug
   trials
3. the FDAAA applicability rule, **written once, explicitly**, with §4.2's coverage in hand
4. **date_type × era** — how much of the era picture rests on plans rather than events
5. the reweighting

The labelled slice is under 5% of the population and is disclosure-selected, so a model
trained on it will not transport to the phase 1/2 questions users actually ask. That is what
the applicability domain and reweighting exist to state.

### 8.3 Build the advancement label
No posting dependence, so it becomes the workhorse target. Multiple lookahead windows
(2/3/5 years), each with its own censoring exclusion; a trial enters training for a window
only if that window closed before the data cutoff. Competing risk stated plainly: failure to
advance can reflect portfolio deprioritisation rather than a negative result. Futility stops
(2,599) are clean negatives here — which is where the `why_stopped` work pays off, since it
cannot touch the headline endpoint-met slice. FDA approval enters as use B (§4.5).

### 8.4 Then features, then models
**DUE NOW, because scoping is settled: which multi-endpoint reading is the target?**
`label_row` sets strict from `any_primary_met`, the most permissive of the four, and `any`
differs from `all` on 2,191 trials. Recommendation: `any_primary_met` as headline with
`all_primary_met` reported beside it, matching the strict/broad pattern — but this is a
product claim, and "cleared at least one of its primary endpoints" is a weaker sentence than
most people hear in a stated 72%. **Owner decision outstanding.**

Calibration is first-class, not an afterthought: a stated "72%" is only honest if ~72% of the
72%-bucket trials succeed. Report reliability curve + Brier + ECE beside AUROC and log-loss,
per model, per §1.1 point 3.

---

## 9. Hard-won lessons. Do not relearn these.

1. **AACT regenerates surrogate keys on every nightly rebuild.** Never join across snapshots
   on `outcomes.id`, `outcome_analyses.id`, `result_groups.id`. A dump from three days ago
   had `outcomes.id` in the 2626xxxxx range; the same outcomes today are 2647xxxxx. Both
   well-formed, zero overlap on 498 shared trials, no error raised. Pull all tables in one
   session or join on natural keys.
2. **Negation handling: delete the negated span; do not veto the field and do not split into
   clauses.** A document-wide veto discarded genuine futility ("Lack of efficacy of the drug;
   no safety concern"). Clause-splitting on "and" tore "No safety and/or efficacy concerns"
   apart and misfiled five business terminations as safety.
3. **Contrast detection must precede single-arm refusal** in `contrast_family`. The reverse
   order blocked "Difference in Percentage", "Proportion difference", "Geometric Mean Ratio"
   and cost 95 analysis rows.
4. **No hardcoded reference values in tests.** Derive from closed forms, independent
   computations, structural properties, or the constant depended on. Three transcribed
   constants were wrong on first write; a constant that matches a bug certifies the bug.
5. **`pandas` turns empty CSV cells into float NaN, which is truthy.** It once made every
   missing results-posting date read as posted. `_scalar()` guards this.
6. **`pandas` also destroys the literal string "NA" on read.** Its default `na_values`
   contains "NA", so `read_csv` converted AACT's explicit not-applicable phase to NaN before
   any tested code saw it. The live path was unaffected, so the offline `--from-raw`
   re-derive would have **silently disagreed with the live pull**. Fixed with
   `keep_default_na=False`. New offline scripts use the `csv` module directly.
7. **"NA" is not "absent", and the distinction is field-specific.** `_text` originally listed
   "na" among its blank tokens, collapsing AACT's explicit not-applicable phase into missing
   — while the module docstring claimed to preserve exactly that distinction. Only real data
   exposed it. Text/date components now report **present / not_applicable / unknown**. For
   `study_type` 'N/A' genuinely does mean unknown, handled locally.
8. **A sponsor's efficacy disclaimer should be believed.** "Business Decision; No Safety Or
   Efficacy Concerns" appears as near-identical boilerplate across 8 trials. Taking it at
   face value is the conservative choice.
9. **`pip install -e .`** — `pyproject.toml` already has `where = ["src"]`. All scripts
   importing `trial_pos` also self-bootstrap `src/`, because `run_checks.py` sets
   `PYTHONPATH` itself and therefore the gate can be green while a hand-run script fails.
10. **Approval is not an endpoint verdict, and indication detail does not fix it.** See §4.5
    use A. The temptation recurs whenever the negative class looks thin.
11. **A cohort restriction inherited from a dead design will quietly cap the whole project.**
    TOP × ChEMBL was load-bearing for Gen 1/2 and pure cost for Gen 3, and it became the
    stated reason for a model-class decision that turned out to be wrong by 20×. When a
    constraint is cited as settled, check which generation it belongs to. **Still live:
    `data/chembl/drug_indication.csv` carries that cap and looks reusable.**
12. **A tripwire that fires on naming rather than behaviour gets switched off.** The
    anti-applicability test matched any name containing "applicab" and false-positived twice
    on innocent helpers. It now checks an exact set of verdict names.
13. **Interrupts that look like yours may not be.** Two pulls died to `KeyboardInterrupt`
    from VS Code's conda auto-activation. Check for an injected command in the terminal after
    the traceback before suspecting the code.
14. **Aggregate a one-to-many table in a subquery, never a plain join.** Joining
    `interventions` directly would multiply studies rows and inflate every count in the audit
    without erroring.
15. **Never call `date.today()` inside logic that decides what enters a training set.**
    Inject the reference date.
16. **A diagnostic can be structurally blind to the thing it is diagnosing.** The
    primary-to-overall gap is computed on rows where both dates exist, which excludes every
    row that uses the fallback. **Same shape again in §3.1:** the interval rule's validation
    set consists of rows that posted a p-value, while production tier-C rows did not. Check
    what a diagnostic's denominator excludes, every time.
17. **Let unrecognised enum values through as themselves; never map them to None.** AACT
    reports `'Estimated'` where the constant said `'Anticipated'`, and the gap was visible
    within one run because `normalize_date_type` returns the lowercased original.
18. **Define a predicate so a vocabulary gap fails safe.** `is_actual_date` asks "is it
    actual", not "is it not planned". `is_planned_date` is separately defined and is NOT the
    negation — conflating them would have discarded 32% of start dates on a guess.
19. **Matching marginal totals do not mean two fields agree.** `is_drug_trial` and
    `phase_is_drug_like` have totals within 1% of each other and disagree on 49,759 trials,
    because the errors run both ways and cancel. Compare paired, never marginal.
20. **Check whether a codified source already exists before declaring a problem hard.**
    DrugCentral publishes FDA indications mapped to SNOMED-CT/UMLS as a Postgres dump, and
    AACT's conditions are in MeSH, a UMLS subset — so the match is code-to-code. **And check
    the repo before building the crosswalk:** `data/ontology/mondo_xref.csv` already had
    7,756 MeSH↔UMLS pairs while rev 4 described that work as speculative.
21. **"Not yet" is not "no", and the difference correlates with time.** Labelling an
    unapproved recent drug as a market failure builds a calendar-reading model. Fixed windows
    with EXCLUSION of unclosed windows — never zero-filling. See §1.2.3.
22. **A NULL VALUE IS NOT A PROPERTY OF A FIELD NAME.** `null_value_for` mapped param_type to
    a null, but a ratio's null depends on the SCALE the sponsor used, which the name does not
    carry. 480 rows named it; 1,868 said only 'Geometric mean ratio'. A regex on the name
    could never have fixed this. Separate what the name can answer (`contrast_family`) from
    what only the numbers can (`ratio_scale`).
23. **When a rule can be measured, measure it before arguing about it — and check whether
    your own proposed fix is worse.** Re-centring ratios on 100 scored 48.4% agreement;
    inferring the scale per row and picking a null scored 88.9%, WORSE than the 90.7% bug it
    replaced. Only refusing the ambiguous stratum improved anything. Two proposed fixes
    failed on data, and that was cheaper to discover than to debate.
24. **A constant is not a test, and raw agreement hides it.** The interval rule scored 66.1%
    raw agreement on percent-scaled ratios while answering "met" on 543 of 543 rows. Quote
    kappa (0.000) and the candidate positive rate (100%) together; either alone is
    survivable, both together are not.
25. **Removing a bad verdict can RAISE a count, and that looks like a bug.** Withdrawing 479
    bogus tier-C labels moved 20 trials from tier C to tier A/B, because `tier_min` is the
    weakest COUNTED tier. Headline went UP by 20 while strict labelled went DOWN by 479. Both
    are correct. Report a delta per cell with its direction, never as a single net number.
26. **A DECISION RULE HAS PARAMETERS, AND THEY ARE USUALLY IN THE DUMP.** Three separate
    bugs, one mistake: the null depends on the ratio's SCALE, the interval test depends on
    its COVERAGE (`ci_percent`, column 19, never read), and which test applies at all
    depends on the DESIGN (`non_inferiority_type`, read for tiering but not for the
    interval branch). Before trusting any rule, list the parameters its validity rests on
    and check each one against a column.
27. **Refuse the verdict, not the row, when the refusal is directional.** Off-coverage
    intervals are not uniformly useless: a looser interval that fails to exclude the null
    still implies failure at the required coverage, and a tighter one that excludes it
    still implies exclusion. Blanket refusal would have discarded 6,853 sound negatives.
    Ask what the observation IMPLIES, not whether it is ideal.
28. **Selecting on your own output breaks agreement statistics.** A rule that conditions on
    its candidate verdict distorts the 2x2 marginals, so kappa on the retained subset is
    not a validation number. The tell is raw agreement rising while kappa falls. Validate
    per band, unconditioned, and let the implication logic carry the rest.
29. **A warning that fires on thin data gets ignored.** The one-directional-disagreement
    banner tripped on a 21-row stratum with two disagreements, printing "DISQUALIFYING"
    about nothing. Verdict banners need a minimum n, named
    (`MIN_STRATUM_FOR_VERDICT`), same as any other threshold.
30. **"Contains a number" is not "states the quantity".** 10,177 of 11,513 NI descriptions
    contain a number and only 1,877 put one next to the word "margin"; the rest are alpha
    levels, power and sample sizes. A presence check on the wrong token overstated
    recoverability by a factor of five.
31. **A RULE KEPT BY A COMMENT DOES NOT SURVIVE COPY NUMBER SIX.** "Aggregate a
    one-to-many table in a subquery, never a plain join" was a comment beside one
    hand-written subquery. The widened pull needed six. Declaring the sources as data and
    generating the SQL let the rule become a test that also covers sources added later --
    the failure it guards (multiplied studies rows, inflated counts, no error) is the
    quietest one available.
32. **Check the harness before blaming the code.** My first end-to-end audit test fed the
    label CSV straight to `audit`, which expects `derive` output; pandas summed the string
    column '1'/'0' by concatenation and int() failed on an 8,000-digit number. The audit
    was fine. Reproduce through the real path (`--from-raw`) rather than a shortcut that
    skips the type boundary.
33. **A COLUMN THAT IS DELIBERATELY ABSENT FROM A RECORD WILL BE READ FROM IT ANYWAY.**
    The entity aggregates were kept off the label record on purpose, then the coverage
    audit read them off the label record and reported 0.0% for five sources while a
    neighbouring section reported 92.5% for columns that DO live there. Two sections of the
    same printout disagreeing is the tell. When a deliberate separation exists, the
    diagnostic has to be plumbed to the other side of it, not pointed at the convenient
    collection.
34. **A TIER THAT NEVER PRODUCES A VERDICT WILL NEVER APPEAR IN A VERDICT-DERIVED
    SUMMARY.** tier E was added so non-inferiority trials stayed visible, and then printed
    as 0 in every tier distribution because tier_min was computed from counted outcomes
    only. A category created for visibility needs its visibility tested, not assumed:
    `test_trial_with_only_ni_intervals_reports_tier_e_not_tier_d` is that test.
35. **A CEILING THAT ADMITS EVERYTHING BOUNDS NOTHING.** "ANY drug-name source present:
    100.0%" was presented as the market target's feasibility evidence. `intervention_names`
    is free text on every trial, including "Placebo", so its presence is not resolvability
    and the figure was uninformative by construction. Before quoting a bound, ask what
    value of it would be BAD news; if no value would be, it is not a bound.
36. **Report a rate in the denominator the decision uses.** Coverage over all 460,569
    trials is not coverage over the drug-only population the models are scoped to, and the
    two differ by more than a factor the eye can correct for.
37. **A STRATUM WHOSE LABEL AN AVAILABLE FEATURE DETERMINES TEACHES THE MODEL THE
    FEATURE.** Phase 4 is 30,899 drug trials whose market label is 1 by construction. Left
    in, a model scores well by reading the phase column, and the metrics look fine. This is
    the same failure as a decision rule that answers the same thing on every row, moved
    from the rule to the population. Before choosing a population, ask which rows have
    their label decided by something already in the feature matrix.
38. **A WINDOW AND A POPULATION RESTRICTION CAN ONLY BE JUDGED TOGETHER.** W=3 was rejected
    as uninterpretable over all phases and is the right choice over pivotal phases, because
    the objection ("most 0s are still in development") was a statement about early-phase
    trials, not about three years. A parameter dismissed on its own may be correct in
    combination, and the combination is what gets deployed.
39. **A DENOMINATOR IS A DECISION, AND IT MUST BE MADE BEFORE THE FRACTION.** The first
    endpoint-met eligibility predicate had no tier condition and reported 15,504 eligible
    against a modelling population of 14,368. The module written specifically to stop
    denominator confusion shipped with a denominator error, because the test fixture
    omitted `tier_min` and therefore never exercised the filter that was missing. A fixture
    that omits a field cannot test a condition on it.
40. **A COVERAGE GRADIENT ACROSS TIME IS A TRANSPORTABILITY PROBLEM EVEN WHEN THE AVERAGE
    IS FINE.** MeSH matchability in the market cohort is 75.0% overall and falls 80.7% ->
    76.2% -> 70.2% from oldest era to newest. The average clears the gate; the gradient
    means the trials a user is most likely to paste are the least matchable, which the
    average conceals entirely. Stratify every coverage figure by era before quoting it.
41. **A SELECTED SLICE CANNOT BE REWEIGHTED TOWARD A STRATUM THAT IS NEARLY EMPTY.** The
    phase 1 analysis rate is 3.9% against phase 3's 27.9%. Inverse-probability weights
    would multiply a handful of phase 1 observations up to represent a huge population, and
    the resulting variance is not a correction, it is a guess with a standard error. Measure
    the selection, state the domain, and decline the extrapolation.
42. **A TWO-STEP SELECTION MUST BE DECOMPOSED OR IT IS ATTRIBUTED TO THE WRONG CAUSE.**
    Regulatory obligation drives posting (51.1-point spread) and barely touches the cliff
    (3.1). Phase barely beats it on posting (35.3) and dominates the cliff (41.9). Measured
    as one combined rate, the whole effect would have been credited to whichever step was
    larger, and the label's actual selection mechanism -- the second step -- is the one most
    published disclosure work does not measure.
43. **AN UNOBSERVABLE INPUT MAKES A RULE ONE-DIRECTIONAL, AND THE RULE MUST SAY SO.**
    FDAAA's three jurisdictional hooks include IND status, which AACT does not record. So
    a visible hook proves applicability and an absent one proves nothing. Reading "no US
    site" as "not applicable" would have asserted the absence of an unobservable across
    55.4% of the population. When a rule's inputs are incomplete, work out which DIRECTION
    survives the gap and return undeterminable in the other.
44. **TWO MECHANISMS CAN LOOK LIKE ONE UNTIL THE STEPS ARE SPLIT.** Legal obligation moves
    the posting rate 64.8 points and the analysis-conditional-on-posting rate 2.7. Phase
    moves them 35.3 and 37.4. Measured as a single disclosure rate, the answer would have
    been "obligation explains it" -- and this project's label depends on the OTHER step,
    the one obligation does not touch. Most published disclosure research measures the step
    that does not apply here.
45. **A CAVEAT THAT AN ESTIMATE IS BIASED UPWARD IS PART OF THE ESTIMATE.** 80.8% posting
    among confirmed-applicable trials is compliance among trials that could be CONFIRMED
    applicable -- documented, recent, mostly industry. Quoted without that, it is an FDAAA
    compliance rate, and it would be wrong.
46. **A refusal must be counted or it vanishes.** 439,157 trials sit in tier D; 1,843 more
    would disappear into it without trace. `refusal_kind`, the per-kind counts and
    `endpoint_na_reason` exist so each refusal is a number and a stateable sentence, not an
    absence. Tier E goes further: non-inferiority trials rely on a different test, so they
    get a NAMED tier rather than being dissolved into "no analysis". Same principle as
    lesson 17, applied to a decision rather than an enum.

---

## 10. Standing discipline

- One smoke-tested script per step; pure logic in `src/` with tests, scripts as thin I/O and
  audit printouts only. **No derivations inside a script** — `agreement.py` exists because
  kappa was violating this.
- `python scripts\run_checks.py` must stay green. **Currently 425.**
- **No hardcoded numeric reference values in tests.** Derive from a closed form, an
  independent computation, a structural property, or the constant depended on.
- **No magic numbers.** Name them or expose them as CLI flags, with the reason written beside
  them. Every threshold producing a verdict must be a flag, and the script must print which
  value it used. `alpha_used` and `ratio_scale_floor_used` travel on every label row for this
  reason.
- **Audit-first.** A new script reports coverage and bias before anything downstream consumes
  its output. Distinguish "unknown" from "zero" **and from "not applicable"** everywhere.
- **Tri-state by default.** A boolean pulled from AACT has three states: true, false, and
  never-collected. Collapsing the third manufactures findings.
- R7: `why_stopped`, `overall_status`, `why_stopped_class`, `termination_score`,
  `safety_termination`, `results_first_posted_date`, `results_first_submitted_date`,
  `results_posted`, `were_results_reported` are LABEL INPUTS and must never be features.
  `endpoint_label.LABEL_DERIVED_FIELDS` is the registry. **This is currently only printed,
  never asserted** — build the assertion as code in §8.4, and make the registry PER-TARGET
  (§1.1 point 1).
- FDAAA components and drug signals are NOT label inputs and may become features.
- Unit-level MoA priors use `unit_year = max(phaseendyear)`. Attaching that to an earlier
  trial in the same unit leaks the future. Recompute the LOO cut at each index trial's own
  readout year.
- Env: Windows/PowerShell, user runs scripts and pastes output, assistant writes code, data
  local. `AACT_USER` / `AACT_PASSWORD`; `scripts\check_aact_connection.py` diagnoses failures
  layer by layer. Set env vars with **single** quotes — double quotes make PowerShell expand
  `$` and backticks, altering the password before Postgres sees it.

---

## 11. Parked, deliberately

- **Mechanism and disease-area attribution.** `archive/.../moa_encoding.py`, `disease_ta.py`,
  `disease_type.py` compute these but for Gen 2: keyed on drug-indication units, targeting
  ChEMBL approval, depending on a Trialtrove-152 label plain AACT trials lack. Fork: salvage
  and re-key/re-target, or build fresh from AACT's `browse_conditions` /
  `browse_interventions` MeSH fields. **§5 is now resolved in favour of drug-only, which
  removes the "half the population has no mechanism" objection** — so this is closer to
  actionable than rev 4 implied, once §8.1b lands the MeSH columns.
- **The advancement label's linkage key.** Needs a (drug, indication) notion;
  `archive/scripts/build_units.py` holds one. Decide explicitly rather than defaulting in.
- **`probe_ctgov.py`** — the live CT.gov v2 API fetch path the tool eventually needs. Not on
  the critical path until a model exists.
- **`outcome_analysis_groups`** — which arms an analysis compared. `groups_desc` is absent
  from `outcome_analyses` on the current server; arm-level detail lives in this separate
  table, so getting it later is a join, not a renamed field. Not needed for the label.
- **The full bioequivalence decision rule** (interval inside 80–125%). Implementable, and
  rejected: the bounds are conventional, widened for highly variable drugs and narrowed for
  narrow-therapeutic-index drugs, and AACT does not carry which regime applied. More
  fundamentally a generic matching its reference is a pharmacokinetic claim, not evidence the
  drug works, so it belongs in a different target if anywhere. The 479 trials carry
  `endpoint_na_reason` and remain identifiable — and are a useful FEATURE signal for market,
  since a bioequivalence trial implies the reference drug is already approved.
- **`max_phase == 4` meaning "approved"** is an unnamed literal in the archived
  `join_chembl.py`, `label_indication.py`, `build_units.py`. Give it a shared constant if any
  is revived.