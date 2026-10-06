# Handoff — rev 8 supplement

**Read with rev 7, not instead of it.** Rev 7 remains the durable record for every section
this document does not name. Where the two disagree, rev 8 wins and says so.

Scope of this revision: the endpoint-type gate was unscored in rev 7 and is now scored,
revised and partly ratified by hand labels. Four bugs in the label pipeline were found and
fixed, three script bugs from §0.1 were closed, and one data-integrity incident destroyed
two inputs. Expected test count **718 passed, `GATE: GREEN`**.

---

## §0.3 — DATA INTEGRITY INCIDENT, read this before trusting any file

`--from-raw` was run to pick up a new column without checking what else that path writes.
`results_raw_studies.csv` carries `intervention_types` but **none of the one-to-many
aggregates** — the drug-name and MeSH columns in `ENTITY_FIELDS`, and the sponsor-class /
responsible-party columns — because those come from separate AACT tables
(`interventions`, `browse_conditions`, `browse_interventions`, `sponsors`,
`responsible_parties`) that the raw dump never captured.

`entity_record` found no source columns, produced 461,824 rows of blanks, and **overwrote a
populated `trial_entities.csv`** with them. The sponsor columns in the label file went to
`unknown` for every trial in the same run. Both losses printed as plausible findings:
entity coverage read `0.0%` on every source and the sponsor table read `neither 461,824
(100.0%)`, which looks like a fact about the data rather than a destroyed input.

**Fixed.** `pull_aact_results.py` now fails closed: `--from-raw` detects the missing
columns, writes nothing, and names the two ways forward. `--allow-missing-aggregates`
re-derives the label columns only and leaves the entity file untouched. Verified against a
172 MB copy of the real file — byte-identical before and after.

**The principle, as a lesson:** a re-derive that cannot reproduce part of its output must
not write that part. An empty file is indistinguishable from a real one to every downstream
reader; a missing file is obvious.

A live pull has since been run and both inputs are restored. **Any figure computed between
those two runs that touched entity coverage or sponsor class is void.**

---

## §0.1 — SCRIPT BUGS: the three from rev 7 are closed

1. **`validate_interval_rule.py` §5 caption claimed pre-fix data.** The file being read is
   the current post-fix `trial_labels.csv`, and pre-fix labels are not recoverable from it.
   The false caption is how the stale §3.1 breakdown survived two revisions — a reader
   checking the number saw a plausible table under a heading that explained away its
   disagreeing with the current one. Caption now reads "AS CURRENTLY LABELLED (post-fix)"
   and states that this is not a before/after comparison.

2. **§3.1 described the §2 design table as matched-coverage.** It is not: every validation
   row reaches it whatever its `ci_percent`, because the design question is asked before
   coverage is consulted. Fixed by stating the denominator in the output rather than by
   conditioning the table, which would have silently altered figures §3.1 already quotes.
   **§3.1's "matched coverage" wording is wrong and should be corrected in rev 7's text.**

3. **Three verdict-producing flags missing from the manifest.** `ratio_scale_floor`,
   `coverage_tolerance` and `as_of` are now in `MANIFEST_FIELDS` and written by the pull.
   `MANIFEST_FIELDS_ADDED_LATER` names them so a script can explain why an older manifest
   suddenly conflicts: those fields are **absent** from it, not different, so the settings
   behind its rows are unknown rather than matching. `--force-resume` would silence the
   message without answering the question.

### New in rev 8

4. **The endpoint clause is still not wired into `fdaaa.compose_flag`.** Rev 7 recorded
   this as empty by design, blocked on a kappa clearing `GATE_KAPPA_MINIMUM`. That
   threshold has been demoted (§12.11) and `GATING_CRITERIA` has now passed, so the
   precondition is met. **`CLASS_GATE` was flipped on the explicit condition that a clause
   accompanies the number, and nothing currently surfaces it to a user.** This is the most
   urgent open item: the flips are live and the safeguard they were approved under is not.

5. **`build_endpoint_type_sample.py` does not collapse duplicate endpoint text.** Its audit
   prints `rows collapsed, same outcome twice : 0`, which cannot be true —
   `build_confirm_sample.py` skips 14,252 exact text repeats on the same corpus. It is
   almost certainly collapsing on `(nct_id, design_outcome_index)`, which is unique by
   construction and therefore never collapses anything. **Its §2 per-class counts are
   inflated by roughly 3% and should not be quoted.** Third instance of this bug family.

