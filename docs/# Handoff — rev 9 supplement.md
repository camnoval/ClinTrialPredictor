# Handoff — rev 9 supplement

**Read with rev 7 and rev 8, not instead of them.** Rev 7 remains the durable record for
every section neither supplement names; rev 8 for every section rev 9 does not name. Where
they disagree, the later revision wins and says so.

Scope of this revision: the endpoint clause reached the user, the gate was revised once more
(B2), the training population is now decided by one predicate, the extra AACT fields were
probed and pulled with per-column provenance, retrospective registration was measured and
excluded, and the posting-bias audit (Step 6.3) ran. Three bugs were fixed and four stale
printed figures removed. Expected test count **786 passed, `GATE: GREEN`**.

**Every figure below comes from a run on the project machine** unless marked *eye-read*
(the assistant reading printed endpoint text) or *arithmetic* (derived from printed counts).
No sandbox figures appear in this revision. Rev 8 §12.13's sandbox figures are superseded
in §12.16.

---

## §0.1 — SCRIPT BUGS: status

Rev 8 item 4 (**clause not wired into `fdaaa.compose_flag`**) is **closed** — §12.16.
Rev 8 items 5 and 6 (`build_endpoint_type_sample.py`: duplicate-text collapse, pre-§12.9
defaults) are **still open**. Its closing print also still says nothing reaches
`compose_flag`, which is now false.

Two known and unfixed:

- `FLAG_THIN_TRAINING` on a **pre-statute phase 3** trial: `NOT_APPLICABLE` attaches
  not-required-to-post plus thin-training, whose text compares against "a later-phase
  trial" — false for phase 3. Predates rev 9.
- `DECLINE_SENTENCE` still carries entries for bioequivalence, dose-finding and safety,
  which can no longer be produced. `MECHANISM_WORD` was pruned for that reason and this dict
  was not. Harmless; inconsistent.

---

## §12.16 — THE CLAUSE REACHES THE USER

`fdaaa.trial_flag(applicability, gate_record)` is the entry point and returns
`{estimate_shown, flag, clause_required}`:

| gate | number shown | flag |
| --- | --- | --- |
| refused | **no** | the decline sentence, *instead* of the FDAAA caveats |
| qualified | yes | FDAAA caveats + the success-meaning clause |
| plain | yes | FDAAA caveats only |
| undeterminable | yes | FDAAA caveats; endpoint clause silent except under B2 (§12.18) |

**Fails closed both ways**: a refusal with no sentence raises, and a record for which
`requires_clause` is true but whose flag lacks the clause raises. A test iterates every
multiset of up to three primaries under both roll-ups and asserts no qualified record ever
returns a bare number. Run over every drug trial on the project machine: **`trial_flag`
raised 0 times across 222,374 drug trials.**

**Behaviour change.** `FLAG_THIN_TRAINING` was attached whenever any part was present,
including an endpoint clause alone. Its text compares against "a later-phase trial", false
on a phase 3 trial with a safety co-primary. It is now attached only by the FDAAA sentences.
Nothing had ever passed a clause, so no user saw the old behaviour.

## §12.17 — THE CO-PRIMARY CLAUSE

The qualified sentence said "this trial's primary endpoint is …". On the project machine,
**653 of 973 headline-labelled clause trials** (11,690 of 48,711 unlabelled) also
registered an efficacy-shaped or bioequivalence primary, so the sentence was false for the
trial as a whole in 67% of the labelled cases where it appeared.

The co-primary form applies whenever the trial registered any primary outside the qualified
class naming the clause (`is_coprimary`). Keyed by roll-up, because under ANY the qualified
endpoint alone can carry a "met" and under ALL it cannot:

> 1 of this trial's 2 primary endpoints is a safety or tolerability measurement. This
> estimate counts meeting any one primary endpoint as success, so it may reflect only that
> an adverse-event rate was acceptable, not that the treatment worked.

Known gap: `[dose, safety, efficacy]` names only the dominant qualified class.

## §12.18 — B2: PHARMACOKINETIC BESIDE UNREADABLE TEXT