6. **`build_endpoint_type_sample.py` predates §12.9's settled decisions.** Defaults are 5
   phase groups, 200 rows, 40 duplicates against §12.9's 3, ~100 and 12. See §12.12 on why
   none of those sizes produce a per-stratum verdict anyway.

---

## §12.10 — THE GATE IS SCORED. Results, and which are authoritative

Two hand-labelling exercises were run, blind, by the project owner. **These are the only
authoritative figures in this section.** Everything in §12.13 marked *sandbox* is a
diagnostic computed off-machine and must be re-run before it enters the record.

### The six-class carving is RATIFIED (25 endpoints, blind, `scheme_sheet.csv`)

The labeller was asked to name, in their own words, what kind of thing each endpoint is —
no vocabulary offered, because the existence of a natural vocabulary was the thing under
test. They reproduced **five of the six classes unprompted**: "pharmacokinetic
measurement" / "pk measurement" eight times, "max tolerated dose, phase I endpoint" four
times, "safety measurement" five times, plus efficacy and bioequivalence.

§12.7 says no automated reference can reach this question, because if the carving is wrong
every automated reference agrees with the rule and reports a clean result. It came back
clean. **The scheme survives and this question is closed.**

### The class-to-gate MAPPING failed, for exactly two classes

| class | gate at rev 7 | hand answer |
| --- | --- | --- |
| pharmacokinetic | refuse | no × 8 — correct |
| efficacy_shaped | allow | yes × 3 — correct |
| bioequivalence | allow | yes × 2, unclear × 1 — correct |
| other | allow (undeterminable) | yes × 2, unclear × 1 — correct |
| **dose_finding** | **refuse** | **yes × 3 — WRONG** |
| **safety_tolerability** | **refuse** | **yes × 4**, no × 1 — **WRONG** |

Binary collapse: raw 0.696, kappa 0.439, and **7 over-refusals against 0 under-refusals** —
entirely one-directional, which §12.5 disqualifies at any kappa.

### The confirming sample (86 presentations / 80 fresh endpoints, `confirm_sheet.csv`)

Drawn after the bars were pre-registered, excluding by `nct_id` every trial seen in the
ratification. Both mappings scored on the identical rows.

| mapping | n | raw | kappa | allowance precision | false-refusal share | over | under |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shipped | 74 | 0.284 | 0.053 | 100.0% | **84.1%** | 53 | 0 |
| candidate | 74 | 0.959 | 0.834 | 98.4% | **18.2%** | 2 | 1 |

Per class: dose_finding 25/26 have an answer, safety 26/28, pharmacokinetic 2/17,
efficacy_shaped 11/15. Both **tested** classes cleared `MIN_STRATUM_FOR_VERDICT`; the two
controls did not and carry no verdict of their own. Self-consistency **7/7** repeated pairs.

**Shipped fails on false-refusal. Candidate passes both bars.**

### What was applied

`CLASS_GATE` now reads:

```
bioequivalence       applicable
dose_finding         applicable   <- flipped, QUALIFIED
pharmacokinetic      not_applicable
safety_tolerability  applicable   <- flipped, QUALIFIED
efficacy_shaped      applicable
other                undeterminable
```

**Pharmacokinetic is the only refusing class.** It survived on replication: answerless 8 of
8 in the ratification and 15 of 17 in the confirming sample, on disjoint endpoints. That is
the strongest single finding in the exercise.

---

## §12.11 — THE DECISION BAR CHANGED, and why that is not rationalisation

`GATE_KAPPA_MINIMUM = 0.60` is **demoted from gating threshold to reported diagnostic.**
The value is unchanged and pinned by a test.

The ratification scored 0.439 against it. **That is not the reason.** The reason is that
0.60 was never connected to anything that ships: nobody has shown a gate at kappa 0.60
produces a better model than one at 0.45, or than no gate at all. The number was chosen as
a plausible agreement level before any class prevalence was known. The argument does not
depend on which way the figure came out — it would hold identically at 0.85, which is what
distinguishes demoting a threshold from lowering one.

What gates instead, both pre-registered before the confirming sample was drawn:

```
MIN_ALLOWANCE_PRECISION = 0.90
MAX_FALSE_REFUSAL_SHARE = 0.25
GATING_CRITERIA = ("allowance_precision", "false_refusal_share")
```

**Two asymmetric criteria, not one symmetric kappa, and not their average.** A false
allowance puts a success probability on a trial for which "success" has no defined meaning
— an authoritative-looking number nothing downstream can detect. A false refusal withholds
an answer that existed, which costs coverage and is at least visible. So allowance
precision carries the tighter bar. Kappa cannot express this; averaging the two would
discard the asymmetry that motivated having two.

The values were chosen to **discriminate** rather than to be passed: on the ratification
rows the shipped mapping scores allowance precision 1.00 (passes) and false-refusal 0.47
(fails), the candidate roughly 0.93 and 0.00 (passes both). A bar both clear, or neither,
is not a test. They **cannot be optimised** — there is no outcome to optimise against,
which is the same reason kappa was demoted. A sweep after labelling is a diagnostic only.

### The downstream test, pre-registered while no model exists

```
TRAINING_EXCLUSION_PREDICTED_DIRECTION = "improve"
TRAINING_EXCLUSION_MIN_EFFECT = None        # not yet decidable
SERVING_GATE_HAS_NO_OUTCOME_REFERENCE = True
```

Train with gate-refused trials included, train with them excluded, compare held-out
performance. If "did this trial meet its primary endpoint" is close to meaningless for an
MTD trial, that label is close to noise and including it should measurably hurt. If the two
models are indistinguishable, the gate is not earning its complexity on the training side,
and that is a finding.

The effect size is `None`, not a placeholder: unknown is not zero, and 0.0 would read as
"no improvement required" rather than "not yet decidable". **Set it before running the
comparison, not after.**

**The serving half can never be tested this way.** If a trial's primary endpoint is a
maximum tolerated dose there is no ground truth for "did it meet its endpoint", so whether
refusing to score it was right cannot be checked against what happened. For that half human
judgment is not a stopgap — it is the only possible reference, permanently.

---

## §12.12 — THE QUALIFIED FLIP, and why 50 hand labels were enough

The flips are **applicable with a mandatory clause**, not bare applicable. Amended before
scoring, and the amendment makes the test harder rather than easier.

The gate is binary — show a number or do not — and that is the wrong shape for what the
labels found. A dose-escalation trial's success means a tolerable dose was identified; a
comparative-safety trial's means the arms differed acceptably. Both are real answers and
neither is what a user reading "68% chance of success" assumes. Silence misleads one way,
an unqualified number the other. `CLASS_SUCCESS_MEANING` holds one sentence per qualified
class, `requires_clause(record)` lets a caller assert it is present rather than discover it
missing, and qualified is checked **before** mixed in `endpoint_clause` because what success
meant serves a reader better than how many endpoints were excluded.

**This is what makes 50 hand labels adequate.** A bare flip would ask those rows to license
silently showing thousands of users a number whose meaning nobody stated. The qualified
flip asks them to license showing a number together with its meaning — a far smaller claim.
Given that the serving half has no possible outcome reference, reducing the cost of being
wrong is the only available risk control.

### Sample size arithmetic, which supersedes §12.9's row counts

`MIN_STRATUM_FOR_VERDICT = 20`. Three phase groups × six classes is 18 strata, so **§12.9's
indicative ~100 rows and the sampler's 200 default both clear the verdict threshold in NO
stratum.** 18 strata above 20 needs ~360 rows. The ratification narrowed the question to two
classes, so the confirming sample was scoped to them plus controls:

```
CONFIRM_SAMPLE_PER_TESTED_CLASS = 25      # dose_finding, safety_tolerability
CONFIRM_SAMPLE_PER_CONTROL_CLASS = 15     # pharmacokinetic, efficacy_shaped
```

80 rows, both tested classes above the floor. It does **not** support a six-class kappa, a
per-phase figure, or any statement about bioequivalence or other.

### Phase grouping: settled

**phase1 / phase2+pivotal / post_approval+unknown.** Chosen on within-stratum homogeneity —
phase2 and pivotal are near-identical on endpoint type, and post_approval and unknown
likewise. Pivotal remains reportable post hoc: it is 68,904 outcomes against phase2's
94,238, so roughly 42% of the merged stratum's rows land there by construction.

---

## §12.13 — SANDBOX DIAGNOSTICS, not yet re-run on the project machine

Every figure in this subsection was computed off-machine. **Do not quote them without a
confirming run.**

- Baseline false-refusal against the sponsor reference fell **75.9% → 69.2%** from the
  flips alone. The `pharmacokinetic_applicable` ceiling variant drives over-refusal to 0
  and under-refusal to 819, which confirms pharmacokinetic should keep refusing.