Under ANY, a trial whose primaries are value-reporting plus at least one unclassifiable is
now **undeterminable with a mandatory clause** (`REASON_UNTESTABLE_BESIDE_UNREADABLE`), not
refused. The unread primary may be the tested one; refusing asserted a reading nobody made.
The clause names the untestable primaries and says the estimate applies only if the others
were threshold-tested, which lowers the cost of the false allowance that §12.11 weights most
heavily. Under ALL the case still refuses.

Measured effect: **91 labelled and 2,707 unlabelled** trials moved from declined to shown
with the clause. Labelled declined fell **424 → 333**, now all-pharmacokinetic.

Evidence that motivated it, *eye-read*: in a 25-trial sample of the 424, the
pharmacokinetic-plus-unreadable trials carried a real efficacy endpoint in the unreadable
slot in 4 of 5 (intragastric pH, trough FEV1, time to first exacerbation, gastric emptying).

**A pattern carve-out (B1) was considered and declined** — an exclusion for an AUC of a
non-drug measure — as too likely to be fitted to the sample that suggested it. The known
cost is recorded in `endpoint_type.py` and in §12.19.

## §12.19 — THE TRAINING POPULATION IS ONE PREDICATE

`eligibility.eligible_for_endpoint_met` now decides the endpoint-met training population
completely. Two exclusions were added to the existing reasons, both **applied last** so
every earlier count is unchanged:

| reason | targets | count, endpoint-met |
| --- | --- | --- |
| `registered_after_primary_completion` | all three | 600 |
| `endpoint_gate_refuses_this_trial` | endpoint-met only | 306 |
| `registration_timing_unknown` (undeterminable) | all three | 18 |

**Endpoint-met eligible: 13,493 drug trials.** 13,493 + 306 = 13,799, the eligible count
before the gate was folded in. 306 is under the 333 refused-yet-labelled because 27 of those
are already retrospective or timing-unknown.

Full endpoint-met reasons: not a drug trial 239,599; no primary analysis posted 207,016;
non-headline tier 941; retrospective 600; gate-refused 306; timing unknown 18.
Advancement: eligible 85,132, of which retrospective exclusion took **11,106**.
Market: eligible 22,337, retrospective **3,060**. Tier-C sensitivity stratum: **880**
(899 before the gate was folded in; the loose-tier comparison now applies the gate too).

**The gate exclusion rests on the hand labels, not on an outcome.** Of the 333
refused-yet-labelled: tier A 258 / tier B 75; met 217 / not met 116; phase 1 135, phase 2
79, pivotal 63, post-approval 41, unknown 15. Several genuine-PK labels in the sample were
drug–drug interaction tests, where "met" means an interaction was found — inverted, not
noisy. **Known cost, *eye-read*:** 9 of 25 in the post-B2 sample measured something other
than the drug (FEV1 AUC ×4, pain-intensity AUC, nasal cross-sectional-area AUC,
postprandial-glucose AUC, serum cortisol, troponin). Those labels are excluded too, and
those trials are declined at serving.

**Fails closed.** Endpoint-met eligibility raises on a row lacking the merged gate verdict
or the first-submitted date, rather than reading the absence as "not excluded". This caught
a stale script on its first run (lesson 67). The merges:

- `merge_registration_timing(rows, submitted_by_nct)` — a trial absent from the fields file
  gets a blank, so undeterminable, never prospective.
- `merge_gate_exclusion(rows, classes_by_nct)` — runs the gate itself; a trial with no
  primary text gets `trial_gate([])`, which is the gate's answer, not a default.