- Of 14,368 headline-labelled drug trials: 12,986 allowed plain (90.4%), **967 allowed with
  a clause (6.7%)**, **415 refused yet labelled (2.9%)**.
- Trials whose every posted primary is dose_finding: 881, of which 874 are tier D and 7
  headline. All-safety: 3,684, of which 3,554 tier D and 107 headline.
- Geometric-ratio over-refusals before the reference was corrected: 714 of 2,359.

### The 415 are an open incoherence

Those trials carry usable labels, so they are training-eligible, and at serving time the
gate declines to score a trial like them. **The model would learn from trials it then
refuses to answer about.** Two defensible resolutions — exclude them from training, or treat
a headline label as overriding the gate — and they are different claims about which signal
is trusted. Recommendation: exclude and record why, because a label you would refuse to show
is a label you do not trust. **Not yet decided.**

### Recorded as a hypothesis, deliberately not acted on

`DOSE_FINDING_SPLIT_UNTESTED` — the dose-finding class conflates two things:

- **the estimand**: "Maximum tolerated dose (MTD)", "Recommended phase 2 dose". The trial
  set out to produce a dose and either did or hit the stopping rule where every dose is too
  toxic. Pass/fail exists.
- **the input**: "Number of participants with a Dose Limiting Toxicity". The measurement
  that *locates* the dose. The phase 1 target DLT rate is conventionally 20–33%, so zero
  DLTs is not success (escalation stopped too low) and 60% is not drug failure (that dose is
  above the MTD). There is no direction in which a DLT count is "met".

The labeller answered yes to 9 of 10 input-type rows, which on their own stated criterion is
arguably wrong. **Not acted on: the split was derived from the same 10 rows that would test
it.** A fresh sample stratified on the distinction would settle it. The qualified clause is
accurate for both halves, so the flip is robust to the split being real — which a bare flip
would not have been.

---

## §12.14 — THE SPONSOR-ANALYSIS REFERENCE: built, and its bias is now known

`services/sponsor_threshold.py` derives a tri-state sponsor verdict from the tier
machinery per §12.9 decision 3, with 100 tests. It is independent of the **keyword rule**
and not of the label pipeline, and never validates the label machinery.

**It has a systematic bias that the hand labels exposed, and this bounds every figure
computed from it.** The reference reads a posted p-value as "this endpoint was
threshold-tested". For pharmacokinetic endpoints that inference is wrong — a sponsor can
test whether exposure differs between arms without exposure being a success criterion. The
reference named pharmacokinetic the largest over-refusal class; the hand labels called that
mapping correct 8 of 8 and then 15 of 17. **The reference therefore overstates over-refusal
in a known direction and cannot be the decision bar.** `HAND_ANSWER_TO_VERDICT` maps hand
labels into the same three-state vocabulary so both references run through the same scoring
code, and the module states that where they disagree the hand label governs.

### Two denominators, and why the gating one is the refusals

`false_refusal_share` divides by the refusals; `over_refusal_rate` divides by every decided
unit. The second falls whenever the rule refuses **less often** — a rule refusing nothing
scores a perfect 0.0 on it while gating nothing at all. §12.9 decision 4 asks whether the
refusing classes are drawn wrongly, which is a question about the refusals, so the
denominator is the refusals. `test_a_rule_that_refuses_less_scores_better_on_the_pooled_rate_only`
pins the argument.

---

## §12.15 — REGISTERED vs POSTED TEXT: §12.2's blocker does not exist

`design_outcomes.measure` and `outcomes.title` are **byte-identical on all 17,859
overlapping drug trials** — identical class sets, identical text sets, 2.10 primaries per
trial on both sides. `audit/audit_text_sources.py`, project machine.

So §12.2's warning — that a gate validated on results-side titles could not be deployed
against registered text — is empirically void wherever both fields exist. **Every
endpoint-type figure computed on posted titles is already the registered-text figure, and
nothing needs recomputing.**

Two caveats. First, it cannot be determined from AACT whether the fields match because
ClinicalTrials.gov stores one text or because AACT derives one from the other; the practical
conclusion is the same but the confidence differs, and a manual spot-check of three NCT ids
against the registry would settle it. Second, the registered field covers 216,197 drug
trials against 53,309 that posted — the ~163,000 that never posted are where the gate does
nearly all its work, are unreachable by any sponsor reference, and are why the hand labels
were necessary.

`design_outcomes.measure` max length is 255 characters, which is a `varchar` boundary rather
than a natural maximum; **61 of 932,714 rows (0.007%) sit at it**, so truncation is not a
material concern.

---

## §13 — CORRECTION LEDGER, rev 8 additions

| # | what was wrong | correct reading |
| --- | --- | --- |
| 15 | Geometric mean ratios produced **inverted labels**. `met_from_ci` tests exclusion of 1; a successful bioequivalence or equivalence study's interval *contains* 1. Reading containment as exclusion errs **both** ways, so interval position carries no information about which error was made. | New refusal `geometric_ratio_equivalence` → tier E, keyed on the statistic's **name**, not the interval. **Zero** geometric-ratio rows now reach tier C. Project machine: 2,190 analysis rows / 291 trials. |
| 16 | `coverage_mismatch` dropped from 1,222 rows / 299 trials to **544 / 147** as a side effect of #15 — geometric ratios previously coverage-refused now refuse earlier as design-refused. Unanticipated. | Correct: coverage only matters if exclusion-of-null were the right test. The refusal's justification (error direction flipping, tighter 0.01 / looser 17.62) still holds. |
| 17 | `percent_scaled_ratio` precedence. Most percent-scaled rows are **also** geometric ratios, so putting geometric first would have absorbed that refusal and destroyed its independent evidence base (n=296, kappa 0.000, one-directional). | Geometric sits **below** percent_scaled. The latter keeps all 1,341 rows / 226 trials unchanged. |
| 18 | The geometric refusal shipped **with no count column**. Three call sites hardcoded the three kinds that existed, so 2,190 refused rows reported zero everywhere, visible only via `endpoint_na_reason` and only for the 212 trials where every analysis refused. | `REFUSAL_COUNT_FIELD` maps every kind to its column; `aggregate_trial`, the output columns and the accounting block all derive from it. New column `n_analyses_geometric_refused`. |
| 19 | The same geometric correction was applied in `endpoint_label` and **not propagated** to `sponsor_threshold`, which kept reading those rows as "the sponsor threshold-tested this". | Inflated the over-refusal cell by 714 of 2,359 (sandbox). One correction became two bugs; the coupling is now pinned by a test. |
| 20 | `CLASS_BIOEQUIVALENCE` refused at the gate. A BE study **does** have a pass/fail endpoint — containment inside 0.80–1.25. The gate was covering for a label-layer bug, at the wrong layer. | Flipped to applicable. It was also nearly inert: it caught **6 of 862** BE-shaped analysis rows, because the gate reads registered text while the analysis shape lives in posted statistics. |
| 21 | §13's 152 phase-3 all-PK headline trials. | Recomputes to **60** on `PHASE3` exactly and **63** on the pivotal group with these patterns. A difference between two rules, not a bug in either — which is §13's own point. **Do not reconcile by adjusting a pattern.** |
| 22 | `pull_design_outcomes.py` died on `can't use a named cursor outside of transactions`. A server-side cursor needs a transaction; that script sets `autocommit=True` while `pull_aact_results.py` does not — which is why one pull worked and the other never had. | Autocommit toggled off around the stream and restored in a `finally`, rather than dropped from the connection, so a long chunked pull does not hold a snapshot on a shared read-only server. |

---

## §14 — NEXT ACTIONS, rev 8

Ordered. Items 1–3 are small and unblock the rest.

1. **Wire the endpoint clause into `fdaaa.compose_flag`.** §0.1 item 4. The flips are live
   and the safeguard they were approved under is not. Highest priority.
2. **Settle the 415 trials** the gate refuses that carry headline labels. A decision, not a
   build. Recommendation in §12.13.
3. **Re-run the sandbox diagnostics in §12.13** on the project machine so they can enter
   the record.
4. **Extra AACT fields**, with per-column provenance marking — *knowable at registration*
   versus *post-hoc*. `designs`, `eligibilities`, `sponsors` class and `browse_*` are
   registration-time and safe; `calculated_values` is mixed and `actual_duration` is
   **leakage** (a trial stopped for futility has a short duration, so duration encodes
   failure). Pull liberally, gate at feature-build time, and record provenance per column or
   every extra field is a liability nobody downstream can assess. This is also how §R7
   becomes an assertion rather than a printed reminder. **A read-only cardinality probe
   first** — a one-to-many join that silently multiplies rows is invisible until a count is
   wrong three steps downstream.