`audit_posting_bias.py` performs both. Its run merged the first-submitted date onto 461,971
of 461,973 label rows and found 11,620 rows the gate refuses across all trials (the extra
over the serving audit's drug-only 10,564 are non-drug trials, ineligible earlier).

**Not yet printed: the negatives inside the 13,493.** Do not estimate it. See §14 item 1.

## §12.20 — RETROSPECTIVE REGISTRATION

A trial first submitted after its primary completion wrote every "registration" field
knowing the outcome, and no trial scored at serving — always before readout — can look like
it. That is a property of the **row**, which no column provenance can express.
`aact_fields.registered_after_primary_completion` is tri-state: false on or before
completion whatever the date type; true after an actual or untyped completion; unknown
after an *estimated* one (a plan already past at submission is incoherent) or with either
date absent.

| population | n | after | share |
| --- | --- | --- | --- |
| all pulled interventional | 462,148 | 55,411 | 12.0% |
| drug trials | 222,373 | 19,496 | 8.8% |
| drug, headline-labelled | 14,417 | 600 | 4.2% |
| … strict negatives | 5,591 | 172 | 3.1% |

Lag among labelled retrospective trials: p50 328 days, p90 1,837; **45.8% more than a year
late.**

**They meet their endpoint more often, within every phase group:**

| phase group | retro n | retro met | prospective met | gap, pts |
| --- | --- | --- | --- | --- |
| phase2 | 110 | 67.3% | 49.4% | +17.8 |
| post_approval | 155 | 72.9% | 59.6% | +13.3 |
| pivotal | 203 | 73.9% | 69.3% | +4.5 |
| phase1 | 58 | 79.3% | 60.1% | +19.2 |
| unknown | 74 | 60.8% | 58.4% | +2.4 |

*Arithmetic*: phase 2 and post-approval clear roughly four standard errors on the retro
proportion alone; pivotal does not (≈1.5); the last two are under the 100-trial floor.
**Quote the per-phase rows, not the pooled 71.3% vs 60.8%**, which mixes phase composition
into the gap. The exclusion does not depend on the gap.

## §12.21 — EXTRA AACT FIELDS: PROVENANCE, PROBE, PULL

### Three provenances, not two

AACT is a current snapshot; ClinicalTrials.gov's version history is not in it. So "knowable
at registration" cannot be asserted from AACT. `services/aact_provenance.py` draws the line
at whether the registry workflow overwrites a field **as a matter of course** during
conduct:

- `registration` — describes the protocol; edits possible, not systematic.
- `editable_current_value` — registration-type but routinely updated: estimated dates
  becoming actual, sites, countries, outcome lists, expanded access, data-sharing plans.
- `post_hoc` — exists because the trial ran. `actual_duration`, and **`enrollment`**, which
  is overwritten with the actual count at completion; the planned figure is not retained.

18 tables are classified, every column of each, with cardinality **declared before it was
measured**. An unclassified column raises. A test pins every `LABEL_DERIVED_FIELDS` entry
as `post_hoc`. Judgment calls worth revisiting: `sponsors` registration despite
post-readout partnering; `eligibilities.criteria` registration despite rescue amendments;
`verification_date` and data-sharing editable; `source_class` and `has_dmc` registration.

### The probe (`audit/probe_aact_fields.py`, read-only)

- **Every declared one-per-trial table is exactly 1:1**, zero repeats, zero duplicates:
  designs 600,802; eligibilities 604,612; calculated_values 605,592; brief_summaries
  604,612; responsible_parties 587,162 — against 605,592 studies. Gaps are unknowns.
- The 980 records missing from eligibilities and summaries are all `WITHHELD` with
  `delayed_posting`; the interventional filter drops them.
- **Exact duplicate rows**: interventions 3,759, facilities 1,106. Counts must be distinct.
- **Exactly one lead sponsor per trial** (605,592 = studies).
- **Empty columns**: `design_outcomes.population`, `nlm_download_date`, its description.
- Tri-state traps: `phase` `'NA'` 238,060 vs null 143,572 (not applicable ≠ not stated);
  masking subfields null on 405,074 rows (not applicable under `masking = NONE`, unknown
  otherwise); `agency_class` `UNKNOWN` 47,302 and `AMBIG` 110 are not classes.
- Presence encodes a label input: `last_known_status` non-null on exactly the 98,141
  `UNKNOWN`-status trials; `results_first_submitted_qc_date` on exactly the 80,358 with
  results. Already `post_hoc`, but must be named as label encoders when the per-target
  registry (rev 7 §8.4) admits any post-hoc field.
- 47 columns the probe found unclassified are now classified.

### The pull (`scripts/pull_aact_fields.py`)

Writes only its own files, to `.partial` then renamed after the last chunk passes; refuses
to overwrite without `--overwrite` and refuses any filename another script owns (§0.3).
Per chunk, fatal: exactly one row per requested id. One-per-trial tables LEFT JOIN;
many-per-trial tables only inside `GROUP BY` subqueries scoped to the chunk. Every column
carries provenance; an aggregate takes the worst among the columns it reads.

Run: **462,148 rows, 136 field columns + 17 text, 388 s, 0 chunk mismatches, 0 trials with
other than one lead sponsor.** Snapshot proxy (`max(studies.updated_at)`):
`2026-10-05T04:14:30`.

Outputs: `trial_registration_fields.csv`, `trial_registration_text.csv`,
`trial_registration_fields.provenance.csv`, `trial_registration_fields.manifest.json`.
`data/aact/smoke/` is a 4,000-trial smoke run and can be deleted.

### What the post-pull audit found (`audit/audit_registration_fields.py`)

- **§0.3 restore verified for drug trials**: the label file's lead sponsor class agrees with
  a fresh pull of the sponsors table on **222,373 of 222,373**. Non-drug rows not compared.
- `studies.source_class` disagrees with the lead class on 0.4%, led by NIH-vs-other (987).
  It is the **submitting organisation's** class, not the lead sponsor's. Use the lead.
- **AACT's facility counts count rows, duplicates included**: `number_of_facilities` equals
  the raw row count in every trial; **497 single-site trials read as multi-site**; 894
  trials have a count different from their distinct sites. Use `facilities__n_distinct`.
  Nothing reads AACT's values today.
- Interventions with rows beyond distinct (type, name): 10,237 trials (2.2%).
- Snapshot drift: 175 more interventional trials than the label file; one drug label row
  absent from the fields file. Features joined to labels mix two snapshots — record both.

## §12.22 — §12.15 QUALIFIED: THE REGISTERED TEXT IS EDITED AT POSTING

Registry history checked by hand on 2026-10-06 for NCT02100670, NCT00782210, NCT01040728:
**in 3 of 3 the results-posting version also edited the registered Outcome Measures
section**, and 2 of 3 edited it during conduct as well.

So rev 8 §12.15's byte-identity between `design_outcomes.measure` and `outcomes.title` is
most likely because the registered field is rewritten when results go in, not because the
two agree independently. **The gate was validated on posting-time text; serving reads
pre-results text.** How much that matters depends on whether the edits touch measure
titles or only time frames and descriptions — the version list names sections, not fields.
`design_outcomes` is classified `editable_current_value`.

## §12.23 — POSTING BIAS (STEP 6.3) RAN

Denominator: drug trials with an actual primary completion whose 12-month posting deadline
has passed — 140,663.

- **Posting is graded on 8 of 9 variables** at the 10% bar, which therefore does not
  discriminate between them. Phase 1 16.8% to phase 3 52.2%; FDA-regulated false 10.6% to
  true 61.8%; FDAAA not-applicable 16.1% to applicable 80.9%.
- **The cliff row discriminates.** Analysis posted given results posted is graded by phase
  (9.4% to 48.5%), lead sponsor class (19.5% to 36.3%), responsible-party type and expanded
  access — and **flat** by era (2.9-point spread), FDA regulation (1.1) and FDAAA verdict
  (9.2). The obligation drives whether a trial posts, not whether it posts an analysis.
- **The applicability domain should name phase, lead sponsor class, responsible-party type
  and expanded access.**
- `has_expanded_access` true posts at 73.3% (n=726) against ~37%. It is `editable` — added
  once a drug looks promising — so as a feature it may carry outcome information.
- The sponsor table has **no `not_recorded` row**: every drug trial in the denominator has a
  lead class, which corroborates the §0.3 restore from a second direction.
- Market window series 22,337 / 19,802 / 13,477 at 3 / 5 / 10 years — non-increasing as
  required. Market-only (no endpoint label) 16,696 of 22,337 (74.7%). Both-MeSH in the
  market-eligible cohort 75.9%, with an era gradient: pre-FDAAA 83.6%, FDAAA 77.2%, Final
  Rule 71.2%.

**Negatives, clarified.** Drug-only strict headline negatives this run: **5,591** (rev 7:
5,571). Rev 8's **7,589** is the all-interventional figure. The model trains on the drug
population, and on 13,493 of it after §12.19, so neither figure is the events count for the
boosting argument. §14 item 1.

---

## §13 — CORRECTION LEDGER, rev 9 additions

| # | what was wrong | correct reading |
| --- | --- | --- |
| 23 | `FLAG_THIN_TRAINING` attached by an endpoint clause alone; its "later-phase" comparison is false on phase 3. | Attached only by the FDAAA sentences. |
| 24 | The qualified clause said "this trial's primary endpoint is X" on trials that also registered an efficacy primary — 653 of 973 labelled. | Co-primary form, keyed by roll-up (§12.17). |
| 25 | ANY roll-up refused pharmacokinetic beside unreadable text, asserting a reading of the unread endpoint. | Undeterminable with a mandatory clause (§12.18). 91 labelled moved. |
| 26 | `RESPONSIBLE_PARTY_TYPES` held the old registry spellings, so `responsible_party_type_known` was False for every PI and sponsor-investigator trial. Nothing read it. | Canonical underscore tokens; both spellings fold. The label file's flag stays stale until the next **live** pull — **do not** re-derive with `--from-raw` (§0.3). |
| 27 | `population.UNKNOWN == "unknown"` equals AACT's normalised `UNKNOWN` agency class, so an absent lead and an AACT-unknown lead shared one bucket in `sponsor_agreement` and the posting-bias stratifier. | `SPONSOR_NOT_RECORDED` via `lead_sponsor_bucket()`. |
| 28 | `lead_sponsor_class_known` reads True for `unknown`/`ambig`: "known" means recognised token. Easy to misread as "the class is known". | Kept for drift detection; `classified_agency_class()` returns None for non-classes. Features use it. |
| 29 | AACT `has_single_facility` / `number_of_facilities` count duplicate rows. | 497 misread trials; use distinct sites (§12.21). |
| 30 | `fdaaa.REASON_DOC[phase_1_only]` claimed "the single largest source of confident NOT-APPLICABLE" (no regulated product is: 65,454 vs 31,969) and printed 16.9% (now 16.8%). | Numbers removed from the text. |
| 31 | `audit_posting_bias.py` printed 14,368 / 221,887 / 460,569 / 63.0% as fixed prose. | Derived from the run, or replaced by a pointer to a computed column. |
| 32 | Rev 8 §12.15: "nothing needs recomputing" read as independent agreement of two text fields. | The registered field is edited at posting (§12.22). The conclusion holds for the snapshot; its meaning changed. |
| 33 | Rev 8 §12.13 sandbox figures (12,986 / 967 / 415). | Project machine (pre-B2): plain 8,358, clause 973, mixed 65, undeterminable 4,597, declined 424, of 14,417 labelled. The sandbox folded undeterminable and mixed into "plain". |
| 34 | 7,589 negatives quoted as the boosting denominator. | All-interventional. Drug-only 5,591; the training population's figure is unprinted (§12.23). |
| 35 | `studies.source_class` assumed to be the lead sponsor's class. | The submitting organisation's (§12.21). |
| 36 | The gate's training exclusion lived in `endpoint_type`, the retrospective one in `eligibility` — two predicates for one population. | One predicate (§12.19). |

---

## §14 — NEXT ACTIONS, rev 9

Replaces rev 8 §14. Rev 8 items 1, 2, 4 and 7 are done; item 3 only in part (below).

1. **Print the strict negatives inside the 13,493** (endpoint-met eligible with
   `endpoint_met_strict == 0`), add it to `audit_posting_bias.py` §1, then re-make or drop
   the boosting argument on that number. Rev 7's ~9 events per variable is dead; rev 8's
   7,589 is the wrong population.
2. **Fix `build_endpoint_type_sample.py`**: duplicate-text collapse (rev 8 §0.1 item 5),
   §12.9 defaults (item 6), and the stale closing print.
3. **DrugCentral probe (rev 7 §8.1c)**: readout-to-approval lag, which confirms or kills the
   3-year market window. The posting-bias audit's own "still missing before a model".
   The entity file is restored and verified.
4. **Feature-build design**, before any model: which provenances each target admits;
   `editable_current_value` fields by name (start date, facilities, countries, expanded
   access); label encoders by name; `classified_agency_class`; distinct facility counts;
   both snapshot dates recorded.
5. Optional, cheap: on the three §12.22 trials, compare version 1 with the latest and look
   only at the primary outcome **title** — settles whether the gate's input text changes.
6. Optional: a fresh sample stratified on the dose-finding estimand/input split (rev 8
   §12.13). Unchanged.
7. **The rest of rev 8 §12.13's sandbox figures are still unconfirmed** — only the
   allowed/clause/refused split was re-run (§13 item 33). Still sandbox: false-refusal
   against the sponsor reference (75.9% → 69.2%) and its pharmacokinetic ceiling variant;
   all-dose-finding 881 and all-safety 3,684 trials; the 714 of 2,359 geometric-ratio
   over-refusals. Re-run on the project machine or retire them; do not quote them.
8. Small: the two items in §0.1.

---

## Lessons, 67–77

67. Make an unmerged input raise. Eligibility refusing a row without the first-submitted
    date caught a stale script on its first run; reading absence as "not excluded" would
    have admitted 600 retrospective trials silently.
68. Check a wording safeguard against the trial, not the class. A sentence true of every
    safety endpoint was false for 67% of the labelled trials carrying it.
69. A constant whose value equals a data token is a collision waiting. `UNKNOWN` was
    `"unknown"`, which is also an AACT agency class.
70. A drift flag only works if someone reads it. `responsible_party_type_known` had been
    False for every PI trial since AACT changed spelling.
71. A source's convenience fields inherit its defects. AACT's facility counts are row counts.
72. Byte-identity between two fields can mean one overwrites the other, not that they agree.
73. A bar nearly every stratum fails does not discriminate; find the decomposition that
    does. Posting rate flagged 8 of 9; the cliff separated them.
74. Two exclusion mechanisms in two modules is a denominator waiting to be computed wrong.
75. Numbers written into printed prose rot. Derive them or delete them — one was also false.
76. Retrospective registration is row-level provenance. No column classification can say it.
77. Copying the files is part of the change. Three times this session a file did not land;
    each was caught only because something failed closed — the test runner twice, an
    eligibility raise once. Say which files changed, and make absence loud.

---

## File inventory, rev 9

**New** — `src/trial_pos/services/aact_provenance.py`,
`src/trial_pos/services/aact_fields.py`, `tests/test_aact_provenance.py`,
`tests/test_aact_fields.py`, `scripts/pull_aact_fields.py`, `audit/audit_serving_flag.py`,
`audit/probe_aact_fields.py`, `audit/audit_registration_fields.py`, `docs/Handoff_rev9.md`.

**Modified** — `src/trial_pos/services/fdaaa.py` (`trial_flag`, thin-training rule, one
reason text), `src/trial_pos/services/endpoint_type.py` (co-primary clause, B2, training
exclusion and its decision record), `src/trial_pos/services/eligibility.py` (retrospective
registration, gate refusal, both merges), `src/trial_pos/services/population.py`
(responsible-party tokens, `AGENCY_NON_CLASSES`, `classified_agency_class`,
`SPONSOR_NOT_RECORDED`, `lead_sponsor_bucket`), `scripts/audit_posting_bias.py` (both
merges, derived prose figures), `tests/test_FDAAA.py`, `tests/test_endpoint_type.py`,
`tests/test_eligibility.py`, `tests/test_population.py`.

**Data, new on the project machine** — the four `trial_registration_*` files (§12.21).
`data/aact/smoke/` is disposable.

**Rev 8 was not in the last zip.** Check that `docs/` carries `Handoff.md` (rev 7),
`Handoff_rev8.md` and `Handoff_rev9.md` before handing over.