5. **Fix `build_endpoint_type_sample.py`** — the duplicate-text collapse (§0.1 item 5) and
   the §12.9 defaults (item 6).
6. **DrugCentral probe (§8.1c)**, which delivers the readout-to-approval lag distribution
   that confirms or kills the 3-year market window — currently an assumption holding up a
   whole target. Needs the restored entity file.
7. **Posting-bias audit (Step 6.3).** Unchanged from rev 7, and still before any modelling.
8. **Optionally: a fresh sample stratified on the dose-finding estimand/input split**
   (§12.13), which is the one open endpoint-type question.

The negative-class recount is worth re-reading before item 7: **7,589 strict headline
negatives** against ~40 features. Rev 7's argument against boosting rested on ~369
negatives and ~9 events per variable. **That argument is dead on its own terms** and must be
re-made on other grounds or dropped.

---

## Lessons, 54–66

54. A re-derive that cannot reproduce part of its output must not write that part. An empty
    file is indistinguishable from a real one downstream; a missing file is obvious.
55. Two scripts with the same code pattern can differ by one connection flag, and reading
    either alone tells you nothing. The named-cursor bug was invisible in both files.
56. A stop rule's denominator decides what it can detect. Dividing a cell by every decided
    unit rewards a rule that acts less often rather than one that acts more accurately.
57. A refusal added without a count column is a refusal that vanishes. Derive the columns
    from the kind vocabulary so a new kind cannot be added without one.
58. A correction applied in one module and not its sibling becomes two bugs. Pin the
    coupling with a test that iterates the vocabulary.
59. A boolean one-sidedness flag cannot see an 8:1 imbalance. Report the ratio beside the
    flag rather than replacing an established convention.
60. A negative result from a diagnostic fitted to its own reference is trustworthy; a
    positive one is not. Attribution is the sound reading, confirmation is not.
61. `str.replace` without an assertion is a silent no-op. Twice this revision, once shipping
    a `NameError` as two failing tests.
62. Appending joined rows per row instead of keying on the entity's id duplicates every
    multi-child entity. It produced a spurious 100.0% agreement across 17,859 trials, and a
    perfect diagonal was the symptom rather than the result. Three instances this revision.
63. Demoting a threshold on the argument that it was never linked to the outcome is a
    different act from lowering it because the rule failed it. The test is whether the
    argument would hold had the number come out the other way.
64. When a measurement can only be taken on a selected 4% of a population, the figure is
    the error rate *where measurable* and not the error rate. Say which.
65. A diagnostic variant that becomes identical to its baseline must be retired, not kept.
    A measurement that cannot come back different is not a measurement.
66. Where a gate's behaviour can never be validated against outcomes, reducing the cost of
    being wrong is the only available risk control — which is why a qualified allowance
    needs far less evidence than a bare one.

---

## File inventory, rev 8

**New** — `src/trial_pos/services/sponsor_threshold.py`,
`src/trial_pos/services/scheme_probe.py`, `tests/test_sponsor_threshold.py`,
`tests/test_scheme_probe.py`, `scripts/build_scheme_sheet.py`,
`scripts/build_confirm_sample.py`, `audit/falsify_endpoint_type.py`,
`audit/probe_scheme_repairs.py`, `audit/probe_be_inversion.py`,
`audit/audit_text_sources.py`.

**Modified** — `src/trial_pos/services/endpoint_label.py` (geometric refusal, refusal count
fields), `src/trial_pos/services/endpoint_type.py` (flips, qualified clause,
pre-registration), `src/trial_pos/services/resume.py` (manifest fields),
`scripts/pull_aact_results.py` (manifest, refusal columns, from-raw guard),
`scripts/pull_design_outcomes.py` (cursor fix), `scripts/validate_interval_rule.py`
(captions), `tests/test_endpoint_label.py`, `tests/test_endpoint_type.py`,
`tests/test_resume.py`.

**`audit/` is a new folder** for scratch diagnostics and probes. The older `audit_*.py`,
`validate_*.py`, `probe_ctgov.py` and `inspect_data_dir.py` remain in `scripts/` because
rev 7 names them by path; moving them needs a matching text edit.

`scripts/score_endpoint_type.py` was never written and is **no longer needed** —
`build_confirm_sample.py --score` enforces `GATING_CRITERIA` against hand labels, which is
what the 0.60 kappa gate was supposed to do and could not.