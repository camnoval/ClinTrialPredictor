# Handoff: clinical-trial success predictor — combined (rev 7 to rev 11)

All five handoff files, unedited, in one place, newest first. This file replaces
`Handoff.md`, the rev 8 and rev 9 supplements, `Handoff_rev10.md` and `Handoff_rev11.md`.

**Precedence:** a later revision supersedes an earlier one wherever they disagree. Read
Part 1 (rev 11) first and in full; it lists which rev 10 claims were verified and which
were refuted (rev 11 §3), and its §2 is the dated decision log (D-1 onwards). New
decisions are appended to Part 1 §2.

**Contents**
- Part 1: rev 11 (2026-10-07) — read first; supersedes everything below where they disagree
- Part 2: rev 10 — supplement
- Part 3: rev 9 — supplement
- Part 4: rev 8 — supplement
- Part 5: rev 7 — the base handoff



---
---

<!-- ===== Part 1: rev 11 (2026-10-07) — read first; supersedes everything below where they disagree (was: Handoff_rev11.md) ===== -->

# Part 1: rev 11 (2026-10-07) — read first; supersedes everything below where they disagree

# Handoff rev 11 — supplement, started 2026-10-07

Read this FIRST, then rev 10, rev 9, rev 8, rev 7 (§13, §12.9, §8). Rev 11 supersedes earlier
revisions wherever they disagree. It is a living file: every decision is appended to §2 the
day it is made, with its date and the evidence it rests on.

**Figures rule.** Every number below comes from a run on the owner's machine and names the
output file it came from. Numbers marked *(arithmetic)* are computed by hand from such
figures. Sandbox runs (synthetic data) produced no figures here.

**On rev 10.** Rev 10's later sections were found unreliable in places (§3). Do not quote a
rev 10 figure that §3 does not list as verified.

---

## 1. State

- **2026-10-19 (training-design delivery, `training_design.zip`):** gate **1062 passed**
  (1049 + 9 `test_temporal_split.py` + 3 in `test_advancement_label.py`). Labels unchanged:
  the advancement refactor reproduces the frozen label (a test, and the probe's own check).
  Then run `audit\probe_training_design.py` (§5). Decisions D-54 to D-57 are in §2.
- **2026-10-13 (labels delivery, `labels_all.zip`):** gate **1049 passed**. Then, in order:
  1. `resolve_trial_drugs.py --overwrite` (v10: learned codes on, D-50; Purple Book
     identity key changed, D-49);
  2. `match_form_approvals.py --overwrite`;
  3. `build_targets.py --snapshot 2026-10-01 --overwrite`, now with ADVANCEMENT (D-51).
  All three targets are then in `trial_targets.csv`.
- **2026-10-13 (market-fix delivery 2, `market_fix2.zip`):** gate **1036 passed**. Then, in
  order:
  1. `resolve_trial_drugs.py --overwrite` (v9: D-44 and D-46 change resolution);
  2. `match_form_approvals.py --overwrite`;
  3. `build_targets.py --snapshot 2026-10-01 --overwrite`;
  4. `audit\probe_learned_codes.py` (D-47 review; changes nothing).
- **2026-10-13 (market-fix delivery 1, `market_fix1.zip`):** gate **1024 passed**. Then, in
  order: `resolve_trial_drugs.py --overwrite` (v8: D-43 changes resolution);
  `audit\probe_resolver_other_names.py` on v8; `match_form_approvals.py --overwrite` (now
  needs `data\purplebook\Advanced_Search.csv`); `build_targets.py --snapshot 2026-10-01
  --overwrite`.
- **2026-10-13 (third delivery):** gate **1016 passed** expected once `targets.zip` is
  installed on top of `form_matcher_fix.zip`. Then run `scripts\build_targets.py
  --snapshot 2026-10-01` (§5).
- **2026-10-13 (second delivery):** gate **1005 passed** expected once
  `form_matcher_fix.zip` is installed on top of `form_matcher.zip` (997 with
  `form_matcher.zip` alone, 967 before it). Report any other count first.
- Resolver v7 has been run (`resolve_trial_drugs_20261001_v7.txt`). Rerun only
  `match_form_approvals.py` after `form_matcher_fix.zip`; the resolver need not run again.
- **2026-10-13:** `data/labels/trial_drug_agents.csv` must be regenerated (v7) after
  `form_matcher.zip`: hydrate words are no longer salts and release codes are no longer
  forms (D-28). Drug ids, agent statuses and match classes cannot change; only the form
  columns do. `match_form_approvals.py` refuses a v6 file.
- Gate history: 967 passed once `partial.zip` (2026-10-07) is installed on top of
  `cleanup.zip`; 964 with cleanup alone.
- **Every `data/labels/trial_drug_*` file written before `idfix.zip` is WRONG for drugs whose
  DrugCentral structure is a salt child (D-19). Regenerate before any use.**
- The pinned AACT tier (rev 10) is unchanged and was re-verified (§3).
- New this session: a row-level AACT pull (`rows__*.csv`), trial-to-drug resolution
  (first version run), and the design decisions in §2.
- Built, not yet run on the owner's data: the drug-id fix and guards (D-19 to D-21, §5).
- **Not yet done:** rerun of the resolver with the redesign, the resolution sample draw,
  Task 2 (lag), and everything after (rev 10 §14 items 3–9).

---

## 2. Decision log

Each entry: decision, date, evidence. Later entries override earlier ones where they
conflict; overridden text is kept and marked.

**D-1 (2026-10-07) Row-level pull, scope (b).** Pull every registration-side AACT table row
by row, keyed on the child's own id. Results-side tables (baseline, milestones, withdrawals,
reported events, participant flows) excluded: post hoc, unknown at prediction time.
Contact tables (names, phones, emails) and AACT's internal search tables excluded.
Already-pulled tables are not re-exported (the one-per-trial tables in the fields file;
`design_outcomes`). Evidence: `probe_intervention_arms_20261001.txt` showed the arm links
exist, are clean, and were read by no pull; `trial_entities.csv` pools every MeSH type.

**D-2 (2026-10-07) Tested agent.** A drug/biological intervention in ≥1 EXPERIMENTAL arm and
in no comparator arm (active, placebo, sham). Trials with no arms: all drug interventions,
placebo removed, on a separate route (`no_arms_fallback`) so the sample measures it.
Undeterminable (never counted as unmatched): no experimental arm; every experimental drug
also in a comparator arm; experimental arms carry no drug; no drug linked to an arm; only
placebo left. Pure placebo/sham/vehicle/dummy/saline dropped; "X or placebo" keeps X.

**D-3 (2026-10-07) MeSH.** Term text, not ids (AACT holds no MeSH ids anywhere). MeSH only
corroborates a match; it never creates one, because it cannot say which arm a term belongs
to. Evidence: 51.4% of indexed terms match no intervention name
(`probe_intervention_arms_20261001.txt`); comparators dominate the MeSH-named misses
(`probe_unresolved_20261001.txt` §2).

**D-4 (2026-10-07) Application route.** Drugs@FDA `Products` brand and ingredient names,
linked by ApplNo to ob_product; for BLAs (absent from the Orange Book) the ingredient name
resolved against DrugCentral. Drugs@FDA ingredient strings split on `;` and `||` only, never
on commas.

**D-5 (2026-10-07) No fuzzy matching.** Every step is a deterministic rewrite, and every
match records its route (name used, whole/components, step, source).

**D-6 (2026-10-07) Combinations.** Unit is (trial, tested agent). A trial is matched when **at
least one** tested agent resolves.

**D-7 (2026-10-07) Labelling.** Blind: the sheet carries no resolver output and no stratum.
The labeller records each tested agent, its DrugCentral id, and FDA approval. CBER-only
approvals are their own row, outside the gate's recall. *Amended by D-12: "approved" means
the specific form tested.*

**D-8 (2026-10-07) Sample.** Frame: market-eligible pivotal trials at W=3. Strata: matched /
unmatched (undeterminable goes with unmatched). Salted-hash order per stratum fixed at draw
time; tranche 2 continues down the same order. Stopping on counts, never rates. Wilson for
precision; recall as a stratified ratio with Wilson on the effective n. Expected labelling
load 250–350 trials.

**D-9 (2026-10-07) New molecule.** The drug has no NDA or BLA original approval before the
readout; ANDAs never count. FDA's NME class is not used (only 279 of 503 approved BLA
originals carry it *(arithmetic, `probe_source_files_20261001.txt` §4d)*).

**D-10 (2026-10-07) Lag, follow-up and decision line.** Only readouts with ≥H years of
follow-up before 2026-10-02; H=10 headline, H=5 sensitivity. Decisive figure per drug
(earliest pivotal readout). If p90 lands between 3 and 5 years, the headline window moves to
W=5 and W=3 becomes the sensitivity.

**D-11 (2026-10-07) Resolver fixes A, B, C (approved, with guards).**
- A. Dictionary-side salt stripping ('fludarabine' -> 'fludarabine phosphate'), DrugCentral
  names only, unique hits only, after every forward step. Guards: the query spelling must
  carry no salt word (no salt swaps); bare elements never match; sibling esters collide.
- B. Formulation qualifiers stripped for MATCHING: liposomal, unfractionated, transdermal,
  low dose, high dose (phrases). Never 'pegylated'; never 'low' alone.
- C. Commas split registry names, last resort only, never between digits.
- Rejected: salt+form stripping on the dictionary side (produced 'pcv24 vaccine ->
  ximelagatran', 'ALKS 5461 -> diroximel fumarate', `probe_unresolved_20261001.txt` §1).
- *Amended by D-12 to D-15: stripping finds the drug; it must never erase the form.*

**D-12 (2026-10-07) Salts and formulations are different products.** Metoprolol succinate
(Toprol-XL, 1992) and tartrate (Lopressor, 1978) are separate approvals with different
indications; trials compare one form against another. **"Approved" means approval of the
specific form the trial tested.** Evidence (`probe_salt_forms_20261001.txt`,
`probe_form_vocab_20261001.txt`):
- DrugCentral ids cannot carry the form: 3,464 salt-bearing synonyms point at the moiety;
  only 318 salts have their own id. Metoprolol succinate/tartrate/fumarate are all id 1786;
  'liposomal doxorubicin' is a synonym of doxorubicin (960).
- Drugs@FDA dates salts apart through the ingredient string, and formulations apart only
  through `Products.Form` (dosage form ; route): Doxil is 'doxorubicin hydrochloride /
  INJECTABLE, LIPOSOMAL' (1995) vs 1974; Exparel 'bupivacaine / INJECTABLE, LIPOSOMAL'
  (2011); Concerta 'TABLET, EXTENDED RELEASE' (2000) vs Ritalin 1955; vedolizumab SC
  (BLA 761359, 2024) vs IV 2014; Baqsimi nasal 2019.

**D-13 (2026-10-07) Agent identity = moiety + stated form.** Moiety: the DrugCentral id
(grouping, features). Form: the salt and the formulation/route the trial states, where it
was found, and whether matching had to strip it. The market label (later) dates the specific
form from NDA/BLA products whose ingredient string and Form are compatible.

**D-14 (2026-10-07) D1: an unstated form means the moiety.** When the trial states no salt
and no formulation, any approved form of the moiety counts. Evidence: 90.2% of matched agents
state no form; for 65.7% *(arithmetic)* of those the moiety has ≥2 approved products
(`probe_form_vocab_20261001.txt` §3). Known cost: a trial testing a new form of an old
moiety under the bare moiety name (e.g. 'vedolizumab' for the SC form) is judged at moiety
level.

**D-15 (2026-10-07) D2: the form is read from the intervention name and other names only.**
Not from the intervention description: cleaner, fewer false positives. Evidence: a
description states a formulation or route word the name does not in 25.9% of unstated agents,
mostly 'iv' and 'intravenous' (`probe_form_vocab_20261001.txt` §4), and descriptions also
describe comparators and schedules.

**D-16 (2026-10-07) Working practice.** Every delivery shows every changed file
individually, with a plain-language note per file, plus a zip at repo paths for installing.
Decisions are written here the day they are made.

**D-17 (2026-10-07, proposed by the assistant, to be confirmed) The labeller records the
TRUE form.** The labelling sheet asks for the form as the registry page describes it
anywhere (name, description, arms), not only in the names. D-15 limits only what the
resolver reads; scoring against the true form then measures what D-14 and D-15 cost.

**D-18 (2026-10-07) Form vocabulary (implementation of D-13).**
- Salts: the trailing salt words the name states, in one spelling each (`hcl` ->
  `hydrochloride`, `mesilate` -> `mesylate`, `sulphate` -> `sulfate`). 'free', 'base',
  'anhydrous' are not salts. A drug that is itself a salt states none ('sodium chloride').
- Formulation/route: 22 classes (`drug_names.FORM_CLASS_WORDS`: extended/delayed/modified
  release, orally disintegrating, liposomal, lipid complex, IV, SC, IM, oral, topical,
  transdermal, nasal, inhalation, ophthalmic, intrathecal, intravitreal, vaginal, rectal,
  sublingual, buccal, implant). Short tokens match whole words only ('iv' never inside
  'ivermectin'). Classes, not words, are what the market label maps onto Products.Form.
- Form handling per match: `no_form_stated`, `stated_form_kept`, `stated_form_stripped`,
  `salt_inferred` (`drug_resolution.FORM_HANDLINGS`).
- A trial whose tested moiety is also a comparator moiety is flagged
  (`tested_moiety_in_comparator`, tri-state: unknown when a comparator is unresolved).

**D-19 (2026-10-07) BUG FIXED: struct2parent's parent ids are not structure ids.**
`struct2parent.parent_id` references DrugCentral's `parentmol` table, not `structures`. The
resolver had replaced every salt-child structure id with that parent id, conflating
unrelated drugs whose structure id equalled a parentmol id. Seen in
`resolve_trial_drugs_20261001_v3.txt`: 'fludarabine -> amfetamine' (968 agents),
'abiraterone -> amifostine', 'mycophenolate mofetil -> acyclovir', 'capecitabine ->
aminorex', 'sacubitril -> alphaprodine', every insulin -> one id (178), bare ids with no
structure name (8, 41, 233). Fix: drug ids are structure ids, never remapped; the
struct2parent link is kept only as a namespaced PARENT GROUP (`pm<id>`), used to treat two
salts of one parent as one moiety in the comparator flag. `parentmol` is not extracted, so
parent groups have no names. Consequences, all corrected below:
- every resolver output before the fix is wrong for salt-child drugs (§1);
- the probe pairs 'dalteparin -> bemiparin' and 'clobetasol -> altizide' were this bug,
  not Orange Book pooling; D-11's stated reason for restricting A to DrugCentral names was
  wrong (the restriction stays: it is still the cleaner source);
- `probe_salt_forms_20261001.txt` §1 "parent with salt children 160" counted structures
  whose id equals a parentmol id: meaningless. "Salt child 318" stands.

**D-20 (2026-10-07) Abbreviations match only as an exact whole intervention name.** A key of
≤3 letters (compact, letters only), or 4 letters written in capitals by the trial, never
matches as a component, a parenthetical, an other name, or after any rewrite. Evidence
(v3 route review): 'albuterol … mcg/inh' -> isoniazid, 'balstilimab (bal)' -> dimercaprol,
'dv, rc48' -> dienestrol, 'adcc & tace' -> chlorotrianisene, 'ats' -> erythromycin. Codes
with digits ('5-fu') stay usable; lowercase drug words ('iron', 'zinc') stay usable.
Inhaler device words (dpi, mdi, pmdi) added to the form vocabulary.

**D-21 (2026-10-07) The biosimilar suffix comes off biologics only.** Stripped only when the
remainder contains a word with a biologic stem (-mab, -cept, -ase, -kin, -stim, -poetin,
-vec, -cel, -vedotin, -tecan, -tansine, -tropin, -cog, -ermin, -gene) or 'insulin'. Evidence:
'latanoprost-ppds', 'tace-haic', 'ibrutinib-rice', 'pentoxifylline-teva' were stripped.

**D-22 (2026-10-07, curation, owner to review) Known source errors are excluded, never
remapped.** `drug_dictionary.KNOWN_SOURCE_ERRORS`: names matching a pattern never reach a
named wrong drug; each entry carries its evidence and the run counts how often it fired.
Entries: dalteparin/Fragmin -/-> bemiparin; monomethyl fumarate/Bafiertam -/-> diroximel
fumarate (both from `resolve_trial_drugs_20261001_v4.txt`, after D-19).

**D-23 (2026-10-07) Two more deterministic guards.** (a) The dictionary-side salt step no
longer reads bracket contents, and never indexes a stripped root ending in '-yl' (alkyl
fragments: 'dimethyl', 'myristyl'). Evidence (v4): two long chemical names -> dimethyl
fumarate; 'myristyl' -> sodium myristyl sulfate. (b) Brackets holding an isotope ([18F],
[68Ga], [99mTc]) are never removed. Evidence (v4): '[18f]t4' -> levothyroxine.

**D-24 (2026-10-07, DECIDED (b)) Partial matches are their own class.** Amends D-6. A
trial is `full` when at least one tested agent fully resolves; `partial_only` when none
does but one partly does; `none`; or `undeterminable`. `partial_only` trials are neither
matched nor unmatched, are EXCLUDED from the market label, and form their own sample
stratum. A trial's `drugs` are its fully resolved agents' drugs only; partly resolved
agents' drugs are kept apart (`partial_drugs`) even in a `full` trial. Evidence (v4/v5):
9,519 / 9,531 partial agents; wrong examples 'torcetrapib/atorvastatin' -> atorvastatin,
'AZD5363 when combined with weekly paclitaxel' -> paclitaxel, excipients (gelatin, captisol
-> betadex, sodium chloride diluent). The owner expects many false positives there; the
partial stratum of the sample measures it.

**D-25 (2026-10-07) The resolution sample is DEFERRED until it can be pure row reading.**
The owner will label only if every row can be answered from an Excel sheet without
searching. Identity and form can be; approval of a specific form cannot, without knowledge
or a lookup. So the sample waits for the form-level approval matcher (built with Task 2,
needed by the market label anyway), and is then redesigned as follows (amends D-7, D-8):
- an .xlsx sheet with each trial's arms (title, type), interventions (name, other names,
  description) inline from the row-level pull; still blind: nothing from the resolver;
- the labeller records only the tested drug name(s) and form(s); no DrugCentral id, no
  approval column;
- approval is filled in mechanically afterwards from the labeller's clean name and form
  by the form-level matcher, NOT by the resolver's own match;
- a second, unblinded reconciliation pass shows the labeller's answer beside the
  resolver's; the labeller marks same / different;
- partial-only stratum cut to a small fixed size (it does not enter the gate; it measures
  the cost of excluding partial-only trials);
- early stopping for FAILURE only, in batches, pre-registered (passing at 0.95 needs about
  73 rows with no error *(arithmetic, Wilson bound)*, so early stopping cannot shorten a pass).
Until then the market label's error rate is unmeasured; endpoint-met does not depend on it.

Entries D-26 onwards rest on `probe_form_lineup_20261001.txt` (owner's machine, 2026-10-13),
cited below as "the probe".

**D-26 (2026-10-13) The form line-up with Products.Form (implements D-13; amends D-18's
"classes, not words").** Requirements are built from the stated WORDS.
- Route words are tested on the route half of `Products.Form`, or on the dosage half when
  the route half says nothing. The probe's 'VIAL', 'SYRINGE' and 'INJECTION;SOLUTION'
  (halves swapped) had been read as silent for every route.
- Dosage-form words (gel, cream, ointment, lotion, foam, patch, suppository) are tested on
  the DOSAGE half, never as a route. Evidence: 'testosterone gel' (31 agents) is
  'GEL;TRANSDERMAL'; 'lidocaine patch', Qutenza and Synera are 'PATCH;TOPICAL'.
- Modifiers are searched in the whole Form. When absent they exclude, except
  extended/modified release and liposomal on a PARENTERAL product, which are then
  'possible'. Evidence: leuprolide, lanreotide and octreotide depots carry no 'EXTENDED
  RELEASE'; Vyxeos (liposomal, 23 agents) is 'POWDER;INTRAVENOUS'.
- A stated word that is a whole word of the product's brand name confirms it ('LUPRON
  DEPOT', 'ACTHAR GEL', 'TOPROL-XL').
- Other evidence-driven entries:
  - ophthalmic is confirmed by intravitreal, intraocular and intracameral routes
    ('aflibercept ophthalmic', 'brolucizumab ophthalmic solution' are intravitreal);
  - 'oral' beside an inhalation word means oral inhalation;
  - 'INHALANT' confirms inhalation (Nicotrol Inhaler is 'INHALANT;ORAL');
  - 'IMPLANTATION' is possible for subcutaneous (Zoladex).
- Several values of one kind (two routes, two salts) mean an ambiguous name: confirmed
  only if the product has all of them, possible if any.
- `form_approval.check_lineup` fails closed at import on an inconsistent table.

**D-27 (2026-10-13) Two bounds, never a guess.** Every product is confirmed, possible or
excluded for an agent. STRICT = all requirements confirmed; LOOSE = none excluded.
- An agent's question (approved before readout? within W?) is answered only when both sets
  agree; otherwise it is undeterminable.
- Evidence: 2,608 NDA/BLA products are 'INJECTABLE;INJECTION'. Eylea (approved
  intravitreal) and Avastin (not) both appear as 'INJECTION', so neither reading is right
  by itself. Generic route alone moved 96 of 19,498 pivotal agents (probe §5, C -> D), and
  in its top examples confirmed-only was wrong more often than right.
- Applies the standing tri-state and fail-closed rules.

**D-28 (2026-10-13) Hydrates are not salts; a release abbreviation before a bare number is
a code.**
- `drug_names.NON_DISTINGUISHING_SALT_WORDS` gains the four hydrate words. Probe §3:
  monohydrate 0 confirmed / 331 excluded, trihydrate 0 / 64, hydrate 0 / 14, dihydrate
  0 / 3. Drugs@FDA writes 'CYCLOPHOSPHAMIDE' and 'AMOXICILLIN'.
- 'XL 184', 'SR 141716', 'CR 845' are not forms; 'XR 500 mg' still is. Probe §2:
  cabozantinib [xl] 23, cobimetinib [xl] 3.
- Changes only the form columns of the resolver's output.

**D-29 (2026-10-13) A product belongs to a moiety by its link OR by its salt-stripped
ingredient name.**
- Each Drugs@FDA ingredient part is also resolved with its trailing salt words removed
  (never to a bare element or an alkyl fragment, the D-11/D-23 guards).
- So 'DEXAMETHASONE SODIUM PHOSPHATE' is a dexamethasone product even where DrugCentral
  files that salt as its own structure. Evidence: 'intravenous dexamethasone',
  'dexamethasone (iv)' and others (25+ agents) and 'iv methylprednisolone' found no
  injectable; 'testosterone [enanthate]', 'etoposide [phosphate]' found no product of
  that salt.
- Under D-14 an unstated salt accepts every such product. A stated salt still requires
  its own.
- The link is the resolver's own code: `drug_dictionary.fda_product_link`, refactored out
  of `build_dictionary` unchanged.
- **To be verified** on the owner's run: `match_form_approvals.py` §1 lists every
  ingredient whose salt-stripped identity differs from its link.

**D-30 (2026-10-13) Product dating.**
- The application's first approved original is used (on or after 1939-01-01) unless every
  Orange Book row of that (appl_no, product_no) gives one date, not earlier than the
  original; that Orange Book date is used then.
- Evidence (probe §4): the key join is sound. 10,481 of 10,494 ingredients and 10,488
  dosage forms agree; struct2obprod -> ob_product holds for 42,236 of 42,236.
- 1,573 NDA products are dated later than their original. Of those, 50 are a form not
  among the original's products, and for 484 it cannot be told.
- The choice moved 22 of 19,498 pivotal agents (C -> C'). BLAs have no product dates;
  32 approved supplements added a route (NDA 21, BLA 11).

**D-31 (2026-10-13) A moiety FDA-approved in DrugCentral but absent from Drugs@FDA NDA/BLA
products is undeterminable, never new.** CBER products, pre-1938 drugs and ANDA-only
listings would otherwise read as new molecules never approved. Fail closed. DrugCentral
`approval` has one FDA row per drug (2,675 drugs, `probe_drugcentral_20261001.txt` §2).

**D-34 (2026-10-13) Matcher corrections from its first run on the owner's data**
(`match_form_approvals_20261001.txt`, exact/pin). Amends D-26, D-29 and D-30.
- (a) Applications whose only originals are TENTATIVE approvals (`SubmissionStatus` TA)
  are not approvals; their products are dropped and counted. The "undated product" list
  (empagliflozin as 'bi 10773' 23, apixaban, dolutegravir, edaravone, daptomycin) had the
  signature of TA-only 505(b)(2) or generic NDAs. **Assistant's bug in D-30.**
- (b) The brand pin covers a brand family: the brand, or the brand followed by a word.
  'zofran (odt)' was pinned away from 'ZOFRAN ODT'; 'marcaine' from 'MARCAINE
  HYDROCHLORIDE'. **Assistant's bug in the D-33 implementation.**
- (c) Injection-type specific routes are parenteral for the unlabelled-modifier rule
  (epidural, intra-articular, …). DepoDur 'INJECTABLE, LIPOSOMAL;EPIDURAL' had its
  extended release excluded (5 agents).
- (d) Salts.
  - 'dihydrochloride' matches 'hydrochloride' (Novantrone, Orserdu: 16 + 6 agents).
  - A product part naming NO salt is POSSIBLE for a stated salt, not excluded:
    'cytarabine hydrochloride' (42 agents) is a US product listed as the base, and
    excluding it made cytarabine read as a new molecule.
  - Moieties whose every product names a salt are unaffected (metoprolol succinate vs
    tartrate still excludes).
- (e) In Drugs@FDA ingredient strings the four-letter FDA suffix comes off whatever the
  stem ('SOMATROGON-GHLA' never resolved, so somatrogon read as absent from Drugs@FDA).
  D-21's guard stays for registry names.
- (f) Topical and transdermal routes are possible for each other ('topical testosterone'
  is AndroGel 'GEL;TRANSDERMAL').
- The script now prints the "rows name different drugs" count even when it is 0.

**D-35 (2026-10-13) The market target as built: `market_fda_any_pivotal_wW`** (owner's
D-32 statement; supersedes rev 7 §1.2's `market_fda_indication` as the target to build).
- Rev 7's indication-specific target is NOT built. Rev 10 measured its crosswalk at 36.8%
  of DrugCentral indication concepts, and Drugs@FDA cannot say which indication a
  supplement added.
- Construction (`services/market_label.py`), each step a countable reason:
  1. `eligibility.eligible_for_market` unchanged: pivotal phase, actual readout, window
     closed, prospective registration.
  2. Only `full` trials carry a label. Partial-only, unmatched and undeterminable
     selections are UNDETERMINABLE, never 0 (D-24).
  3. The floor (rev 7 §1.2.3): a trial whose every tested agent was approved before its
     readout is INELIGIBLE.
  4. The label is 1 when a tested agent not approved before the readout is approved on or
     after it and within W years, proved by a strict product (D-27). It is 0 when no such
     agent can be, and no agent's prior status or window answer is unknown. Otherwise it
     is undeterminable.
- Computed for W = 3, 5 and 10 in one file; D-10's lag picks the headline.
- The snapshot is a required flag and may not be later than either source's currency.

**D-53 (2026-10-13, owner) LABELS FROZEN; the next work is training.**
- After the labels-delivery runs (resolver v10, matcher v5, targets v4), `trial_targets.csv`
  is the label set. Label work stops.
- The last fixes each moved under 1% of labels (D-48, D-49, D-52's likely size), and the
  goal is a trained predictor.
- Deferred as KNOWN LIMITATIONS, revisited only if training results justify it:
  - D-52 (new plus background drug in one intervention);
  - the D-25 hand-check sample, and with it labelling unresolved market trials 0;
  - market's selection bias (§4): labelled trials over-represent drugs approved somewhere.
- Training order: endpoint met first (the cleanest label, independent of drug resolution),
  then advancement, then market, marked EXPERIMENTAL with its selection bias stated.

**D-49 (2026-10-13; amends D-46) A Purple Book identity ignores form words; a product
keeps its own identity.**
- The identity key is the proper name in INN spelling with dose and form words removed
  (`purplebook.purple_book_identity_key`). IV, SC and IM immune globulin are ONE identity,
  and each product carries its route (D-26).
- Matcher run 4 found the bug: 'immune globulin intravenous (human), 10%' lost
  'intravenous' to the resolver's form stripping and matched the IM identity, so its IV
  form read as excluded (about 20 agents).
- The matcher now uses the identity the dictionary created for a proper name
  (`DrugDictionary.purple_book_identity`), never a re-resolution, which another product's
  alias can make ambiguous ('poliovirus vaccine inactivated'; alpha-1-proteinase
  inhibitor).

**D-50 (2026-10-13; switches D-47 on) Learned codes are applied.**
- The resolver runs twice: pass 1 under the D-44 guard; codes learned from its resolved
  agents (`services/learned_codes.py`); pass 2 with them as the last dictionary source.
- Every kept code is written to `data/labels/learned_codes.csv`.
- Review basis: `probe_learned_codes_20261001.txt` kept 664 codes; all 150 shown were read
  and are correct (BMS-734016 ipilimumab, ABT-199 venetoclax, LCZ696 Entresto with 35
  agents, CPX-351 Vyxeos, CTL019 tisagenlecleucel, many biosimilar codes); 42 conflicts
  refused. Expected effect: about 504 agents, 100 W=5 unresolved market trials.
- **Corrected 2026-10-19:** the review read 150 of the 664 kept codes, not all of them. Among
  the 514 not shown, resolver v10's route review has SHR-1210 -> apatinib (SHR-1210 is
  camrelizumab; at least 67 agents in the truncated tables) and MK-8507 -> clofedanol
  (MK-8507 is ulonivirine). Mechanism and limitation in §6.

**D-51 (2026-10-13) The advancement label as built** (`services/advancement_label.py`;
D-37, D-38; phase 3 out as proposed). `advancement_same_disease_wW_{exact,family}` for
W = 2, 3, 5.
- Headline: W=3 with D-37's exact rule (B); family (C) is the sensitivity.
- Link: a shared name key (compact; without dose and form; without trailing salts; 4+
  characters), a strictly higher phase rank, the same disease, and a later trial first
  submitted between this trial's own first submission and readout + W.
- INELIGIBLE: not an early phase; the D-38 floor, disease by disease.
- UNDETERMINABLE: no name key, no mesh-list condition, or no registration date.

**D-52 (2026-10-13, OPEN, asked of the owner) A new drug plus a background drug in one
intervention.** 'terlipressin and albumin' and 'lifileucel plus pembrolizumab' need, under
D-32's contained rule, a product containing BOTH, though Terlivaz and Amtagvi were approved
alone. The alternative: judge such an agent on its NEW moieties (those not approved before
readout by the matcher's own evidence). `match_form_approvals.py` section 5 counts the W=5
labels that would flip.

**D-44 (2026-10-13) An other name counts only as an alias** (`drug_resolution.resolve_agent`,
`alias_rejection`). When the own name does not resolve, a resolved other name is used only
if all three tests pass:
- the own name's partial result is covered by it;
- the other names do not resolve to several drug sets, unless this one holds them all;
- no drug-shaped token of the own name (`drug_names.INN_STEMS`, 7+ letters) is foreign to
  its drugs' names. A misspelling within 2 edits is the same drug.
Probe (`probe_resolver_other_names_20261001.txt`, on v8):
- 9,637 of 150,545 resolved agents came through an other name;
- 1,565 failed a test (camrelizumab -> apatinib, pyrotinib -> trastuzumab, Opdualag ->
  relatlimab alone, FOLFOXIRI -> fluorouracil);
- they touch 21 of 2,962 W=5 labels.
The resolver prints the rejections by reason.

**D-45 (2026-10-13, DECIDED by the owner; supersedes D-40's rule for loose regimens) A
loose regimen of drugs each already approved is excluded like an approved agent.**
- A loose regimen (`market_label.is_loose_regimen`): a multi-moiety agent, not approved
  before readout, every moiety DrugCentral-FDA-dated before readout, and no product
  containing them all ever approved.
- Excluded as `REASON_LOOSE_REGIMEN`, and left out of the lag.
- Targets v2: 304 such agents were labelled 0 at W=5, a quarter of the 1,210 negatives.
  Fixed-dose combinations keep the D-40 rule (77 positives, 21 negatives).

**D-46 (2026-10-13, DECIDED by the owner; extends D-19) A Purple Book identity for
licensed products DrugCentral cannot name.**
- Each Purple Book proper name that no DrugCentral or Drugs@FDA name resolves gets a
  stable NEGATIVE id (`purplebook.purple_book_id`, 48 bits of SHA-256; collisions raise).
  DrugCentral's positive ids are untouched.
- Names: the proper name, it without brackets, the brand. They are the lowest-priority
  dictionary source, so DrugCentral and Drugs@FDA names always win.
- A word-order-free lookup applies to these identities only ('13-valent pneumococcal
  conjugate vaccine' = 'Pneumococcal 13-valent Conjugate Vaccine (Diphtheria CRM197
  Protein)'). Shared words across identities are ambiguous, never guessed (rabbit vs
  equine anti-thymocyte globulin).
- 'alpha' is read as 'alfa' first (darbepoetin).
- Matcher run 3: 1,206 unresolved agents named an unlinked CBER product and 175 an
  unlinked CDER one.
- Identities are decided from the other sources only, so they do not depend on row order.

**D-47 (2026-10-13, DECIDED by the owner: "whatever makes it more generalizable and
robust") Learned development codes: report first, switched on only after review.**
Recovering codes of approved drugs (LCZ696, QVA149) is what makes "unresolved = not
approved" true enough to label unresolved trials 0 with a measured error: the fix for
market's selection bias. `audit/probe_learned_codes.py` learns from the GUARDED (D-44)
output only, keeps a whole-name code only when 2+ trials agree and none disagrees, and
prints every kept mapping. Nothing is applied until the owner has read it.

**D-48 (2026-10-13) Matcher corrections from its third run.**
- (a) An application's earliest product is never dated after its original approval:
  Dayvigo's Orange Book 2020-04-07 vs approval 2019-12-20; Zaditor's only listed product
  2006 vs 1999. Fixes the lemborexant and ketotifen D-42a cases.
- (b) A comma-joined ingredient part resolves when every comma component does: Trikafta
  'ELEXACAFTOR, IVACAFTOR, TEZACAFTOR', whose DrugCentral link lacks tezacaftor.
- (c) Purple Book proper names are read in INN spelling (D-46).

**D-41 (2026-10-13) The Purple Book is approval evidence beside Drugs@FDA**
(`services/purplebook.py`; the owner's Advanced Search export, 2,251 product rows, 864 BLAs:
480 CDER, 384 CBER).
- Every row becomes a BLA product with its OWN route and approval date, supplements
  included. Revoked licences count: they were approvals.
- Identity: the proper name resolved as a registry name is (`resolve_one_name`, which
  splits 'X and Y'). A proper name with no DrugCentral identity is counted and not used.
- Purple Book products are keyed apart from Drugs@FDA's (product prefix 'PB'): extra
  evidence, never a replacement. The minimum over products still dates the form.
- Fixes, checked on the export:
  - Orencia SC dated 2011-07-29 by supplement (Drugs@FDA: IV only);
  - Eylea intravitreal 2011 vs Avastin IV only (the D-27 counter-example pair, now
    distinguishable);
  - Entyvio SC 2023; Zenapax 1997 (revoked).
- Not fixed: Mylotarg's 2000 approval is absent (only 761060, 2017): D-42a. CBER products
  with no DrugCentral identity (vaccines, albumin, immune globulins, allergenics) are not
  linked. The matcher prints how many unresolved trial agents name one, for a later
  identity decision.
- Key check printed by the matcher: CDER BLA numbers in Drugs@FDA, and first-approval dates
  compared.
- Format: dates "Month D, YYYY"; 15 CDER rows undated (undated products, fail closed).

**D-42 (2026-10-13) DrugCentral's FDA dates guard "not approved before readout"**
(`form_approval.approved_before`). Both guards can only turn False into undeterminable,
never into True.
- (a) For a single moiety with no stated form and no brand pin: if DrugCentral dates an
  FDA approval before the readout and the products say none, the answer is
  undeterminable. Probe: 26 agents, 15 W=5 labels (gemtuzumab ozogamicin, belantamab,
  daclizumab).
- (c) Any moiety with an UNDATED DrugCentral FDA row: undeterminable. Probe: 44 agents,
  32 labels (pre-1938 drugs, medical gases).
- Guard b is not built (D-40).

**D-43 (2026-10-13) Dose units are normalised exactly as names are.**
- `DOSE_UNITS = key(u) for u in _DOSE_UNITS_WRITTEN`. NFKC had turned the micro sign into
  Greek mu, so 'µg' was never a dose. Assistant's bug, earlier session.
- Probe: 162 agents recovered, 34 in W=5 unresolved market trials.
- Changes resolution: the resolver reruns (v8).

**D-40 (2026-10-13, DECIDED by the owner) Combinations of drugs each already approved
keep the current rule:** the agent counts as approved when a product containing all its
moieties is approved (D-32). Guard b is not built.
- Recorded consequence: a loose regimen never coformulated (cisplatin + gemcitabine,
  carboplatin + paclitaxel) can never have a combination product approved, so it is
  labelled 0 whenever eligible. Guard b touched 566 pivotal agents and 350 of 2,962 W=5
  labels.
- The market build prints how many W=5 labels rest on such agents, split by whether a
  combination product ever existed, so the owner can revisit with the numbers.

**D-37 (2026-10-13, DECIDED by the owner) Advancement links within the SAME DISEASE.**
- Owner's principle: "psoriatic for psoriatic". A later higher-phase trial of the same
  drug counts only in the disease the focal trial studied. Advancement in another
  indication does not count.
- Linkage key: the registry's own tested-agent names and codes, never the DrugCentral
  moiety (probe: the moiety key has no key for 53% of early trials and an 81% link rate
  where it does — it keeps the later-approved).
- Disease match: B (an exact shared MeSH mesh-list term) is the owner's choice. C (exact,
  or one trial's own mesh-list term an ancestor of the other's) also keeps siblings apart
  (rheumatoid vs psoriatic arthritis are siblings under Arthritis) but removes coding-depth
  false negatives ('Carcinoma, Non-Small-Cell Lung' vs 'Lung Neoplasms'). The build prints
  both, B as decided and C beside it, and the owner may switch.
- Advancement does not use Drugs@FDA; it links registry to registry.

**D-38 (2026-10-13, DECIDED by the owner) The advancement floor is excluded, disease by
disease.** A focal trial is excluded when a higher-phase trial of the same drug IN THE SAME
DISEASE (D-37's match) was registered before it. Its label would be known in advance (a
model learns "established programme", not the question). Probe at any-condition: 43.9% of
focal trials. Disease-specific, the floor keeps new-indication trials of established
drugs, e.g. pembrolizumab's phase 2 in a cancer where it has no phase 3 yet.

**Proposed by the assistant 2026-10-13, owner did not object, to be confirmed: phase 3 is
not in the advancement population.** Its next step, approval, is the market label;
counting a later phase 3 would mostly measure repeat pivotal trials.

**D-39 (2026-10-13, DECIDED by the owner) Order of work: market fixes first, then the
advancement label, then training (all three targets together).** Owner: "we should fix
everything to maximize the usefulness of this tool".
- Market fixes, each probed then built:
  1. the micro-sign resolver bug;
  2. registry-learned code names;
  3. CBER products dated from the Purple Book (which covers CBER vaccine, blood, cell and
     gene products), if its download carries licence dates;
  4. the three tail guards;
  5. then the unresolved labelled 0, with the error measured by the D-25 sample.

**D-36 (2026-10-13, DECIDED by the owner) Market window: W=5 headline, W=3 sensitivity.**
Amends D-10's procedure (its line was exceeded: H=10 p90 6.16y loose / 6.06y strict, §4).
- At W=3, about 39% of the new drugs approved within 10 years of readout would be labelled
  0; at W=5, about 17%.
- Cost: 3,206 -> 2,962 labelled trials, and readouts after 2021-10 drop out.
- The headline trains the served model and sets the user-facing sentence ("approved within
  5 years of readout"). The sensitivity reruns the same pipeline at W=3, only to show
  whether conclusions depend on the window. `trial_targets.csv` carries both columns.
- W=10 stays a diagnostic.

**D-32 (2026-10-13, DECIDED by the owner: CONTAINED) Combination products count.** A
tested agent counts as approved when any NDA/BLA product CONTAINING its moiety set is
approved; a combination product approves each of its moieties. Owner's reason: the drug is
then on the market.
- **What the output variable means (owner, 2026-10-13):** "approved" is approval anywhere
  in the US market, for ANY indication. It is NOT indication-specific approval for the
  condition the trial studied. Drugs@FDA carries no indication. The market label and the
  lag must be described this way wherever they are reported.
- The record of the choice before the decision follows.
- Is a single agent "approved" by a combination product containing it?
- Probe §5 A -> B: 280 of 19,498 pivotal agents change status, 205 of them from approved
  before readout to no product. 33 first-approval dates differ by more than a year.
- `match_form_approvals.py --set-rule` is required until decided.
- Matcher run (exact/pin): contained would change 248 of 19,498 pivotal agents, 233 of them
  from not approved before readout to approved. Examples, each a different case:
  - cholecalciferol 19, lactic acid 30, vitamin E 9, calcium carbonate 4: supplements
    present only in combination products;
  - dasabuvir / ABT-333 13 and ABT-450/r/ABT-267 4: the Viekira Pak regimen, split across
    interventions;
  - articaine 4: sold only with epinephrine.

**D-33 (2026-10-13, DECIDED by the owner: PIN; amends D-14) A brand name pins the form.**
An agent matched through a brand name is judged on that brand family's products, falling
back to the moiety when the family has no NDA/BLA product of it. Owner's reason: IV
ibuprofen (Caldolor) is a different approval from oral ibuprofen, and peptides sometimes
fail in oral formulations, so the form a brand names matters. D-14's moiety-level rule
still applies to unstated forms without a brand.
- The record of the choice before the decision follows.
- Probe §6: 547 pivotal agents matched through a brand and stating no form. For 136, the
  brand's first approval is later than the moiety's.
- For 22, moiety level says approved before the readout and the brand says not: Caldolor,
  Exparel, Axiron, AzaSite, Endometrin, Bethkis, Combivent Respimat, Vivitrol, Zilretta,
  Tlando, Qlosi, …
- `--brand-rule` is required until decided.
- Matcher run (exact/pin): moiety level would change 39 of 19,498 pivotal agents, 37 of
  them from not approved before readout to approved: Lialda 4, Caldolor 3, Exparel 3,
  Axiron 2, AzaSite 2, Endometrin 2, Acthar, Actiq, Astepro, Bethkis, … Every example is a
  distinct later product.

Entries D-54 onwards were made 2026-10-19, at the start of training (rev 11 §7 item 5).
They rest on the three labels-delivery run outputs (`resolve_trial_drugs_20261001_v10.txt`,
`match_form_approvals_20261001_v5.txt`, `build_targets_20261001_v4.txt`; figures in §4) and
on the literature named in them.

**D-54 (2026-10-19, owner) Endpoint met: ANY primary met is the target; ALL is the
sensitivity.** Closes rev 7 §1.3 item 4 and §8.4.
- The headline is `endpoint_met_label` in `trial_targets.csv` as frozen (D-53), which is
  `endpoint_met_strict`, read from `any_primary_met`. The gate's roll-up stays ANY
  (`endpoint_type.DEFAULT_GATE_ROLLUP`, pinned to the label by a test).
- `all_primary_met` (in `trial_labels.csv`) is scored on the SAME eligible trials as a
  sensitivity. It is reported, never served, and never changes eligibility.
- The user-facing sentence must say "met at least one of its primary endpoints", and the
  co-primary clause (rev 9 §12.17) stays.

**D-55 (2026-10-19) Headline metric: the Brier skill score, one per target.** Owner: "Brier
skill if we can get that baseline data from the literature." **The reference forecast is
PROPOSED by the assistant, owner to confirm.**
- Literature checked 2026-10-19:
  - Endpoint met has a close anchor. Of 2,823 completed phase 3/4 superiority RCTs with
    results on ClinicalTrials.gov (searched June 2015), 60% of those posting an effect
    estimate or p-value were significant (*literature*, Dechartres et al., BMC Medicine
    2016, doi 10.1186/s12916-016-0740-1). The eligible endpoint-met rate here is 60.7%
    (8,187 of 13,487, *arithmetic*).
  - Advancement and market have only programme-level rates from Biomedtracker: phase
    transition success I 63.2%, II 30.7%, III 58.1% (*literature*, BIO, Biomedtracker and
    Amplion, Clinical Development Success Rates 2006-2015, 2016); likelihood of approval
    from phase I 7.9% for 2011-2020 (*literature*, BIO, Informa and QLS, 2021). Those count
    a programme's next phase in any indication with no window; advancement here is a
    same-disease registry link within 3 years (16.0% at W=3 exact).
- **Why a literature rate cannot be the reference:** a Brier skill score compares the model
  with a reference forecast on the SAME test trials and the SAME label. A constant from a
  different definition is miscalibrated against this label by construction, so a model
  beats it by learning the base rate alone, and the bar stops meaning "the model works".
- **Proposal:** the reference is the rate by phase on the training side of each target's
  temporal split (rev 10 §14 item 8's phase-rate baseline, known before any test trial ran).
  The literature rates are printed beside it as external plausibility anchors, each with its
  denominator.
- **The bar:** a model counts as working when its Brier skill score on the temporal test set
  is above 0 and the paired-bootstrap 95% interval lies above 0. Log loss, AUROC, ECE and
  the reliability curve are reported beneath it, never in its place.

**D-56 (2026-10-19, owner) Features may use every `editable_current_value` field.** Owner's
reason: a next question is "how can this trial improve its odds", and sites and countries
are levers a sponsor controls.
- `post_hoc` fields are never features for any target (they do not exist at serving).
  `registration` and `editable_current_value` are admitted for all three targets.
- A registration-only model is fitted beside every headline model as a pre-registered
  sensitivity, so what the editable fields add is measured, not assumed.
- **What would make this not good**, to be checked in the results, not assumed:
  - editable fields updated BECAUSE of how the trial went carrying the model:
    `has_expanded_access` (added once a drug looks promising; posts results at 73.3%,
    rev 9 §12.23), `verification_date` (a stale date marks an abandoned record), and the
    registered outcome counts, which are revised at results posting, the endpoint-met
    label's own event (rev 9 §12.22);
  - a gap between the headline and the registration-only model that those fields explain;
  - training sees final values (completed trials) and serving sees mid-trial ones (sites
    still opening), so a site count means less at serving than in training;
  - an association read as a lever. "More sites, higher odds" is not "adding sites raises
    the odds". The improve-the-odds question is a later, causal one and is not answered by
    these models' coefficients.

**D-57 (2026-10-19, owner) The registry's start is a left truncation; early data is cut in
the split, not in the labels.** Owner: "we don't have historical data that isn't in
ClinicalTrials.gov."
- Evidence: advancement's positive rate at W=3 exact falls 58.8% (1998-2000), 46.9%,
  26.3% (2004-2006), then 13.9% to 16.8% in every band from 2007-2009 to 2022-2024
  (`build_targets_20261001_v4.txt` §4). The bands before 2007 hold 1,789 of 29,871 labels
  (6.0%, *arithmetic*).
- Hypothesis, tested by `probe_training_design.py` §2: the D-38 floor and the link both
  read registration dates, so a later-phase trial already running when the focal trial
  registered (for example one registered in the 2005 ICMJE wave) counts as advancement
  instead of the floor.
- The cut year is set from the probe's output, then applied by the split, so the frozen
  labels (D-53) do not change. The same truncation applies to drug-history features
  (prior trials of the same drug), which the probe's §3 measures by band for every target.

---

## 3. Rev 10 claims, checked

Verified:
- §12.24: all 10 AACT-derived file sizes and sha256 match; the pinned zip matches the pin;
  restore is PostgreSQL 18.6, C collation, snapshot proxy 2026-10-01 04:14
  (`probe_source_files_20261001.txt`, `probe_restore_inventory_20261001.txt`).
- §12.25 and §12.27 figures appear in shipped run outputs.
- §12.30/§15 literature: [1] 69.6% of mentions mapped, 66.6% MeSH, MeSH lacks investigational
  drugs; [2] 70.6% exact, 87.6% after all steps. Both resolve against DrugBank (includes
  investigational drugs), so neither is a benchmark for DrugCentral. [2] also reports a
  ~3-year MEAN from phase 3 completion to approval.

Refuted or corrected:
- §12.28 "every NDA (5,298) and ANDA (21,593) is in ob_product": false. NDA 5,298 of 5,899
  (601 absent); ANDA 21,593 of 22,996 (1,403 absent); BLA 0 of 485
  (`probe_source_files_20261001.txt` §4a).
- §12.27 "every synonym maps to exactly one drug": 133 synonym rows have no drug id. The old
  probe's "5,117 drugs" against 5,116 structures was that NULL counted as a drug.
- New, not in rev 10: 1,449 ApplNos in Submissions are absent from Applications even after
  zero-padding — real orphans, not a key format (`probe_source_files_4g.txt`). Approved
  originals among them: 1,156 *(arithmetic, §4d '?' rows)*, 24 of them Type 1 NMEs.
- No IND or application numbers in AACT: id_type 'FDA' holds FDA grant numbers; 'IND' occurs
  only in free text, 447 rows *(arithmetic, `probe_ids_20261001.txt`)*.

---

## 4. Measured on the owner's machine

Labels-delivery runs (2026-10-13, resolver v10, matcher v5, targets v4; read 2026-10-19):
- Resolver v10: resolved 152,307 (53.7%), partial 10,165, ambiguous 125, unresolved 120,959.
  Learned codes kept 664; agents resolved through one in pass 2: 1,633.
- Matcher v5: NDA/BLA products 14,395 (BLA 3,095). Pivotal agents with an actual readout
  20,234 in 14,863 trials: approved before readout 15,195 (75.1%), not 4,630 (22.9%),
  undeterminable 409 (2.0%, *arithmetic*).
- Targets v4: endpoint met eligible 13,487 = 8,187 met / 5,300 not met (unchanged). Lag
  H=10: 871 decisive identities, 62.2% / 61.8% approved within 10 years (loose / strict),
  p90 5.81y. Market W=5: 2,787 labelled, 1,815 / 972 (65.1%); W=3 3,008 (52.1%); W=10 2,035
  (72.7%). Advancement W=3 exact: 29,871 labelled, 4,773 / 25,098 (16.0%); family 27,377
  (18.9%).

Labels delivery, `labels_all.zip`, 2026-10-13, RUN (above):
- `services/advancement_label.py` (new, D-51).
- `services/learned_codes.py` (new, D-50).
- `purplebook.py`: `purple_book_identity_key` (D-49).
- `drug_dictionary.py`: identity key, `purple_book_identity`, the learned-code source.
- `form_approval.py`: the created identity is used (D-49).
- `resolve_trial_drugs.py`: two passes and `learned_codes.csv` (D-50).
- `match_form_approvals.py`: section 5 (D-52 measurement).
- `build_targets.py`: advancement columns and section 4; Purple Book names in the lag tail.
- Tests: 13 added (1036 -> 1049).

Market-fix delivery 2 runs (2026-10-13; resolver v9, matcher run 4, targets v3, learned
codes report):
- Resolver v9: resolved 151,367 (+822), partial 10,241 (+699: the guard turns incomplete
  combinations partial). Guard rejections (per other name): own name's drugs lacking 811,
  other names disagreeing 3,406, a different drug's name 153. Purple Book identities 301;
  agents decided by a Purple Book source about 2,650. Full trials 96,835 (+532). MeSH
  corroboration 94.7% -> 93.8%, expected: Purple Book identities have no DrugCentral MeSH.
- Matcher run 4: products 14,393 (BLA 3,093); 197 clamped to their original (D-48a);
  D-42a down to gemtuzumab ozogamicin alone. Pivotal 19,932: approved 15,007 (75.3%), not
  4,527 (22.7%), undeterminable 398 (2.0%).
- Targets v3: endpoint unchanged; loose regimens out of the lag (421 records). H=10 lag:
  864 identities, 61.8% approved within 10 years, p90 5.81y / 5.80y. W=5 market 2,721
  labelled, 1,762 / 959 (64.8%); 258 excluded as loose regimens. The tail now holds long
  real vaccine lags (Priorix 2004 -> 2022, Heplisav 2000 -> 2017).
- Learned codes report: 2,199 codes with evidence, 714 already resolve, 42 conflict, 779
  under 2 trials, 664 kept.

Market-fix delivery 2, `market_fix2.zip`, 2026-10-13, RUN:
- `drug_names.py`: `INN_STEMS`, `inn_tokens`, `within_edits`.
- `drug_dictionary.py`: Purple Book identity source, word-set lookup, `names_of`.
- `drug_resolution.py`: the D-44 guard, with audited reasons.
- `purplebook.py`: `inn_spelling`, `purple_book_id`.
- `form_approval.py`: D-48 a, b, c.
- `market_label.py`: D-45.
- `resolve_trial_drugs.py`: `--purplebook`, and the alias-guard section.
- `match_form_approvals.py`: the Purple Book feeds the dictionary; identities are printed.
- `build_targets.py`: D-45 in the label and the lag.
- `audit/probe_learned_codes.py` (new).
- Tests: 12 added (1024 -> 1036).
- Smoke: a vaccine trial named '13-valent pneumococcal conjugate vaccine' resolves to its
  Purple Book identity and is labelled 1 from Prevnar 13's 2010 licence.

Market-fix delivery 1, `market_fix1.zip`, 2026-10-13, RUN (resolver v8, matcher run 3,
targets v2):
- `services/purplebook.py` (new, D-41).
- `services/form_approval.py`:
  - the Purple Book's products and route vocabulary;
  - guards D-42a/c, with new statuses `STATUS_DC_EARLIER` and `STATUS_DC_UNDATED`;
  - approvals-file columns `dc_fda_first` and `dc_fda_undated`;
  - `fda_approval_dates`.
- `services/drug_names.py` (D-43).
- `scripts/match_form_approvals.py`: `--purplebook`; section 1b (key check, unlinked
  names, unresolved agents naming an unlinked product); `--inspect NAME`, repeatable (for
  lemborexant, ketotifen, Trikafta); the guard statuses in section 3.
- `scripts/build_targets.py`: section 3b, the D-40 report (W=5 labels resting on
  combinations of drugs each already approved, split by whether a combination product
  exists).
- Tests: 8 added (1016 -> 1024).

Resolver other-name probe, delivered 2026-10-13, NOT YET RUN (run it on v8):
`audit/probe_resolver_other_names.py`. It classes every agent resolved through an AACT
other name by partner-risk signal:
1. its own name resolves to other drugs;
2. its other names name several drugs;
3. its own name is an INN-shaped word naming none of the drugs used;
4. a code (likely alias);
5. a brand or descriptor (likely alias).
It reports counts overall, pivotal and W=5 labelled, with examples. It decides whether the
resolver's other-name fallback needs a guard before market or advancement is trusted, and
informs the learned-codes decision.

Market-fix delivery 1 runs (2026-10-13):
- **Resolver v8:** resolved 150,545 (+166 from the micro sign; probe said 162); full trials
  96,303 (+77). Nothing else moved.
- **Matcher run 3:**
  - NDA/BLA products 13,220 (BLA 1,920 with the Purple Book's 1,076 linked rows).
  - Purple Book key check: 463 of its 480 CDER BLAs are in Drugs@FDA; first-approval dates
    agree on 461 and the Purple Book is earlier on 2. The 17 Purple-Book-only are
    withdrawn licences (Zenapax).
  - Confirmed products for stated routes rose: intravitreal 18 -> 57, subcutaneous
    114 -> 212, intravenous 539 -> 653.
  - Pivotal 19,537: approved 14,655 (75.0%), not 4,473 (22.9%), undeterminable 409
    (2.1%), including D-42a 12 and D-42c 44.
- **Targets v2:**
  - Endpoint unchanged (13,487).
  - W=5 market 2,936 labelled, 58.8% positive.
  - Lag H=10 p90 6.06y / 6.00y.
  - D-40 report: loose regimens 304 x 0, 5 x 1; fixed-dose combinations 21 x 0, 77 x 1 —
    the evidence for D-45.

Market-fixes probe (`probe_market_fixes_20261001.txt`, 2026-10-13, W=5):
- **Micro sign:** 1,088 not-resolved agents carry a mu; 162 resolve once 'µg' is a unit,
  34 of them in W=5 unresolved market trials. Every example is correct (indacaterol,
  aclidinium, ipratropium, dexmedetomidine, Symbicort -> budesonide + formoterol).
  BUILD.
- **Registry-learned codes: NOT SAFE as designed.**
  - 135,748 keys learned; 7,877 conflict.
  - Evidence >= 1: 14,479 agents, 963 of 5,633 trials. Evidence >= 2: 7,769 agents,
    627 trials.
  - Top mappings are often partner drugs, not aliases, even at >= 2: shr-1210 and
    camrelizumab -> apatinib, tqb2450 -> carboplatin, kn046 -> cisplatin + paclitaxel,
    rhtpo -> aclarubicin, dalteparin -> enoxaparin, probiotic -> inulin.
  - Cause: AACT other-names fields list combination partners.
  - **This implicates the resolver's other-name fallback:** an agent whose own name fails
    can be resolved through a partner's name. Its precision is unmeasured. NEXT: a
    resolver precision probe before any market or advancement label is trusted.
- **CBER-type:** of the W=5 unresolved trials, not-resolved agents by AACT type: DRUG 5,259,
  BIOLOGICAL 1,820. CBER words: vaccine 614, toxoid 34, plasma 31, immunoglobulin 50, …
  Trials with a biological or CBER-worded agent: 1,383 of 5,633 (24.6%, an upper bound;
  BIOLOGICAL includes CDER antibodies).
- **Guard a** (single moiety, no stated form, DrugCentral FDA date before readout): 26
  agents, 15 W=5 labels — gemtuzumab ozogamicin 9, belantamab mafodotin 7, daclizumab 7
  (withdrawn and reapproved); lemborexant 2 and ketotifen 1 look like matcher misses, to
  inspect. BUILD.
- **Guard b** (every moiety DrugCentral-FDA-dated before readout): 566 agents, **350 of
  2,962 W=5 labels (12%)**. It mixes fixed-dose combinations (amlodipine + valsartan:
  Exforge, a real new product) with loose regimens never coformulated (cisplatin +
  gemcitabine, carboplatin + paclitaxel), which the current rule labels 0 forever.
  Elexacaftor + ivacaftor + tezacaftor (17) should be approved before such a readout
  (Trikafta) and is not: a possible matcher miss, to inspect. OWNER DECIDES.
- **Guard c** (a moiety with an undated DrugCentral FDA row): 44 agents, 32 W=5 labels —
  prasterone 15, calcium gluconate, morphine, medical gases (oxygen, nitric oxide,
  helium). BUILD, fail closed.

Purple Book (owner's download, 2026-10-13): the file is the MONTHLY CHANGES report (rows
N/R/U, September 2026 only), not the catalogue. The full export comes from Advanced Search.
- Its columns carry what Drugs@FDA lacks: per-product Route of Administration and Approval
  Date, supplement rows, revoked licences, Center (CDER/CBER).
- `BLA Number` vs Drugs@FDA `ApplNo` is to be key-checked before use.
- Date format: "September 27, 2023".

Market-unresolved probe (`probe_market_unresolved_20261001.txt`, 2026-10-13):
- Unresolved or partly resolved market-eligible trials: 6,564 at W=3, 5,633 at W=5.
- Their share rises by readout band: 47.9% (2004-06), 62% (2007-12), 69% (2013-18),
  72.5% (2019-21), 80.0% (2022-24).
- Windows closing after DrugCentral's last FDA approval (2026-03-26): 246 (W=3).
- Unresolved names textually matching a Drugs@FDA NDA/BLA name: within W 4 / 5958 (0.1%);
  before readout 65 (1.1%); after W 12; no hit 5,877 (98.6%). This is a FLOOR, and the
  top-names list shows it is badly blind:
  - CDER-approved drugs under code or variant names: lcz696 (Entresto), qva149 (Utibron),
    ac2993 (exenatide), 'faster-acting insulin aspart', 'indacaterol 150 µg' (the µ sign),
    'inhaled human insulin';
  - CBER products Drugs@FDA can never label: 13-valent pneumococcal, v114, 20vpnc,
    menacwy-crm, zoster vaccine, nonacog beta pegol.
  - The rest are failures (tanezumab, taspoglutide, bifeprunox, dimebon, dasotraline,
    rapastinel, tak-875, anacetrapib).
- **Conclusion: the unresolved set cannot be labelled 0.** Keeping it out (owner's lean)
  keeps the selection problem of §4's targets entry.
- Code-like share of unresolved names: 28.8%.

Advancement linkage probe (`probe_advancement_links_20261001.txt`, 2026-10-13, W=3):
- Focal early-phase trials eligible at 3y: 62,745.
- NAME key: no key 12.7%; floor 43.9%; linked 10.2% (with a shared mesh-list condition
  4.6%); not linked 33.2%. Link rate among keyed, not floor: 23.5% (10.7% with a shared
  condition).
- MOIETY key: no key 53.2%; floor 42.7%; keyed and not floor only 2,561, link rate 81.1%.
  That is the selection signature.
- Name-key link rate by moiety resolvability: resolvable 42.7%, unresolvable 18.2%. The
  moiety key would drop the non-advancers.
- Where both keys exist (2,532): they agree on 2,068; name says not linked but moiety
  linked on 461 (salt and spelling differences); the reverse on 3.
- Most-shared name keys are real backbone drugs (cyclophosphamide 2,502 trials,
  pembrolizumab, carboplatin, cisplatin, …), not generic words.
- Fix needed: the name key keeps salts ('cediranibmaleate').

Targets, first run (`build_targets_20261001.txt`, 2026-10-13, snapshot 2026-10-01,
approvals contained/pin):
- **Endpoint met reproduces rev 10 exactly:** 13,487 eligible = 8,187 met / 5,300 not met;
  18 undeterminable (registration timing).
- **Lag (D-10), H=10:** 1,048 decisive new-at-readout identities, 519 (49.5%) approved
  within 10 years at the loose bound and 514 (49.0%) at the strict bound.
  - Of those approved: p50 2.38y / 2.36y, p75 3.99y / 3.96y, p90 6.16y / 6.06y
    (loose / strict).
  - Approved within 3 years: 61.1% / 61.5%. Within 5 years: 83.4% / 83.7%.
  - **p90 is over 5 years: beyond D-10's decision line, so the owner decides W.**
  - H=5 (1,435 identities, p90 4.05y) is truncated at 5 years by construction, so it
    cannot test a 5-year line.
- **Lag tail (strict lag over 3 years: 230 identities)** mixes real long programmes
  (landiolol, ceftobiprole, prucalopride, istradefylline, terlipressin) with three
  artefact classes:
  - withdrawn-then-reapproved drugs whose first approval Drugs@FDA lacks (gemtuzumab
    ozogamicin first approved 2000, read as 2017; daclizumab 1997, read as 2016);
  - pre-1938 unapproved marketed drugs given their first NDA late (phenobarbital: 1995
    readout, Sezaby 2022);
  - multi-moiety agents of old drugs that read as new under the contained set
    (amoxicillin + omeprazole: 1996 readout, first co-pack 2011).
  - Failed-then-repurposed programmes (atrasentan, lonafarnib, teplizumab, maribavir) are
    correct under D-32 (any indication), but lengthen the tail.
- **Market label, labelled trials:**
  - W=3: 3,206 (1 1,471 / 0 1,735, 45.9%);
  - W=5: 2,962 (1 1,712 / 0 1,250, 57.8%);
  - W=10: 2,147 (1 1,394 / 0 753, 64.9%).
  - The positive rate by 3-year readout band is flat at every window (W=3: 40.6% to 52.4%
    from 2001 onwards), so no "recent means 0" censoring artefact.
- **W=3 cascade:** prior approval 7,991; no tested agent resolved 5,958; tested agent
  undeterminable 4,267; partly resolved 606; prior status unknown 283; window answer
  unknown 3.
- **OPEN, found by this run: the labelled market population is conditioned on a future
  event.** DrugCentral holds drugs approved somewhere, so "the tested agent resolved" is,
  for a historical trial, largely "this drug was later approved somewhere". The unresolved
  5,958 (W=3) are where never-approved drugs go. The 45.9% positive rate is that
  selection's signature. Market is not trainable until this is decided.
- The same applies to any advancement linkage key built on DrugCentral resolution.

Resolver v7 (`resolve_trial_drugs_20261001_v7.txt`, 2026-10-13, after D-28):
- Agent statuses (150,379 / 9,531 / 72 / 123,574) and match classes (full 96,226,
  partial-only 6,832, none 76,762; pivotal full 20,308) equal v6, as D-28 requires.
- Form handling: no_form_stated 141,301; kept 9,918; stripped 7,329; inferred 1,362.
- Agents stating more than one salt: 295 (v6 411). extended_release stated by 2,381
  agents (v6 2,464).
- `ob_product` repeated rows 7,339, none naming different drugs (the counter for that
  is absent from the output, so 0): the dictionary's last-row-wins never changed a drug.

Form matcher, first run (`match_form_approvals_20261001.txt`, exact/pin, before D-34):
- NDA/BLA products 12,288 (NDA 11,444, BLA 844).
- Dated by application original 3,700, by Orange Book product date 7,929, undated 659
  (part of that is D-34a); Orange Book earlier than the original 10.
- Salt-stripped identity differs from the link on 335 products (93 ingredient strings).
  The 24 shown were all salts or esters of the same moiety (dexamethasone phosphate ->
  dexamethasone, testosterone enantate -> testosterone, …). The other 69 were not shown.
- Pivotal resolved agents with an actual readout 19,498:
  - approved before readout 14,320 (73.4%);
  - not approved before readout 4,736 (24.3%);
  - undeterminable 442: bounds disagree 163, undated product 79, absent from
    Drugs@FDA 200.
- 'Not approved before readout' is led by real new molecules (vildagliptin, dapagliflozin,
  insulin degludec, sugammadex, vonoprazan, aliskiren, …). It also holds cholecalciferol
  (19): supplements read as new under the exact set rule (D-32).

Form line-up probe (`probe_form_lineup_20261001.txt`, 2026-10-13), beyond what D-26 to D-33
cite:
- NDA/BLA products 12,288; linked to a moiety 11,808 (ob_product 10,494, ingredient 1,214,
  ob_application 100, unlinked 480).
- 818 products have no dated approved original; 11 products' ApplNo is absent from
  Applications.
- `ob_product` (appl_no, product_no) is NOT unique: 55,242 rows, 47,903 keys, 5,531 keys on
  more than one row. `drug_dictionary` keeps the last row (unchanged); whether repeats
  name different drugs is now counted in the resolver's DICTIONARY section.
- Resolved agents 150,379, of which 8,580 state a class and 12,442 a salt (319 more than
  one).
- Pivotal resolved agents with an actual readout: 19,498 in 14,266 trials; 3,048 state a
  form; 1,382 have more than one moiety.
- Line-up errors fell into five mechanisms (D-26, D-28, D-29); the rest of the 'excluded'
  examples were real unapproved forms (oral insulin, oral vinorelbine, liposomal
  paclitaxel, intranasal ketamine, inhaled vancomycin).

Row pull (`pull_aact_rows_20261001.txt`, probe output alongside): 23 tables, 461,824
interventional trials, 19,072,127 trial-table rows, 434 s; probe row counts equal pulled row
counts for every table; 0 cross-trial links on all four checked keys. The second-run
reproducibility check was requested and not yet reported.

Resolver, first version (`resolve_trial_drugs_20261001.txt`, before D-11 and D-12):
- Selection over 222,329 drug trials: arm rule 162,520; no-arms fallback 17,300;
  undeterminable — no experimental arm 30,212, all background 9,958, experimental arms carry
  no drug 2,103, only placebo 210, no drug linked 26.
- Agents: resolved 148,033; partial 7,981; ambiguous 33; unresolved 127,509.
- Trials matched: 100,715 of 179,820 determinable (56.0%); pivotal 21,280 of 34,774 (61.2%).
- MeSH corroboration among matched trials with indexed MeSH: 91,970 corroborated, 5,411 not.
- Top unresolved names are mostly non-US or never-approved agents (sintilimab, camrelizumab,
  veliparib…) and non-drugs ('chemotherapy', 'blood sample'): misses that do not hurt the
  market label. Misses that do: fludarabine (634), class names (G-CSF, interleukin-2),
  development codes (CC-5013, ISIS 301012).

**All resolver figures in this section predate D-19 and are superseded where drug identity
matters** (match counts by route, MeSH corroboration, the comparator flag). Selection
counts and the form-handling counts do not depend on drug ids.

Resolver v6 (`resolve_trial_drugs_20261001_v6.txt`, after D-24; current): full 96,226 of
179,820 determinable (53.5%), partial-only 6,832 (3.8%), none 76,762; pivotal full 20,308
of 34,774 (58.4%), partial-only 1,457 (4.2%), none 13,009. Agents as v5. Tested moiety also
a comparator moiety 4,437 (pivotal 1,346). MeSH corroboration 88,461 vs 4,943; trials not
fully matched whose MeSH names a drug 16,364.

Resolver v5 (`resolve_trial_drugs_20261001_v5.txt`, after D-22/D-23, before D-24): agents
resolved 150,379, partial 9,531, ambiguous 72, unresolved 123,574; trials matched (full or
partial, pre-D-24 definition) 103,058 of 179,820 determinable (57.3%); pivotal 21,765 of
34,774 (62.6%). Source errors fired: bemiparin removed from 'dalteparin sodium' (25) and
'fragmin' (24); diroximel fumarate from 'bafiertam' (2) and 'monomethyl fumarate' (2).
Alkyl fragments skipped 70. Tested moiety also a comparator moiety: 5,501 (pivotal 1,723).

Resolver v4 (`resolve_trial_drugs_20261001_v4.txt`, after D-19 to D-21, before D-22/D-23):
agents resolved 150,515, partial 9,519, ambiguous 71, unresolved 123,451; trials matched
103,147 of 179,820 determinable (57.4%); pivotal 21,786 of 34,774 (62.7%). Form handling:
no_form_stated 141,251; kept 10,002; stripped 7,387; salt_inferred 1,394. Tested moiety also
a comparator moiety: 5,504 trials (pivotal 1,723); not 80,564; cannot tell 136,261. MeSH
corroboration 94,207 vs 5,460; unmatched trials with a MeSH-named drug 10,516.

Resolver v3 (`resolve_trial_drugs_20261001_v3.txt`, form redesign, before D-19): matched
103,303 of 179,820 determinable (57.4%); pivotal 21,806 of 34,774 (62.7%). Form handling:
no_form_stated 141,445; stated_form_kept 9,998; stated_form_stripped 7,358; salt_inferred
1,478. Comparator flag 5,530 True — conflated by D-19, do not use.

`probe_unresolved_20261001.txt`: dictionary-side salt recovers 838 agents uniquely (837 to
FDA-approved drugs), 27 currently-unmatched pivotal trials; wrong pairs seen: dalteparin ->
bemiparin (15), clobetasol -> altizide (5). Unmatched trials whose MeSH names a drug: 12,571
(MeSH drug FDA-approved in 12,101; pivotal 3,445) — a mix of leaks and comparators.

`probe_salt_forms_20261001.txt`: form-vs-form trials 782 (early 439, pivotal 164,
post-approval 131, unknown 48); includes noise (placebo routes; same drug named with and
without its salt).

---

## 5. Built this session

Training-design delivery, `training_design.zip`, 2026-10-19, NOT YET RUN:
- `services/temporal_split.py` (new): rev 7 §4.3 as a function. Train = outcome determined
  before the boundary, under two readings (`readout`; `label_known` = readout + the target's
  label lag); test = started on or after it (actual starts only, or any recorded start);
  straddlers dropped; inconsistent dates and missing dates undeterminable, never forced.
- `services/advancement_label.py`: `linked_trials` returns the trials behind a label (floor
  and within-window); the label now reads it. Behaviour unchanged, pinned by a test against
  the original loop on random trials.
- `audit/probe_training_design.py` (new, scratch): band rates and the phase-rate baseline
  (D-54, D-55); advancement's early-band mechanism and floor share (D-57); drug-history
  truncation by band; the split grid per target with events counts; coverage of every
  fields-file column by provenance (D-56). It recomputes the W=3 exact advancement label and
  stops if it differs from `trial_targets.csv`.
- Tests: `tests/test_temporal_split.py` (new, 9); `tests/test_advancement_label.py` (+3).

Market-fixes probe, delivered 2026-10-13, NOT YET RUN: `audit/probe_market_fixes.py`.
It sizes each D-39 fix:
- the micro-sign bug (agents recovered);
- registry-learned codes at evidence 1 and 2 (agents and W=5 market trials recovered,
  examples, conflicts);
- CBER-type share of the W=5 unresolved trials;
- the three tail guards, with the W=5 labels each touches.

**Assistant's bug found 2026-10-13 (earlier session):** `drug_names.key()` applies NFKC,
which maps the micro sign U+00B5 to Greek mu U+03BC. `DOSE_UNITS` holds only U+00B5, so
'µg' is never stripped as a dose: 'indacaterol 150 µg' left 'indacaterol μg' and did not
resolve. Fixed in the market-fix delivery after the probe sizes it; it changes resolution,
so the resolver reruns (v8).

The owner is to download the Purple Book data (purplebooksearch.fda.gov, "Download Purple
Book Data") into `data/purplebook/`; its columns are probed before use.

Two probes, delivered 2026-10-13, RUN (§4), at the owner's request before deciding:
- `audit/probe_market_unresolved.py`: the market trials whose tested agent did not resolve.
  It shows their share by readout band, DrugCentral's currency, and every unresolved name
  checked against Drugs@FDA's own NDA/BLA product names and dated against the readout (a
  FLOOR on what labelling them 0 would get wrong).
- `audit/probe_advancement_links.py`: registry-name versus DrugCentral-moiety linkage for
  advancement on one early-phase population. It reports the floor, link rates with and
  without a shared MeSH condition, the name-key rates split by moiety-resolvable, the two
  keys' agreement, and name-key collisions.

Targets, delivered 2026-10-13 as `targets.zip`, run once (§4):
- `services/market_label.py` (new): the market label (D-35) and the D-10 lag.
- `services/form_approval.py`: `parse_approval_row`, the tested inverse of `approval_row`;
  `approved_within`.
- `services/eligibility.py`: `years_after`, the one window-arithmetic implementation, now
  used by `window_closed` (behaviour unchanged).
- `scripts/build_targets.py` (new): writes `data/labels/trial_targets.csv`, one row per
  trial, with the endpoint-met verdict and label (the same eligibility code and loaders as
  `audit_posting_bias.py`) and the market verdict and label for W = 3, 5, 10. It prints the
  lag at H=10 and H=5, both bounds, the D-10 line and the tail drugs. Advancement: no column.
- `tests/test_market_label.py` (new, 11 tests).

Matcher fixes (D-34), delivered 2026-10-13 as `form_matcher_fix.zip`:
- `services/form_approval.py`; `services/drugsatfda.py` (`TENTATIVE`);
  `scripts/match_form_approvals.py` (zero counts printed);
- `tests/test_form_approval.py` (8 tests added, 35 in all).
- Same day, after the owner's decisions: `--set-rule` defaults to contained and
  `--brand-rule` to pin (D-32, D-33). No test change.

Form matcher, delivered 2026-10-13 as `form_matcher.zip`, run once (exact/pin):
- `services/form_approval.py` (new): the form-level approval matcher, D-26 to D-31, with
  D-32/D-33 as required settings.
- `services/drug_names.py` (D-28).
- `services/drug_dictionary.py` (product linking refactored into `ob_product_index` and
  `fda_product_link`, behaviour unchanged, repeated Orange Book keys counted).
- `scripts/match_form_approvals.py` (new); `tests/test_form_approval.py` (new, 27 tests);
  two test files extended (3 tests).

Task 2 probe, delivered 2026-10-13 (the entry first written here said 2026-10-07: wrong,
corrected), RUN: `audit/probe_form_lineup.py` (scratch), output
`probe_form_lineup_20261001.txt`.

Delivered and run:
- `audit/probe_source_files.py`, `audit/probe_restore_inventory.py`,
  `audit/probe_intervention_arms.py` (scratch).
- Row pull: `services/aact_rows.py`, `scripts/pull_aact_rows.py`, `tests/test_aact_rows.py`;
  `aact_provenance.py` (10 tables appended), `aact_fields.py` (`ROW_LEVEL_ONLY_TABLES`),
  `aact_snapshot.py` (required tables), `rebuild_aact.py` (fourth pull),
  `tests/test_aact_fields.py`. The fields pull's SQL and columns are byte-identical to before.
- Task 1 v1: `services/drug_names.py`, `drugsatfda.py`, `drug_dictionary.py`,
  `tested_agent.py`, `drug_resolution.py`, `resolution_sample.py`;
  `scripts/resolve_trial_drugs.py`, `scripts/build_resolution_sample.py`; six test files.
- `audit/probe_unresolved.py`, `audit/probe_salt_forms.py`, `audit/probe_form_vocab.py`.

Superseded: `fix1_resolution.zip` (held; its changes ship inside `form_redesign.zip`).

Partial class, delivered 2026-10-07 as `partial.zip` (install after `cleanup.zip`):
`services/drug_resolution.py` (MATCH_CLASSES, partial_drugs), `resolution_sample.py` (three
strata), `scripts/resolve_trial_drugs.py` (rates by class), `scripts/build_resolution_sample.py`;
two test files.

Cleanup, delivered 2026-10-07 as `cleanup.zip` (install after `idfix.zip`):
`services/drug_dictionary.py` (D-22, D-23a), `drug_names.py` (D-23b); two test files.

Drug-id fix, delivered 2026-10-07 as `idfix.zip` (install after `form_redesign.zip`):
`services/drug_dictionary.py` (D-19 parent groups; D-20 guard), `drug_names.py` (D-20,
D-21, inhaler words), `drug_resolution.py` (original spelling to components, parent groups
in the comparator flag and the trial file); `audit/probe_form_vocab.py`,
`audit/probe_unresolved.py` (no parent remapping); three test files.

Form redesign, delivered 2026-10-07 as `form_redesign.zip` (includes fix1):
`services/drug_names.py` (D-11 B/C, salt list, stated form), `drug_dictionary.py` (D-11 A),
`drug_resolution.py` (form fields, matched entry, comparators), `tested_agent.py`
(comparators), `resolution_sample.py` (sheet `tested_form`, key form fields);
`scripts/resolve_trial_drugs.py` (route review, FORM and FORM REVIEW sections),
`scripts/build_resolution_sample.py` (instructions per D-12/D-14/D-17);
`audit/probe_unresolved.py` (label fix); five test files.

---

## 6. Known limitations and caveats

- **Moiety-level judgement for unstated forms (D-14).** A new form tested under the bare
  moiety name is judged as the moiety.
- **Old forms without an NDA/BLA original.** Testosterone propionate has none; testosterone
  cypionate's earliest NDA is 2022 although Depo-Testosterone dates from 1979 (old products
  were sometimes filed as ANDAs). Counting ANDAs is not the fix: a generic approved after a
  readout would then count as the form's approval.
- **Development codes** for later-approved drugs are the main recall gap; no source maps them
  yet.
- **Dosage-form granularity.** `Products.Form` has 122 dosage forms and 92 routes over 12,288
  NDA/BLA products; formulation words must map onto classes of these, not exact strings.
- **'At least one' inflates matches** where only an established partner of a combination
  resolves.
- **The no-arms fallback** includes comparator drugs; its precision is the first suspect.
- **MeSH corroboration is not precision**: MeSH and the resolver read the same names.
- **Leading counter-ions are not read** as stated salts ('sodium valproate').
- **Brand names containing a form word** read as that form ('Acthar Gel' is an injection,
  read as topical).
- **'infusion' reads as IV**; a name stating two routes records both classes.
- **(2026-10-13) Forms added to a BLA by supplement are invisible.** BLAs have no product
  dates, and a route added by supplement may not appear in Products: abatacept SC is only
  'INJECTABLE; IV (INFUSION)'.
- **(2026-10-13) OTC monograph drugs have no NDA.** Oxymetazoline nasal or urea cream
  read as forms never approved; D-31 does not catch them when the moiety has another NDA.
- **(2026-10-13) Off-label routes are 'never approved' by construction** (rectal or
  vaginal misoprostol, intravitreal bevacizumab). That is correct for "approval of the
  stated form", and worth remembering when reading the market label.
- **(2026-10-13) 'ER' can mean emergency room** ('etomidate … ER'). Not special-cased.
- **(2026-10-19) Learned codes can be a partner drug (D-44/D-47).** `alias_rejection`'s
  third test fires only on INN-shaped words in the intervention's own name. An intervention
  named only by a code ('SHR-1210') whose single resolving other name is its partner
  ('apatinib') passes all three tests, and D-47 then learns the code from every such trial.
  Touches market labels and drug-id features only: endpoint met does not use resolution,
  and advancement links on registry names (D-37). At most about 100 W=5 market labels rest
  on learned codes (D-50). Before market training: a co-occurrence probe (a code that is its
  own intervention in a trial where its learned drug is a separate intervention cannot be
  that drug's alias).
- **(2026-10-19) `drug_names.compact` drops every non-ASCII letter.** 'tβ4' (thymosin
  beta-4) compacts to 't4' and resolves to levothyroxine, D-23's T4 case by another route.
  Market only; a handful of agents.
- **(2026-10-19) `eligibility.DEFAULT_MARKET_WINDOW_YEARS` is still 3** against D-36's W=5.
  `build_targets.py` passes W explicitly; training code must too, never the default.

---

## 7. Next, in order

1. ~~Form redesign~~ built (§5).
2. ~~Rerun and review~~ done (v6 is current).
3. ~~Draw tranche 1~~ DEFERRED (D-25).
4. Task 2: readout-to-first-approval lag (D-9, D-10), at the form level per D-12/D-14.
   - Matcher BUILT and run once (2026-10-13). Fixes D-34 delivered.
   - D-32 (contained) and D-33 (pin) decided 2026-10-13; matcher v2 run (contained/pin).
   - Lag and market label BUILT 2026-10-13 (`build_targets.py`); run it.
   - RUN 2026-10-13 (§4). W decided (D-36: W=5). Still open: the market selection
     problem (probe run: unresolved cannot be labelled 0; what to do with the market
     target asked 2026-10-13), the three tail-artefact guards.
4a. Advancement label: BUILT 2026-10-13 (D-51), in `trial_targets.csv`.
4b. LABELS FROZEN (D-53). D-52 and the D-25 sample are deferred limitations.
5.  NEXT: training.
    a. ~~Sanity-check the three run outputs~~ done 2026-10-19 (D-50 corrected; §6).
    b. Rev 10 §14 items 3 to 7 before any model:
       - pre-model decisions: multi-endpoint reading DECIDED (D-54); headline metric
         DECIDED as the Brier skill score, reference forecast proposed (D-55, owner to
         confirm); editable fields DECIDED (D-56); left truncation DECIDED, cut year from
         the probe (D-57). STILL OPEN: `TRAINING_EXCLUSION_MIN_EFFECT`. Proposal: in the
         headline metric, excluding gate-refused trials counts as an improvement only when
         the paired-bootstrap 95% interval of the difference lies on the improve side. Only
         306 of 13,793 rows differ (2.2%, *arithmetic*), so "indistinguishable" is the
         likely result, which rev 8 §12.11 already counts as a finding.
       - RUN `audit\probe_training_design.py` (delivered 2026-10-19), then choose each
         target's split boundary and train rule, the advancement cut year, and the
         feature-build design from its output;
       - the feature builder, with the leakage registry asserted in code. Features come
         from `trial_registration_fields.csv` through its provenance file, never from
         `trial_labels.csv` (which mixes post-hoc and label columns). Resolution and
         approval facts enter only when dated before the trial's own registration: "the
         tested agent resolves to DrugCentral" leaks later approval into all three targets.
         Drug-history features use registry name keys as of the registration date;
       - temporal splits that print each target's events count (`services/temporal_split.py`).
    c. Endpoint met first against the phase-rate baseline, then advancement, then market
       (experimental, its selection bias stated in its output).
   - Then the lag script, on `trial_drug_form_approvals.csv`.
4b. Then the resolution sample per D-25.
5. Rev 10 §14 items 3–9.
6. Rename rev 8 and rev 9 handoff files (still `# Handoff — rev N supplement.md`).

---

## 8. Lessons from this session

- `ORDER BY 1 COLLATE "C"` collates the literal 1; name the column. `SELECT DISTINCT x …
  ORDER BY x COLLATE "C"` is rejected; collate in the select list.
- `restore_aact_snapshot.py` needs `--pg-bin` outside the `pgtools` environment.
- Redirected stdout on Windows is cp1252: scripts call
  `sys.stdout.reconfigure(errors="backslashreplace")`.
- Regexes that strip numbers must not touch digits inside codes (mk-3475, covid-19) or
  locants (2,4-dinitrophenol); doses must be removed before splitting on '/'.
- A name with nothing left after filtering is not a placebo.
- Ambiguity guards only work if sibling forms strip to the same root ('furoate' was missing).
- Mapping salts to the parent and stripping forms silently changes the drug's identity. For
  training data, keep every level and let the label decide.
- Every file delivered must be shown individually before installing.
- **Check what a foreign key references before using it as an id.** struct2parent's
  parent_id looked like a structure id and was not; synthetic tests that assumed the same
  could never catch it. The route review on real data did, within one run.
- A test fixture that encodes an assumption about a source's schema must say so, and the
  assumption must be checked on the real file (here it was not).
- (2026-10-13) `Products.Form` is not always "dosage ; route": some rows have one half, and
  some have the halves swapped. The probe assumed the format, and its own output showed
  'VIAL' as possible for oral.
- (2026-10-13) A word can name a dosage form or a route; reading 'gel' as a route was the
  largest single error in the line-up. Ask what kind of fact a word states before mapping
  it.
- (2026-10-13) A key that looks unique must be counted: `ob_product` (appl_no, product_no)
  repeats 5,531 times and the dictionary had silently kept the last row.
- (2026-10-13) Date a delivery from the session's own clock, not from the last date in the
  handoff: §5 carried 2026-10-07 for a 2026-10-13 delivery.
- (2026-10-13) A status that is not an event is not an event: Drugs@FDA's tentative
  approvals sat in the product list beside real ones, and the matcher read them as
  "approved, date unknown".
- (2026-10-13) Take a file's name from the code that writes it, never from memory:
  `build_targets.py` first looked for `trial_labels.manifest.json`; the pull writes
  `trial_labels.csv.manifest.json` (`pull_aact_results.manifest_path`), which the script
  now calls.
- (2026-10-13) If a reply says questions follow, ask them in that reply. The owner was
  left not knowing two decisions existed.


---
---

<!-- ===== Part 2: rev 10 — supplement (was: Handoff_rev10.md) ===== -->

# Part 2: rev 10 — supplement

# Handoff — rev 10 supplement

**Read with rev 7, rev 8 and rev 9, not instead of them.** Rev 7 remains the durable record
for every section no supplement names; each later revision wins where it disagrees, and
says so. Rev 10's §13 adds items 37–48 and its §14 replaces rev 9's.

**The goal from here is a predictor that trains and runs on the project machine.**
Publishing the data for others to rebuild is out of scope: if the model is trained and
works, people need to be able to replicate the results, not the data. Code for publishing
was written this session and is parked (§0); nothing in §14 depends on it.

Scope of this revision: the AACT tier is now **pinned to a monthly archive and rebuilt
locally, byte-reproducibly**, so every result is stable against one fixed input, and
every AACT figure was re-baselined on it. The market label's sources were probed and
chosen (Drugs@FDA for dated approvals, DrugCentral for indication codes). DrugCentral is
extracted from its release archive rather than queried. The events count for the boosting
argument was printed. The market resolution gate was re-anchored on truth (§12.30). Expected test count **868 passed, `GATE: GREEN`**.

**Every figure below comes from a run on the project machine**, unless it is marked
*arithmetic* (derived from printed counts) or *literature* (from a source's own documents,
cited by number from §15).
No sandbox figures appear.

**Rev 9 figures are superseded wherever this revision re-ran them.** They came from AACT's
live database (labels about 2026-09-20, fields 2026-10-05) and cannot be regenerated by
anyone. The superseded files are kept in `data/aact_live_20261005/` only until this
revision is accepted.

---

## §0 — STATE

- Docs file names are mangled: `docs/# Handoff #U2014 rev 8 supplement.md` and `… rev 9 …`,
  not `Handoff_rev8.md` / `Handoff_rev9.md`. Rename them (§14 item 14).
- `config.yaml` remains a stale Gen-1 artefact. `README.md` was rewritten this revision
  (the Gen-1 text is in `archive/docs/README_gen1.md`), but its "Run it" and "Data" sections
  describe the parked publishing setup and are stale too.
- **Parked, not part of the current goal:** `services/data_registry.py`,
  `services/zenodo.py`, `scripts/build_data_registry.py`, `scripts/bootstrap.py`,
  `scripts/upload_zenodo.py`, `.devcontainer/`, `environment.yml` and their tests. They
  work and are tested, but nothing on the path to a trained model uses them. Ignore them
  unless publishing is taken up again.
- The local PostgreSQL restore of the pinned AACT snapshot is in `data/pg/` on port 54320.
  Restart it with `restore_aact_snapshot.py --start` and stop it with `--stop`.

---

## §12.24 — THE AACT TIER IS PINNED AND REPRODUCIBLE

### Why

AACT's live database changes nightly, so no pull from it could ever be reproduced, and
every figure built on one drifts. Pinning to one archive makes every pull, audit and
training run repeatable on the project machine. AACT keeps **permanent monthly archives**
(*literature* [6]); its daily copies are deleted at the start of each month.

### The pin (`services/aact_snapshot.py: PINNED`)

| field | value |
| --- | --- |
| file | `20261001_clinical_trials_ctgov.zip` |
| url | `https://aact.ctti-clinicaltrials.org/snapshots/4418/download` [7] |
| bytes | 2,547,627,020 |
| sha256 | `8f88d1f779c70050ee8679b4440ce3e62d8a5843cb61642bcb6f91f8669bc629` |
| dump | custom format, created 2026-10-01 05:38:53; from PostgreSQL 14.3, by pg_dump 14.19 |

`audit/probe_aact_archive.py` confirmed that all 23 tables the pulls read are present.
The list is derived from the pull declarations (`aact_snapshot.required_tables`), not
typed. Two members of the zip are 0 bytes, `schema.png` and `data_dictionary.csv`; nothing
reads them.

### The restore (`scripts/restore_aact_snapshot.py`)

It verifies the zip against the pin, then:
- creates a cluster with `initdb` in the C locale (byte-wise text order), listening on
  localhost only;
- extracts the dump and runs `pg_restore -j 4 --no-owner --no-privileges`;
- checks that every required table exists and has rows.

Run: **pg_restore exit 0, 0 error lines, 1,390 s.** `studies` and `calculated_values` both
have 605,150 rows.

### Determinism (`scripts/rebuild_aact.py`)

The three pulls ran twice from the same restore, with `--as-of 2026-10-01`. **All 10 data
files were byte-identical.** Only `trial_registration_fields.manifest.json` differs, and it
carries run timestamps.

Two fixes, made before the runs, are what made this hold:

- Every `string_agg(DISTINCT …)` had no `ORDER BY`, so the order within a joined cell was
  unspecified and depended on the server's collation. Every aggregate now sorts with
  `ORDER BY … COLLATE "C"` (`aact_aggregates.ordered_text`), and two tests pin it.
- The fields chunk query and both results-pull queries had no `ORDER BY`. They now order
  by `nct_id`, and outcome rows by `nct_id, o.id, oa.id`. The surrogate ids are stable
  within a single snapshot.

Rebuilt files, as produced on the project machine. A rebuild that does not reproduce
these hashes means the input or the code changed:

| file | bytes | sha256 |
| --- | --- | --- |
| results_raw_outcomes.csv | 73,846,462 | `886f0dab90bcc4ca13bd3adcb01caecee2e32792567a5976856fdc23ff525ab9` |
| results_raw_studies.csv | 81,618,574 | `e06754144a469dd6540af49391d6c98ac0f6ebfb53a43b4a7c6f53995b758727` |
| trial_design_outcomes.csv | 308,094,649 | `024fb870285fc1426a920ab27adfa7d9793b4eec981c2722a21cf3c480f7496d` |
| trial_design_outcomes.csv.manifest.json | 90 | `4f8685e70851cba1ff87349795ba756080e2d96c9bdb3285715e587d52751415` |
| trial_entities.csv | 172,464,800 | `93c7a5ccdbee6a12c1562ae4201a88a7514503f1b67fb7b35fea0ffcee9ac5d3` |
| trial_labels.csv | 179,194,174 | `5cc4276686c7fc365eb2327f87055b5e8b47bbdd9378c6c8531cc4b00b6e4db6` |
| trial_labels.csv.manifest.json | 224 | `1c7dea42446ec35cc2a4d33d63f725daf4b1102ec86af309daad601e8b395a0c` |
| trial_registration_fields.csv | 311,268,875 | `90a51c0b0c33799e1b08474ff276bc4bc41cbf062c84bd6547e9ad45ca4ce381` |
| trial_registration_fields.provenance.csv | 16,045 | `8cd17bcf09ef68db2db907478dca918cc0e714366ca71584bf1baece76db948f` |
| trial_registration_text.csv | 1,285,691,171 | `d94d75cf6681a0b56ccee66a0881bb38b3bbb49fdcd6c22996f0646cd1202d3c` |
| trial_registration_fields.manifest.json | 6,312 | timestamps; verify by structure, not by hash |

These now live in `data/aact/`. Not yet rebuilt: `recon_raw_*` (§14 item 11).

## §12.25 — THE RE-BASELINE (snapshot 2026-10-01)

**Use `--snapshot 2026-10-01` from now on**, not 2026-10-05.

### Pull figures

| | pinned 2026-10-01 |
| --- | --- |
| interventional trials | 461,824 |
| drug trials (`is_drug_trial`) | 222,329 |
| results posted | 75,607 (16.4%) |
| ≥1 primary analysis | 24,276 |
| strict labelled | 21,271, of which 13,011 positive |
| headline A/B, all interventional | 20,099 = 12,510 met / 7,589 not met |
| tiers A / B / C / E / D | 18,444 / 1,655 / 1,172 / 1,456 / 439,097 |
| `endpoint_na_reason`: NI / geometric / percent-scaled / coverage | 1,568 / 212 / 204 / 48 |
| any vs all primary readings differ | 1,874 of 21,271 |
| registered primary outcome rows | 932,714 across 454,110 trials; 1 blank measure |
| fields pull | 461,824 rows, 320 s, 0 trials with other than one lead sponsor |

**The snapshot drift from rev 9 §12.21 is gone.** Labels and fields now come from one
snapshot, so the first-submitted date merged onto 461,824 of 461,824 label rows.

### Training population (`audit_posting_bias.py` §1)

| disposition | n | met | not met |
| --- | --- | --- | --- |
| **eligible (the training population)** | **13,487** | **8,187** | **5,300** |
| registered after primary completion | 600 | 428 | 172 |
| registration timing unknown | 18 | 11 | 7 |
| endpoint gate refuses | 306 | 196 | 110 |
| labelled, before those three | 14,411 | 8,822 | 5,589 |

Eligible, by phase group:

| group | n | met | not met |
| --- | --- | --- | --- |
| phase 1 | 731 | 435 | 296 |
| phase 2 | 4,137 | 2,036 | 2,101 |
| pivotal | 6,053 | 4,200 | 1,853 |
| post-approval | 1,916 | 1,140 | 776 |
| unknown | 650 | 376 | 274 |

The minority class among the eligible is not-met, at **5,300**. In phase 2, met is the
minority.

The live-database run printed 13,493 = 8,191 / 5,302 (labelled 14,417 = 8,826 / 5,591).
That is superseded.

### Other targets and the audit

- Endpoint-met exclusion reasons: not a drug trial 239,495; no primary analysis 206,977;
  non-headline tier 941; retrospective 600; gate-refused 306; timing unknown 18. The tier-C
  sensitivity stratum is 880.
- Advancement: eligible 85,059, with 11,105 removed as retrospective. Market: eligible
  22,314, with 3,060 retrospective.
- Market window series, 3 / 5 / 10 years: 22,314 / 19,798 / 13,471.
- Both MeSH sides, market-eligible: 16,940 (75.9%). By era: pre-FDAAA 83.6%, FDAAA 77.2%,
  Final Rule 71.2%. Market-only (no endpoint label): 16,677 (74.7%).
- Disclosure denominator 140,613. The rev 9 §12.23 conclusions stand unchanged: the cliff
  separates phase, sponsor class, responsible party and expanded access, and is flat by
  era, FDA regulation and FDAAA verdict.
- FDAAA verdicts over drug trials: applicable 32,752, not applicable 122,439,
  undeterminable 67,138.
- The endpoint-type gate figures are unchanged from rev 9: applicable 122,083, refused
  10,564, undeterminable 83,550; 333 refused yet labelled.

## §12.26 — THE EVENTS COUNT AND THE BOOSTING ARGUMENT

Rev 9 §14 item 1 is done. The events count is **5,300**.

- *Arithmetic*: at rev 8's planning figure of about 40 features, that is about 133 events
  per variable.
- Rev 7's argument against boosting (about 9 events per variable) is dead.
- In simulation (*literature* [3]), random forests
  and neural nets needed about 200 events per variable to stabilise, against 20–50 for
  logistic regression; boosting was not tested.
- At about 133, logistic regression with limited interactions is comfortably supported.
  Shallow, regularised boosting is defensible; an unconstrained ensemble is not.

What would make 5,300 not good:
- **The training side of the temporal split.** It is not computed yet, and it is the
  number that actually bounds the model.
- **Parameters, not features.** Phase has 9 levels and sponsor class 8, so 40 features is
  more than 40 parameters.
- **Clustering.** Trials of the same drug or programme are not independent events, and
  they can leak across the split.
- **Selection.** A flexible model fitted to a graded 6.5% slice learns that slice's
  structure, which transports worst.

**Decided:** the model family is chosen by a pre-registered held-out comparison (logistic
with limited interactions against shallow boosting, scored on Brier and ECE), run after the
temporal split. It is not chosen by an events-per-variable rule. The hand labels are not
training data and never limited this count. The labels come from the trials' own posted
analyses, so the limit is the ClinicalTrials.gov posting cliff.

## §12.27 — DRUGCENTRAL, EXTRACTED FROM THE RELEASE ARCHIVE

### The public instance is not a source

`unmtid-dbs.net:5433` serves the **2023-11-01** release (`dbversion` 54). It holds about
200 tables belonging to other users, including a 3,915-row copy of `approval`, so other
users can write to it and nothing read from it can be verified. Rev 10's first probe also
found an unexplained inconsistency there (§13 item 44).

### The release used

| field | value |
| --- | --- |
| archive | `Drugcentral_2026-09-25.pgdump` (custom format, gzip, from PostgreSQL 16.15), from [8] |
| sha256 | `5afea88f6a1916f250c28c10ab988e7464ca6fd5ccf2c42f080600473de732dc` |
| bytes | 1,378,106,682 |
| `dbversion` | 56, dated 2026-09-02 |

`scripts/extract_drugcentral.py` converts it in two steps. First `pg_restore 18.6 -f`
writes plain SQL for the 12 wanted tables; no server is needed. Then
`services/pgdump.py` reads that SQL into CSVs (`data/drugcentral/dc_*.csv` plus a
manifest). Rows:

| table | rows |
| --- | --- |
| structures | 5,116 |
| approval | 4,128 |
| identifier | 87,135 |
| synonyms | 23,875 |
| omop_relationship | 42,515 |
| ob_product | 55,242 |
| struct2obprod | 77,694 |
| doid / doid_xref | 10,040 / 34,645 (unchanged from 2023) |

The only column holding both NULLs and empty strings is `approval.applicant`.

### Findings (`audit/probe_drugcentral.py`, on the extracted CSVs)

- **Approvals are per drug and agency, one row each.** FDA: 2,675 drugs, 503 of them
  undated, latest approval 2026-03-26. No approval is dated per indication.
- **New drugs carry indications.** 104 of 105 drugs first approved after 2023-08-18 have
  an indication row. Coverage by stratum:
  - first approved 2013 to 2023-08-18: 485 of 489;
  - first approved by 2012: 1,447 of 1,578;
  - FDA approval undated: 170 of 503 (33.8%).
- **Indication coding gap, by era.** Indication rows coded in neither UMLS nor SNOMED:
  2,276 of 9,335 for drugs first approved by 2012, against 13 of 890 for those approved
  2013 to 2023-08-18. This confirms rev 7 §1.2.1 caveat 1.
- **Crosswalk ceiling.** Of 1,722 distinct indication UMLS codes, 634 (36.8%) reach MeSH
  through MONDO [5] and 559 (32.5%) through `doid_xref`. Both are measured by concept, not by
  trial. The UMLS Metathesaurus, which needs a free licence, is the full route.
- **The name key has no fan-out.** Every synonym maps to exactly one drug.
  - Drug-trial distinct values matched exactly: MeSH terms 2,687 of 13,372; intervention
    names 4,660 of 180,614; other names 4,922 of 82,469. None map to more than one drug.
  - AACT's intervention MeSH terms include class ancestors (Pyridines, Heterocyclic
    Compounds), so the MeSH denominator is padded.
- **MeSH descriptor fan-out.** 103 descriptor ids map to more than one drug, up to 13.

## §12.28 — DRUGS@FDA, FOR DATED APPROVALS (decision (b))

**Decided:** dated approval events come from Drugs@FDA; indication codes come from
DrugCentral. The files [9] were downloaded on 2026-10-06 to `data/drugsatfda/`: 12
tab-delimited tables, with keys unique in Applications, Products and Submissions.

- **Currency.** The latest approval is 2026-10-02, so a 3-year window closes for readouts
  up to 2023-10-02 against Drugs@FDA. DrugCentral's bound is 2023-03-26, and it applies to
  the indication target.
- **New indications are dated.** `ActionTypes_Lookup` has a category "Efficacy-New
  Indication" (level 1 EFFICACY, level 2 INDICATION), carried by 1,907 approved
  supplements. That dates the supplement, but **not which indication it added**; only the
  attached letter or label says, as a PDF. Public notes are filled on at most 0.6% of
  supplements; 19.9% have any document attached.
- **The application number links the two sources.** 26,891 of 29,380 Drugs@FDA
  applications appear in DrugCentral's `ob_product`: every NDA (5,298) and ANDA (21,593).
  The 485 BLAs are absent, because the Orange Book has none, so biologics need the name
  route. 2,538 of 2,908 single ingredients (87.3%) match a DrugCentral synonym exactly.
- **Gaps:**
  - CBER biologics (vaccines, cell and gene therapies) are not in Drugs@FDA. Their trials
    must be undeterminable, never 0.
  - One original approval is dated 1900-01-01, a placeholder.
  - 185 applications have more than one approved original; 3,988 have none (3,440 of them
    ANDAs).
  - Ingredient strings use three separators (`;` 689, `,` 79, `||` 3) and salt forms.

## §12.29 — SMALLER CHANGES

- **`audit_posting_bias.py` §1 prints the class balance.**
  `eligibility.endpoint_met_outcome_counts` reports met / not met for the eligible
  population and for each post-label exclusion, and `strict_outcome` fails closed on any
  token other than 1/0.
- **`build_endpoint_type_sample.py`.**
  - The unit is now one distinct, non-blank primary text per trial
    (`sampling.sampling_units`): 14,252 collapsed, the same as `build_confirm_sample.py`.
  - Defaults are 100 rows, 12 repeats and a floor of 5. The floor was chosen so that 18
    strata × 5 fits in 100, and a test pins that.
  - It stratifies on three groups (`sampling.STRATUM_GROUPS`).
  - The kappa gate was replaced by `GATING_CRITERIA` in its prose, and literal figures
    were removed from printed prose.
  - **None of the 18 strata reaches `MIN_STRATUM_FOR_VERDICT`.**
  - This closes rev 8 §0.1 items 5 and 6 and rev 9 §14 item 2.
- **Every pull takes `--port`;** port 5432 was hard-coded in four scripts.

## §12.30 — THE RESOLUTION GATE, RE-ANCHORED ON TRUTH

Rev 7 §1.2.2 set an absolute gate: below about 50% of drug trials resolving to a drug
entity, the indication work is not worth starting. It was never ratified. **Owner
direction, 2026-10-06: base the gate on the real-world truth, not on a fixed 50%.**

### What "resolves" can mean in reality (*literature* [1], [2])

- **A hand-annotated set of 500 interventional trials** [1]: 69.6% of intervention
  mentions mapped to at least one DrugBank concept. NLM's automatic MeSH terms covered
  66.6% of entries, and the authors note **MeSH does not include investigational drugs**.
- **An automated multi-step match of ClinicalTrials.gov drug trials to DrugBank** [2]
  reached 70.6% of drug trials on exact names
  alone. Synonyms, product names and fuzzy matching added about 23,500 more trials:
  about 87% in total (*arithmetic* from their counts).
- So even with a perfect resolver, the share of drug-trial mentions that are real drug
  entities is well below 100%: placebo, standard of care, comparators, unnamed codes.

### Why a population-level rate cannot be the gate

DrugCentral covers approved drugs [4]. A trial whose drug was **never approved** usually
cannot resolve against it, and for the market target that trial's true label is 0. A
low resolution rate is therefore partly a measure of how often drugs fail, not only of
how well names match. The rate mixes two different things:

- an approved drug the resolver missed, which becomes a **false 0**;
- an unapproved drug that correctly finds no match, which is a **true 0**.

Only a hand-checked truth can separate them. That is what rev 7 §1.2.1 caveat 3 already
asked for.

### The gate, as replaced

Measured on a hand-labelled sample of pivotal drug trials, stratified by whether the
resolver matched. For each trial the labeller records the **tested agent** (not placebo,
comparator or background therapy) and whether it has an FDA approval. Two criteria, in
the same shape as the endpoint gate's (rev 8 §12.11):

- **recall on truly approved agents**: the share of trials whose tested agent is
  FDA-approved that the resolver links to the right drug. Each miss is a false 0.
- **precision of a match**: the share of the resolver's matches that are the right tested
  agent. A wrong match onto an approved drug is a false 1.

**Ratified by the owner, 2026-10-06, before any sample exists: recall ≥ 0.90 and
precision ≥ 0.95.**

**The gate is not the point of this output.** The point is the measured truth: how often
the resolver finds a trial's real tested agent, and how often a match is wrong. Those two
rates, each reported with a 95% interval, travel with the market label as its known error
rate (rev 7 §1.2.1 caveat 3), and they are reported beside the market model. The gate only
decides whether the market target proceeds on that resolver.

Sizing, *arithmetic*: an interval of ±0.05 around a rate near 0.90 needs roughly 140
trials whose tested agent is truly approved. The sample is drawn to reach that count in
the recall stratum, not to a fixed total.

The population-level resolution rate is still printed, as a plausibility check against
the literature range above: far below it means a weak resolver; far above it means
over-matching, such as placebo or class terms matching drugs.

---

## §13 — CORRECTION LEDGER, rev 10 additions

| # | what was wrong | correct reading |
| --- | --- | --- |
| 37 | Rev 9's figures were treated as reproducible. | They came from the live database. Re-baselined on the pinned 2026-10-01 snapshot (§12.25); eligible endpoint-met is now 13,487 / 5,300. |
| 38 | `string_agg(DISTINCT …)` with no `ORDER BY`, and chunk queries with no `ORDER BY`. | The order was unspecified and collation-dependent. Fixed; two runs are byte-identical (§12.24). |
| 39 | Rev 7 §1.2.1 / §8.1c assumed DrugCentral could date an approval per indication. | It records the first approval per drug and agency only. Dated new indications come from Drugs@FDA action types, which still do not say which indication. |
| 40 | The DrugCentral public instance was treated as the source. | It is shared, writable by others, and on the 2023 release. Use the extracted 2026-09-25 archive. |
| 41 | `build_endpoint_type_sample.py` printed "rows collapsed, same outcome twice: 0". | It now collapses 14,252 repeated texts within a trial. Rev 8 item 5 closed. |
| 42 | The rev 9 opener said the label and fields manifests were in the zip. | They were not; only data files carry them. |
| 43 | Rev 9 §12.21: 175 more trials in the fields pull, one label row absent. | That was drift between two snapshots. With one pinned snapshot, the merge is 461,824 of 461,824. |
| 44 | The first DrugCentral probe reported 29–31 matched terms mapping to more than one drug, while also reporting no name with more than one drug. | That came from the public instance; on the extracted release the count is 0. The cause is undetermined; do not cite that run. |
| 45 | The public-instance probe's figures (latest FDA approval 2023-08-18, indication coverage, crosswalk 36.8% / 32.5%) were read as current. | Superseded by the 2026-09-25 release figures (§12.27). |
| 46 | `recon_raw_*` in `data/aact/` still came from the live database. | Audit-only. Regenerate from the pinned restore when the reconstruction audit is next needed (§14 item 11). |
| 47 | The results pull's "negative-class recount" prints ~369, ~9 per variable and ~40 features as fixed prose. | Stale printed figures (rev 9 lesson 75). Remove them (§14 item 10). |
| 48 | Rev 7 §1.2.2's resolution gate: an absolute 50% of drug trials resolving. | Unconnected to truth, and confounded with approval, because DrugCentral holds approved drugs. Replaced by recall ≥ 0.90 and precision ≥ 0.95 against a hand-labelled sample, ratified 2026-10-06 (§12.30). |

---

## §14 — NEXT ACTIONS, rev 10

Replaces rev 9 §14. The order follows rev 7 §8 and rev 9 §14: drug resolution (§8.1c),
then the advancement label (§8.3), then features, then models (§8.4).

Rev 9 items 1 and 2 are done (§12.26, §12.29). **Item 3 is only half done.** Both sources
were probed and chosen (§12.27–28), but neither of its two numbers exists yet.

1. **Trial-to-drug resolution** (rev 7 §8.1c, rev 9 §14 item 3).
   - Resolve each drug trial's **tested agent** to a DrugCentral drug: through names
     (synonyms, salt to parent), MeSH ids, and Drugs@FDA application numbers via
     `ob_product`.
   - Print the population-level rate against the literature range (§12.30).
   - Draw the hand-labelled sample (sized for the recall stratum, §12.30) and report
     recall and precision with 95% intervals; the ratified bars decide whether the market
     target proceeds.
   - This one join also feeds the advancement linkage key and grouping by drug in
     evaluation, not only the market target.
2. **The readout-to-first-approval lag** for pivotal trials of new molecules: actual
   primary completion to Drugs@FDA's first ORIG approval, within the window bound of
   §12.28. This confirms or kills the 3-year window (rev 7 §1.2.3: the check gates the
   decision rather than follows it). If the 90th percentile sits under 3 years, the
   window stands; if it is near 5, most successful programmes get labelled 0.
3. **Settle the open decisions before any model:**
   - the advancement linkage key (rev 7 §1.3 item 1, which waited on drug resolution);
   - which multi-endpoint reading is the target (rev 7 §1.3 item 4; the strict label
     currently uses any primary met);
   - `TRAINING_EXCLUSION_MIN_EFFECT`, set before the comparison runs (rev 8 §12.11);
   - **one headline metric per target, named in advance** (rev 7 §1.1 item 3). That is
     the bar a model has to clear to count as working.
4. **The advancement label** (rev 7 §8.3), on the linkage key from item 3.
5. **Feature-build design** (rev 9 §14 item 4):
   - which provenances each target admits;
   - the `editable_current_value` fields named (start date, facilities, countries,
     expanded access);
   - the label encoders named;
   - use `classified_agency_class` and the distinct facility counts;
   - one pinned snapshot date;
   - a temporal boundary per target (rev 7 §1.1 item 2).
6. **The feature builder.** One module for all three targets, with the per-target leakage
   registry asserted in code, so R7 becomes a test.
7. **Temporal splits**, printing each target's training-side events count.
8. **Models.**
   - Endpoint-met first, by the pre-registered comparison (§12.26), with a simple baseline
     (the met rate by phase) as the floor it must beat. Then advancement.
   - Market after its label: windows close against the earlier source date; CBER
     products are undeterminable; decide how to attribute an efficacy supplement to an
     indication; decide the crosswalk route.
   - Group by drug where the resolution allows.
9. **A local predictor entry point.** Given an NCT id, read the trial from the local
   restore, build its features, score it, and return the three outputs through
   `fdaaa.trial_flag` with their flags.
10. **Small:** remove the stale printed figures in `pull_aact_results.py` (§13 item 47).
11. **Optional:** regenerate `recon_raw_*` from the restore, after auditing
    `validate_reconstruction.py`'s SQL ordering; pin the MONDO release.
12. **Carried from rev 9:** item 5 (registered primary-outcome titles across versions);
    item 6 (the dose-finding estimand/input sample, visible as 10,075 `dose_finding`
    over `safety_tolerability` decisions); item 7 (retire or re-run rev 8 §12.13's
    sandbox figures); item 8 (rev 9 §0.1's two small items).
13. **Cleanup, once this revision is accepted:** delete `data/aact_live_20261005/`. Keep
    `data/aact_snapshots/` and `data/pg/`.
14. **Rename the mangled docs files** to `Handoff_rev8.md` and `Handoff_rev9.md`.

---

## §15 — REFERENCES

Literature and source documents cited in rev 10.

1. Miftahutdinov Z, Kadurin A, Kudrin R, Tutubalina E. Medical concept normalization in
   clinical trials with drug and disease representation learning. *Bioinformatics*. 2021.
   doi:10.1093/bioinformatics/btab474. (500 hand-annotated interventional trials; 69.6% of
   intervention mentions mapped to DrugBank; MeSH lacks investigational drugs.)
2. Vasan K, Gysi DM, Barabási A-L. The clinical trials puzzle: how network effects limit
   drug discovery. *iScience*. 2023;26(12):108361. PMC10749231. (Drug-trial interventions
   matched to DrugBank: 70.6% of drug trials by exact name, then synonyms, product names
   and fuzzy matching.)
3. van der Ploeg T, Austin PC, Steyerberg EW. Modern modelling techniques are data hungry:
   a simulation study for predicting dichotomous endpoints. *BMC Medical Research
   Methodology*. 2014;14:137. doi:10.1186/1471-2288-14-137.
4. Ursu O, Holmes J, Knockel J, et al. DrugCentral: online drug compendium. *Nucleic Acids
   Research*. 2017;45(D1):D932–D939. doi:10.1093/nar/gkw993.
5. Vasilevsky NA, Matentzoglu NA, Toro S, et al. Mondo: unifying diseases for the world, by
   the world. medRxiv. 2022. doi:10.1101/2022.04.13.22273750. (Source of
   `mondo_xref.csv`.)
6. AACT (Clinical Trials Transformation Initiative). PostgreSQL database instructions:
   daily snapshots and permanent monthly archives.
   https://aact.ctti-clinicaltrials.org/downloads/postgres_instructions
7. AACT snapshot downloads, the pinned archive's link:
   https://aact.ctti-clinicaltrials.org/snapshots/4418/download
8. DrugCentral downloads (release archive 09/25/2026). https://drugcentral.org/download
9. U.S. Food and Drug Administration. Drugs@FDA data files.
   https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files

---

## Lessons, 78–85

78. A shared public database is not a source. If others can write to it, nothing read from
    it can be verified. Extract a pinned release.
79. An aggregate with no `ORDER BY` is a nondeterminism nobody sees until two runs are
    compared, and collation makes it machine-dependent.
80. Reproducibility is a property you test, not one you claim. Run twice from the same
    input and compare hashes.
81. "Latest" is not a version. DrugCentral's download link, MONDO's purl and Drugs@FDA's
    daily file all move. Record a hash or a release tag at download.
82. A test that pins a query's exact text breaks on a correct change. Assert the structure
    the change must keep.
83. Never paste a credential into a chat or a shared log. Revoke and reissue.
84. Hand labels were never the bottleneck. The events count is set by how many sponsors
    post an analysis, not by labelling effort.

---

85. A coverage rate against a source that only holds successes measures success as well as
    coverage. Gate a resolver on recall and precision against a hand-checked truth, not on
    how much of the population it touches.

---

## File inventory, rev 10

**New, in use:**
- services: `src/trial_pos/services/pgdump.py`, `aact_snapshot.py`;
- tests: `tests/test_pgdump.py`, `test_aact_snapshot.py`;
- scripts: `scripts/extract_drugcentral.py`, `restore_aact_snapshot.py`, `rebuild_aact.py`;
- scratch probes: `audit/probe_drugcentral.py` (now reads the extracted CSVs),
  `audit/probe_drugsatfda.py`, `audit/probe_aact_archive.py`;
- docs: `archive/docs/README_gen1.md`, `docs/Handoff_rev10.md`.

**New, parked** (publishing; see §0): `services/data_registry.py`, `services/zenodo.py`,
`tests/test_data_registry.py`, `tests/test_zenodo.py`, `scripts/build_data_registry.py`,
`scripts/bootstrap.py`, `scripts/upload_zenodo.py`, `.devcontainer/devcontainer.json`,
`environment.yml`, `data_sources.json`.

**Modified:**
- `src/trial_pos/services/eligibility.py` (class-balance counts), `sampling.py` (defaults,
  stratum groups, sampling units), `endpoint_type.py` (`endpoint_text_key`),
  `aact_aggregates.py` and `aact_fields.py` (ordered aggregates and ordered chunks);
- `scripts/audit_posting_bias.py`, `build_endpoint_type_sample.py` (rewritten),
  `pull_aact_results.py`, `pull_aact_fields.py`, `pull_design_outcomes.py`,
  `validate_reconstruction.py` (`--port`; ordering in the first two);
- `tests/test_eligibility.py`, `test_sampling.py`, `test_endpoint_type.py`,
  `test_aact_aggregates.py`, `test_aact_fields.py`;
- `pyproject.toml` (optional `pull` extra), `.gitignore`, `README.md` (rewritten; partly
  stale, §0).

**Data on the project machine:**
- `data/aact/`: rebuilt from the pin;
- `data/aact_snapshots/`: the pinned zip;
- `data/pg/`: the local restore;
- `data/drugcentral/`: the `dc_*.csv` files, the manifest and the archive;
- `data/drugsatfda/`: 12 tables;
- `data/aact_live_20261005/`: superseded, to be deleted.


---
---

<!-- ===== Part 3: rev 9 — supplement (was: rev 9 supplement) ===== -->

# Part 3: rev 9 — supplement

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


---
---

<!-- ===== Part 4: rev 8 — supplement (was: rev 8 supplement) ===== -->

# Part 4: rev 8 — supplement

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


---
---

<!-- ===== Part 5: rev 7 — the base handoff (was: Handoff.md) ===== -->

# Part 5: rev 7 — the base handoff

# Handoff: clinical-trial success predictor — rev 7

Supersedes rev 6. Rev 6's structure and arguments stand; what changed is that **eleven
numbers in it were verified against the live CSVs and found wrong**, seven of them from one
cause, and three of its arguments were quoted off the wrong denominator. §13 is the
correction ledger — read it before quoting any figure from an earlier revision. §12 adds the
endpoint-type classifier, which is built, tested and deliberately UNSCORED.

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

### 0.1 rev 6 -> rev 7

Nothing in rev 6's architecture moved. What moved:

| | rev 6 | **rev 7** |
| --- | --- | --- |
| §3 "other numbers" block | computed on a **452,000**-trial run | recomputed on 460,569 |
| era split | 117,593 / 297,742 | **117,613 / 306,291** |
| multi-endpoint disagreement | 2,191 trials | **1,880** trials |
| `tier_min D` | 439,157 (= D + E) | **437,835** (D alone) |
| §3.1 production impact | 1,966 C / 231 D / 122 headline | **1,322 E / 521 D / 249 C / 227 headline** |
| market/endpoint overlap | 5,768 (22.8%) | **5,822 (23.0%)** |
| phase not-applicable | 231,038 (51.1%) | **236,949 (51.4%)** |
| PHASE1/PHASE2 reading | "moves 14,740 trials" | **moves 9,219 verdicts** |
| phase-1 representation gap | 5.5x (disclosure denominator) | **8.0x** on the modelling population |
| 75% market gate | "target 3 proceeds" | ceiling cleared, **gate still pending §8.1c** |
| endpoint-type gate | phase proxy, clause unfilled | **classifier built, unscored** (§12) |
| test count | 425 | **553** |

The seven §3 figures share ONE cause: that block was computed on a 452,000-trial run and
never recomputed after the pull widened to 460,569. They sum to exactly 452,000, which is
how it was found. Everything else in §3 verified exactly.

**Three live bugs found in the scripts** while verifying, all still open:
1. `validate_interval_rule.py` section 5 prints the header "by tier_min as labelled BEFORE
   the fixes" but reads the current post-fix `trial_labels.csv`. That caption is how the
   stale §3.1 breakdown survived two revisions.
2. §3.1's bug-3 table is described as "matched coverage", but the script's section 2 runs
   over all 25,683 validation rows unconditioned.
3. `trial_labels.csv.manifest.json` records alpha / broad_includes_safety / era_fallback /
   population / schema, but **not** `ratio_scale_floor`, `coverage_tolerance` or `as_of` —
   all three of which produce verdicts. A `--resume` would therefore accept a run whose
   verdict-producing flags had changed.

---

### 0.2 File manifest — what this document references that is NOT on disk

Nine paths named in this handoff do not exist in the repo. None is an error; all nine are one
of three things, and the distinction matters because two of them are blockers and the rest are
not.

**Absent from the handoff zip the assistant was given, PRESENT on the owner's machine.**
Scope matters and rev 7 got it wrong once: an opener told a fresh session not to plan around
these, which inverted the instruction. `results_raw_studies.csv` (81 MB),
`trial_entities.csv` (172 MB) and `mondo_xref.csv` all exist locally, so `--from-raw` runs,
section 3 of the posting-bias audit does not skip, and the §8.1d both-MeSH figure IS
verifiable — it was unverifiable only in the assistant's copy (lesson 56).

| path | consequence of its absence |
| --- | --- |
| `data/aact/results_raw_studies.csv` | `--from-raw` on the label pull will not run |
| `data/aact/trial_entities.csv` | section 3 of `audit_posting_bias.py` skips; the §1.2.2 both-MeSH figure cannot be re-verified |
| `data/ontology/mondo_xref.csv` | archive-era only; nothing current reads it |
| `data/chembl/drug_indication.csv` | archive-era only, and carries the Gen-1 cohort cap |

**Not yet written** (these are the work, not a packaging problem):

| path | §14 item |
| --- | --- |
| `scripts/score_endpoint_type.py` | 4 — until it exists the 0.60 kappa gate is enforced by nothing |
| `scripts/probe_drugcentral.py` | 6 — the 50% resolution gate and the 3-year market window both rest on it |

**Produced by a script that has not been run yet:**

| path | produced by |
| --- | --- |
| `data/aact/trial_design_outcomes.csv` | `scripts/pull_design_outcomes.py` (§14 item 2) |
| `data/labels/endpoint_type_sample.csv` and its key and instructions | `scripts/build_endpoint_type_sample.py` (§14 item 3) |

Two large files are excluded from any zip built by the assistant, because they are unchanged
and each is bigger than everything else combined: `data/aact/trial_labels.csv` (178 MB) and
`data/aact/results_raw_outcomes.csv` (74 MB). `trial_labels.csv.manifest.json` IS included,
since it is small and §0.1 bug 3 is about its contents.

---

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
**only 5,822 of the 25,332 market-eligible trials (23.0%) carry a headline endpoint-met
label.** For the other 19,510 the market number would be the ONLY output the tool can
produce. Complementary, not redundant, and this is the strongest argument for building
target 3 anywhere in this document. The 23.0% overlap becomes the §4.5 use-C external
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
tests/                                       test_endpoint_label (120), test_population (116),
                                             test_agreement (52), test_endpoint_type (48),
                                             test_sampling (48), test_eligibility (38),
                                             test_recon_stats (32), test_FDAAA (29),
                                             test_posting_bias (25),
                                             test_aact_aggregates (24), test_resume (21)
                                             = 553. Counted with
                                             `grep -c '^def test' tests/test_*.py`, not from
                                             memory: rev 6 carried per-file counts of
                                             116 / 102 / 23, a bracketed [120, 108]
                                             correction that was itself wrong, and the
                                             filename `test_fdaaa.py` when the file on disk
                                             is `test_FDAAA.py`. A wrong per-file count makes
                                             a MISSING file indistinguishable from a
                                             miscounted one, which is exactly the diagnosis a
                                             stale tree needs (lesson 54).
src/trial_pos/services/endpoint_type.py      the endpoint-type classifier (§12)
src/trial_pos/services/sampling.py           stratified sampling, allocation, weights
scripts/pull_design_outcomes.py              registered primary outcome text pull
scripts/build_endpoint_type_sample.py        audit + hand-labelling sample builder
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
| tier_min D | 437,545 | 438,024 | **437,835** |
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
2,319 in the union. The **live post-fix** breakdown of that union is **1,322 at tier E, 521
at tier D, 249 at tier C and 227 in the headline** (untouched — their labels came from
p-values). The rev-6 figures (1,966 C / 231 D / 122 headline) predate tier E and are
superseded: same union, different destinations.

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
classifications **2,246** entered the broad label, the other 359 already having an analysis
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

- Multi-endpoint: any_met == all_met for 19,532 of 21,412 (91.2%); **1,880 differ**. Mean
  `frac_primary_met` 0.561. **All four readings still carried, and the choice is now DUE** —
  see §8.4.
- `why_stopped`: none 420,311 / operational 15,412 / other 13,883 / business 6,541 /
  futility 2,603 / safety 1,246 / external_evidence 366 / benefit_risk 157 /
  efficacy_success 49.
- `results_posted` vs AACT's `were_results_reported`: **460,569/460,569 agreement.** Two
  independently derived definitions matching exactly — the check most worth having passed.
- Strict and broad headline were identical pre-fix (both 19,938), because broad's extra rows
  come from `why_stopped` and therefore sit at `tier_min = D`, which the A/B headline filter
  excludes by construction. Rev 2 predicted this and it is confirmed at 90× scale.
- Era (pooled, with fallback): pre_fdaaa 32,577 (7.1%) / fdaaa_801 **117,613** (25.5%) /
  final_rule **306,291** (66.5%) / unknown 4,088 (0.9%). The §6.1 table already carried
  306,291; this block disagreed with it and this block was the stale one. **Read with §6.1 in mind** — the
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

**`phase` is explicitly not-applicable for 236,949 trials (51.4%).** Over half of
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

**Ordering superseded by §14 (rev 7).** The DONE subsections below are the durable record of
what was run and what it found; read §14 for what happens next.

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
against 63.0% over all drug trials.

**READ WHAT THIS CLEARED, rev 7.** The pre-registered gate in §1.2.2 was a **drug-resolution**
rate — can this trial's intervention be resolved to a product that DrugCentral knows. What
75.0% measures is **both-MeSH presence**: the trial carries MeSH drug terms and MeSH
condition terms. Presence is a structural UPPER BOUND on resolution, never equal to it. So
the honest reading is: **the ceiling clears the gate and the gate itself is still pending
§8.1c**, which is where resolution is actually measured. This is sound as a STOP signal — had
the ceiling come in under 50%, target 3 would be dead — and weak as a GO signal. It is also
unverifiable from the current handoff zip, since `trial_entities.csv` is not in it. Treat
"target 3 proceeds" as provisional until the DrugCentral probe runs. Every source is better in this cohort than in
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
| **phase** | **35.3%** (P1 16.9% → P3 52.2%) | **23.4%** (2.0% → 25.4%) | **37.4%** (11.3% → 48.7%) |
| **lead_sponsor_class** | **51.0%** (other_gov 5.8% → fed 56.8%) | **14.4%** (1.7% → 16.1%) | **17.4%** (other 19.5% → industry 36.9%) |
| **is_fda_regulated_drug** | **51.1%** (10.6% → 61.8%) | **14.6%** | 0.8% |
| **has_us_facility** | **40.9%** (17.1% → 58.0%) | **11.4%** | 8.8% |
| has_expanded_access | **36.3%** | **21.3%** | **18.2%** (n=725 true) |
| responsible_party_type | **29.6%** | 8.4% | **16.5%** |
| era | **23.1%** (pre 20.5% → fdaaa 43.6%) | 6.5% | 2.6% |
| is_us_export | **20.4%** | 6.5% | 5.8% |
| is_fda_regulated_device | **19.5%** | 1.2% | 8.4% |

**Every stratifier is notable on the posting rate. The endpoint-met slice is not a random
sample of drug trials and cannot be treated as one.** The handoff has asserted this since
rev 2; it is now measured.

**THE DECOMPOSITION IS THE FINDING.** The two steps are driven by different things:
- `is_fda_regulated_drug` moves the posting rate by 51.1 points and the CLIFF by **0.8**.
  **Regulatory obligation decides whether you post at all, and has essentially no effect on
  whether statistics accompany it.**
- `phase` moves the posting rate by 35.3 points and the CLIFF by **37.4** — the largest
  cliff effect of any variable. **Design maturity decides whether an analysis is posted,
  conditional on posting.** A phase 1/2 trial that discloses posts an analysis 11.3% of the
  time; a phase 3 trial that discloses does so 48.7% of the time.
- `lead_sponsor_class` acts on BOTH: posting 51.0 points, cliff 17.4. NIH posts MORE than
  industry (53.8% vs 43.6%) and posts analyses LESS (cliff 21.4% vs 36.9%), so the
  conventional "industry discloses least" framing is inverted for the step this label
  depends on. Lead sponsor beats responsible party as a selector on the analysis rate
  (14.4% vs 8.4%), which settles §8.1b's open question about which entity to bin by.

That matters because most published disclosure research measures the FIRST step. **The
selection acting on THIS project's label is mostly the second**, and it is phase-driven.
A model fit on the labelled slice is fit overwhelmingly on phase 3 trials — analysis rate
25.4% against **2.0%** for phase 1 — and the product invites users to paste phase 1 trials.
**That is a transportability problem the reweighting question cannot fix, because at a 2.0%
analysis rate the phase 1 stratum has almost no observations to reweight TOWARD.**

Restated as representation, which is what the applicability domain needs: phase 3 is 17.5%
of the eligible population and **40.3%** of the labelled slice (2.31x); phase 1 is 23.2%
and **4.2%** (0.18x). By sponsor: industry 46.6% → 68.0% (1.46x), other 47.0% → 26.5%
(0.56x), other_gov 1.4% → 0.2% (0.15x).

**Consequence for the applicability domain, stated plainly:** the endpoint-met model speaks
for pivotal-phase, FDA-regulated, US-sited drug trials that posted a statistical analysis.
Every one of those four conditions is a measured selection axis, not a guess. For a phase 1
trial the honest output is a heavily flagged number or none at all, and §1.1's one-line
pattern is where that lands.

`has_us_facility` is worth noting separately: 58.0% posting against 17.1%, the largest
non-phase effect. It is also the only jurisdictional hook with usable coverage for
pre-2017 trials (9.1% unknown against 37.9% for the sponsor declarations), so it is both
the best-measured selection axis and a strong one.

**`other_gov` posts at 5.8%**, the lowest rate of any stratum in the audit, on n=1,981.
That stratum is one the model will not be able to speak for at all.

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
| applicable | 18,730 | **80.8%** | 25.1% | 31.0% |
| undeterminable | 52,330 | 51.0% | 15.7% | 30.7% |
| not applicable | 69,225 | **16.1%** | 3.7% | 23.0% |

**Posting-rate spread 64.8% — the largest of any stratifier in the entire audit. Cliff
spread 8.0%**, which is below the 10% notable threshold but NOT negligible, and the
assistant first wrote 2.7% here from a stale local file (see lesson 47).

Set beside phase (posting 35.3%, cliff 37.4%), the decomposition holds with a ratio of
about 8:1 for obligation and about 1:1 for phase:

- **Step one, whether anything is posted, is driven by LEGAL OBLIGATION.** 80.8% against
  16.1%. Nothing else in the audit comes close.
- **Step two, whether a statistical analysis accompanies it, is driven by TRIAL DESIGN.**
  The obligation moves it 8.0 points; phase moves it 37.4.

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
treated as in scope. That is a reading, not a fact. **14,740 trials carry the registration**
(4,396 applicable, 5,391 not applicable, 4,953 undeterminable), but flipping the flag moves
only **9,219 verdicts**: re-run with `PHASE1_ONLY_PHASES` inverted gives applicable
32,647 -> 28,251, undeterminable 67,129 -> 62,306, not_applicable 122,111 -> 131,330. The
other 5,521 spanning trials are decided EARLIER by era or product scope (5,391) or by an
unusable date (130), so the reading never reaches them. Quote 9,219, not 14,740. `PHASE1_ONLY_PHASES` is the one
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
(2,603) are clean negatives here — which is where the `why_stopped` work pays off, since it
cannot touch the headline endpoint-met slice. FDA approval enters as use B (§4.5).

### 8.4 Then features, then models
**DUE NOW, because scoping is settled: which multi-endpoint reading is the target?**
`label_row` sets strict from `any_primary_met`, the most permissive of the four, and `any`
differs from `all` on **1,880** trials. Recommendation: `any_primary_met` as headline with
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
    phase 1 analysis rate is 2.0% against phase 3's 25.4%. Inverse-probability weights
    would multiply a handful of phase 1 observations up to represent a huge population, and
    the resulting variance is not a correction, it is a guess with a standard error. Measure
    the selection, state the domain, and decline the extrapolation.
42. **A TWO-STEP SELECTION MUST BE DECOMPOSED OR IT IS ATTRIBUTED TO THE WRONG CAUSE.**
    Regulatory obligation drives posting (51.1-point spread) and barely touches the cliff
    (8.0). Phase barely beats it on posting (35.3) and dominates the cliff (37.4). Measured
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
    the posting rate 64.8 points and the analysis-conditional-on-posting rate 8.0. Phase
    moves them 35.3 and 37.4. Measured as a single disclosure rate, the answer would have
    been "obligation explains it" -- and this project's label depends on the OTHER step,
    the one obligation does not touch. Most published disclosure research measures the step
    that does not apply here.
45. **A CAVEAT THAT AN ESTIMATE IS BIASED UPWARD IS PART OF THE ESTIMATE.** 80.8% posting
    among confirmed-applicable trials is compliance among trials that could be CONFIRMED
    applicable -- documented, recent, mostly industry. Quoted without that, it is an FDAAA
    compliance rate, and it would be wrong.
46. **NEVER WRITE A NUMBER INTO THE HANDOFF THAT HAS NOT COME OUT OF THE OPERATOR'S RUN.**
    Slices two and three were first recorded here using figures from the assistant's local
    `trial_labels.csv`, which predated the scale / coverage / NI refusals. The posting-rate
    column was unaffected and correct; every analysis-rate and cliff figure was wrong by a
    few points, and two cells were flagged NOTABLE that are not (`has_us_facility` cliff
    10.7 -> 8.8, `is_fda_regulated_device` cliff 10.1 -> 8.4). The handoff is the durable
    artefact and rev 2 sat in the repo for two revisions teaching a dead argument, which is
    exactly how a stale number becomes a decision. Numbers enter this document only after
    they appear in a run output, and the source file must be the current one.
47. **A refusal must be counted or it vanishes.** 437,835 trials sit in tier D; 1,843 more
    would disappear into it without trace. `refusal_kind`, the per-kind counts and
    `endpoint_na_reason` exist so each refusal is a number and a stateable sentence, not an
    absence. Tier E goes further: non-inferiority trials rely on a different test, so they
    get a NAMED tier rather than being dissolved into "no analysis". Same principle as
    lesson 17, applied to a decision rather than an enum.
48. **A figure block carries the run it was computed on, and that run must be named.**
    Seven wrong numbers in §3 came from one unnamed run: the block was computed at 452,000
    trials and never recomputed when the pull widened to 460,569. They were found only
    because they still summed to 452,000. Every figure block from here on names its row
    count in its first line, so a stale block is detectable by arithmetic rather than by
    memory.
49. **A caption that lies is worse than no caption.** `validate_interval_rule.py` section 5
    is headed "as labelled BEFORE the fixes" and reads the current post-fix file. The
    caption is why a superseded breakdown was quoted for two revisions: a reader who
    checked the number against the script would have confirmed it. A caption is an
    assertion about provenance and must be tested like any other.
50. **A gate clears on the quantity it MEASURES, not the one it was written about.** The
    50% market gate was pre-registered on drug RESOLUTION and cleared at 75.0% on both-MeSH
    PRESENCE. Presence is an upper bound on resolution, so the ceiling cleared and the gate
    did not. Sound as a stop signal, weak as a go signal. Before recording a gate as
    cleared, name the quantity in the threshold and the quantity in the run output, and
    confirm they are the same quantity.
51. **A count of AFFECTED rows is not a count of CHANGED verdicts.** The PHASE1/PHASE2
    reading was quoted as moving 14,740 trials, which is how many carry the registration.
    Flipping the flag moves 9,219 verdicts; the other 5,521 are decided earlier by era or
    product scope, so the reading never reaches them. Measure a judgment by re-running with
    it inverted, not by counting the rows it could in principle touch.
52. **A denominator that is not the modelling population overstates a gap or hides it.** The
    phase-1 representation gap is 5.5x against the 140,285-trial disclosure denominator and
    **8.0x** against the 14,368-trial modelling population, where phase 1 is 423 trials
    (2.9%). The modelling population is the only denominator that describes what the model
    will be trained on. Lesson 16's family, fourth occurrence.
53. **One row per outcome is an assumption, and it fails silently.** The endpoint-type
    sample loader counted one row per outcome x ANALYSIS on the results-side file, inflating
    outcomes 148,110 against a true 120,521, inflating every class share and the multi-match
    denominator, and putting the same key into a stratum several times so it could be drawn
    twice. Found by smoke-testing on the real file; a fixture with one analysis per outcome
    would have hidden it. Enforce cardinality at the loader, and count what was collapsed.
54. **A per-file count is a diagnostic instrument, not decoration.** Rev 6's test inventory
    had three per-file counts wrong and one filename wrong while the TOTAL was right, so it
    looked fine in aggregate. When a stale code tree reached a fresh session, those per-file
    counts were the only way to tell "this file is missing" from "this file was miscounted",
    and they could not do it. Counts in the inventory are now generated by a command written
    down beside them.
55. **A zip is a snapshot with a build time, and it must state it.** A rev-7 handoff was
    shipped inside a rev-6 code tree: the document described six modules that were not
    present. The fresh session correctly REFUSED to reconstruct them, on the grounds that a
    falsification test run against reconstructed patterns certifies the reconstruction rather
    than the pre-registered rule — the §12.7 trap, one level up. Third occurrence of the
    stale-artefact fuse (lesson 11, and the rev-2-inside-rev-4 note in the header). **Before
    handing over: run the gate from INSIDE the packaged tree, quote the count, and list what
    changed since the last zip.**
56. **"Absent from the zip" is not "absent from the project".** Rev 7 §0.2 recorded
    `results_raw_studies.csv`, `trial_entities.csv` and `mondo_xref.csv` as deliberately
    excluded, and an opener told a fresh session not to plan around them. They are present on
    the owner's machine — 81 MB, 172 MB and a MONDO crosswalk — so `--from-raw` DOES run,
    section 3 of the posting-bias audit does NOT skip, and the §8.1d both-MeSH figure IS
    verifiable. Scope a statement about a missing file to the artefact it is missing from.



---

## 10. Standing discipline

- One smoke-tested script per step; pure logic in `src/` with tests, scripts as thin I/O and
  audit printouts only. **No derivations inside a script** — `agreement.py` exists because
  kappa was violating this.
- `python scripts\run_checks.py` must stay green. **Currently 553** (rev 6: 425; +32
  k-class agreement, +48 endpoint-type, +48 sampling).
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

---

## 12. THE ENDPOINT-TYPE CLASSIFIER — BUILT, TESTED, DELIBERATELY UNSCORED

### 12.0 The problem it solves

The endpoint-met label is read from a trial's own posted primary ANALYSIS: a p-value against
an alpha, or an interval against a null. That presupposes the primary endpoint was something
a threshold was applied to. Much of early-phase research reports a VALUE instead — an AUC, a
Cmax, a maximum tolerated dose — and for those trials the question "did it meet its primary
endpoint" has no answer to estimate. A probability there is not a cautious estimate, it is a
category error.

`fdaaa.compose_flag` has always taken the endpoint clause as a PARAMETER and left it
unfilled, because inventing it would assert a measurement nobody made. §12 is what fills it.

### 12.1 IT GATES PER TRIAL, NOT PER PHASE. SETTLED.

Phase is a proxy for endpoint type and leaks in both directions: a phase 1 trial with an
efficacy-shaped primary endpoint is a legitimate target, and a phase 3 trial with a
pharmacokinetic primary endpoint is not, however late its phase. A phase gate waves the
second group through and refuses the first. **Endpoint type is the variable that decides
whether the question applies, so the gate reads the endpoint.**

### 12.2 THE INPUT IS REGISTERED TEXT. LOAD-BEARING.

| field | what it is | coverage |
| --- | --- | --- |
| `design_outcomes.measure` | what the sponsor REGISTERED. Mirrors CT.gov v2 `primaryOutcomes[].measure`, which is what the live tool reads | essentially every trial |
| `outcomes.title` | what the sponsor posted WITH RESULTS, edited at posting time | only the 75,434 posting trials — **12.2%** of phase 1 drug trials |

The classifier is built on the FIRST. A gate validated on results-side titles could not be
deployed against a trial with no posted results, which is exactly the case the flag exists
for. Primary-title coverage among drug trials, for the record: PHASE1 5,506/45,093 (12.2%),
PHASE1/PHASE2 3,850/14,740 (26.1%), PHASE2 18,698/59,105 (31.6%), PHASE3 12,852/36,966
(34.8%), EARLY_PHASE1 399/4,980 (8.0%), PHASE4 7,472/30,899 (24.2%).

**`design_outcomes` is NOT among the six declared sources in `aact_aggregates.py`.** It is a
new pull, not a re-pull: `scripts/pull_design_outcomes.py`, standalone, writes
`data/aact/trial_design_outcomes.csv`, leaves `trial_labels.csv` and `trial_entities.csv`
untouched. **[DECISION]** confirmed by the owner as option (b), a standalone pull.

Key is `(nct_id, design_outcome_index)` with a `measure_sha8` beside it. `design_outcomes.id`
is queried for ORDER BY only and never written — AACT regenerates surrogate keys nightly
(lesson 1), so a file keyed on one would join cleanly against the wrong rows after any
rebuild. The hash is what makes a text change between snapshots detectable.

### 12.3 The class scheme. SETTLED.

Six classes the rule emits, plus one the labeller may use and the rule never emits:

| class | gate | why |
| --- | --- | --- |
| `bioequivalence` | not_applicable | tested, but against containment bounds rather than a null, and §11 rejects implementing that rule. **[DECISION]** its own class, not folded into PK: it already has its own promised refusal sentence in §1.1 |
| `dose_finding` | not_applicable | the result is a selected dose, not a verdict |
| `pharmacokinetic` | not_applicable | a value with an interval around itself, not tested against a threshold |
| `safety_tolerability` | not_applicable | a count or rate; where it IS compared the trial's own posted analysis carries it |
| `efficacy_shaped` | **applicable** | the shape the label was built for |
| `other` | **undeterminable** | the rule could not read it. NEVER folded into not_applicable |
| `unclear` | — | labeller-only. Maps to None, lands in `n_incomparable`, counted |

**`other` -> undeterminable is the tri-state rule applied to a product state.** "The rule
could not read the endpoint" and "the question does not apply" are different claims, and
collapsing them would let the tool tell a user the question does not apply on the strength
of a regex that simply failed to match.

`CLASS_PRECEDENCE = (bioequivalence, dose_finding, pharmacokinetic, safety_tolerability,
efficacy_shaped)` resolves a title matching several. **That order is a reading, not a
fact**, in the same family as the PHASE1/PHASE2 reading, so `matched_classes()` returns
every hit and the audit counts how often the order decided anything.

### 12.4 Roll-up: ANY, with mixed trials given their own sentence. SETTLED.

**[DECISION]** `DEFAULT_GATE_ROLLUP = ROLLUP_ANY`, because `endpoint_met_strict` is set from
`any_primary_met`. `test_default_rollup_matches_the_label_rollup` derives the ANY semantics
from `label_row`'s BEHAVIOUR, not from a constant: it builds a two-endpoint trial with one
met and one missed and asserts the strict label reads met. **If §8.4 moves the label to ALL,
that test fails and forces the gate to move with it.** Otherwise the tool would refuse
trials whose labels it happily computed in training.

**[DECISION]** mixed trials get a THIRD sentence, not one of the two existing ones:

> This trial registered 3 primary endpoints; 2 of them are pharmacokinetic measurements with
> no threshold to clear, so this estimate reflects the 1 of 3 that was tested.

Saying "the question does not apply" would be wrong and saying nothing would overstate what
the number covers. The mechanism word is chosen by PRECEDENCE, not by count, so the sentence
a user reads does not change because one more safety endpoint was registered.

### 12.5 The kappa gate. PRE-REGISTERED.

**[DECISION]** `GATE_KAPPA_MINIMUM = 0.60`, `REPORTABLE_KAPPA_MINIMUM = 0.40`, both fixed
before any hand label existed, because a number arriving without a criterion gets
rationalised into acceptability.

**The gating figure is kappa on the BINARY COLLAPSE** (`refuses_estimate`: would the tool
decline?), not the multi-class kappa. **[DECISION, with reasoning, at the owner's request]**
the decision that reaches a user is two-valued even though the classes are not. Confusing
pharmacokinetic with dose-finding is wrong about the class and RIGHT about the decision, and
blocking the feature on an error no user can see would be punishing the wrong thing.
Confusing dose-finding with efficacy-shaped is wrong about the decision and must block.
Multi-class kappa is reported beside it and gates nothing. Both come from ONE set of hand
labels via `agreement.collapse_matrix`, so they cannot disagree with each other.

The collapse's direction is stated rather than assumed: an `other` the labeller calls
pharmacokinetic counts as DISAGREEMENT, while an `other` the labeller calls efficacy-shaped
counts as agreement. That makes the collapsed figure conservative on refusals and blind to
one class of ordinary misreading — which is why the full matrix, `per_class_rates` and
`asymmetries` print beside it.

Side conditions, all pre-registered:
- disagreement must not be **one-directional**. A rule that errs one way is biased rather
  than noisy and is disqualified at any kappa (`asymmetries`, `MIN_PAIR_FOR_ASYMMETRY = 5`).
- strata below `MIN_STRATUM_FOR_VERDICT = 20` print and produce **no verdict** (lesson 29).
- **revisit 0.60 DOWNWARD if self-agreement < 0.70.** A threshold above the human ceiling is
  unreachable by construction.

### 12.6 The sample. SETTLED, sizing OPEN.

**[DECISION]** unit is one PRIMARY OUTCOME, not one trial. **[DECISION]** stratified,
**non-proportional** allocation (owner: "whatever trains the model better long term").
**[DECISION]** 40 of the 200 presented TWICE under unrelated ids, shuffled apart, to measure
the owner's own self-agreement — which is the ceiling any rule can reach against these
labels.

Allocation is square-root of stratum size with a floor, which sits between equal allocation
(best per-stratum precision) and proportional (best population estimate). Selection is by
salted sha256 of the natural key, not an RNG: reproducible on any machine and any Python
version from the printed salt alone.

**WHAT 200 LABELS BUYS, AND DOES NOT.** SE of a kappa at n=200 is ~0.055, so the 95%
interval is roughly ±0.11. A result of 0.75 clears decisively; 0.45 fails decisively; **0.63
clears on the point estimate and not on the interval, and 200 labels cannot say which side
of the line it is on.** A decisive answer anywhere in that middle band needs ~1,000 labels.
Six-class per-class recall/precision IS available at ~33 per class. The 29-cell
phase x class table is NOT: with 29 strata and a floor of 6, the floor consumes 174 of 200
and every cell lands at 6–13, so per-stratum kappa is unavailable everywhere.

**Sizing recommendations, not yet ratified by the owner:**
1. Collapse the stratification phase axis from five groups to three (phase1 / pivotal /
   other). Phase is not an input to the rule — it is in the stratification only to cover
   text styles — so three bands answer the question and free ~66 labels from the floor.
2. Boost the decisive cells to ~25 each once the real `design_outcomes` cell sizes are
   known. Unequal allocation is already handled correctly by the N/n weights, so boosting
   costs no bias, only a shift in where precision goes. **The pivotal-phase refusal cells
   are the ones worth insisting on**: a false refusal on a phase 3 trial is the tool's most
   visible possible failure, those endpoints are ~3% of pivotal outcomes, and errors in a 3%
   cell barely move the pooled figure that is supposed to protect against them.
3. **Pre-register the continuation rule NOW**: if the pooled binary kappa lands in
   0.55–0.75, draw a second tranche under a different salt excluding everything already
   labelled, and report only the combined figure. Deciding to add labels after seeing a
   number you dislike biases the result upward.
4. Report the trial-level figure on the SINGLE-endpoint subset. Free, and it is the number
   closest to what ships: 34,234 of 53,203 drug trials with posted primaries have exactly
   one primary outcome, so for those the per-outcome figure IS the trial-level figure. For
   multi-endpoint trials the ANY roll-up can amplify one error into a whole-trial flip, and
   that amplification stays **unmeasured**.

### 12.7 Two references that need no hand labelling

**The sponsor-posted analyses — NOT YET BUILT, recommended next.** For the 75,434 posting
trials, the sponsor's own primary analysis says whether a threshold was applied: a p-value or
an interval against a null means yes, a value with a CI around itself means no. That is the
same distinction, judged by the people who ran the trial, across 78,256 analysis rows, and it
is **fully independent of the keyword rule**. Runs on `results_raw_outcomes.csv` already on
disk — no new pull. Blind spot, and it is severe: posting is selected (16.4% overall, phase 3
over-represented 2.31x, phase 1 at 0.18x), so it is strongest exactly where endpoint type
matters least. It reduces the hand-labelling job to adjudicating DISAGREEMENTS — roughly
60–80 labels rather than 250 — and adjudication is the more informative use of the time.

**The gate-versus-tier cross-check — BUILT, in section 4 of the sample builder.** A trial the
gate refuses which nonetheless carries a tier A/B label is a case where the sponsor DID apply
a threshold. Runs on tens of thousands of trials rather than 200. Covers the over-refusal
direction only: the absence of a label mostly means nothing was posted, which is a disclosure
fact rather than an endpoint-type fact, so no kappa is computed on it.

**What NO automated reference can do is ratify the class scheme itself.** Every one of them
is downstream of the decision that endpoints divide into these six kinds and that four of
them mean refuse. If that carving is wrong, all of them will agree with the rule and report a
high kappa. A measurement that cannot come back negative is not a measurement. The
irreducible human step is ~25 endpoints drawn from where the references disagree: "yes,
refusing this is right" / "no, that is wrong". Fifteen minutes, once.

**The assistant labelling the sample is NOT a substitute.** It wrote `CLASS_PRECEDENCE` and
every pattern, so its labels measure whether the regex faithfully implements its own
judgment — implementation fidelity, not scheme validity. Usable as an explicitly
non-independent second rater, reported as such, never as the gate reference. Pre-labelling
for the owner to review is specifically refused: reviewing means anchoring, and the result
would be confirmation dressed as measurement. This is why the predicted class is kept out of
the labelling file.

### 12.8 Files, and what is NOT wired

New: `src/trial_pos/services/endpoint_type.py`, `src/trial_pos/services/sampling.py`,
`tests/test_endpoint_type.py` (48), `tests/test_sampling.py` (48),
`scripts/pull_design_outcomes.py`, `scripts/build_endpoint_type_sample.py`.
Modified: `src/trial_pos/services/agreement.py` (k-class core added, binary API unchanged in
behaviour and now DELEGATING to it — `test_binary_and_k_class_agree_on_a_2x2` pins that),
`tests/test_agreement.py` (+32, original 20 untouched).
**Unchanged, explicitly: `fdaaa.py`.** `compose_flag` already took the clause as a parameter.

`GATE_ONLY_FIELDS` names every field the module emits and all of them are **gate-only (R7)**:
they may decide whether an estimate is DISPLAYED and may never enter the endpoint-met feature
matrix. Two reasons — the class is derived from the same primary-outcome text the label's
analysis rows hang off, and the trial-level gate shares the label's any/all roll-up, so a
feature built from these is one revision away from being a label input and the revision would
not look like one. **[DECISION]** owner agreed: gate-only, and unavailable prospectively
anyway. `test_every_emitted_field_is_registered` fails if an output is added without
registering it. These go into the per-target leakage registry when §8.4 builds it.

**NOT BUILT: `scripts/score_endpoint_type.py`.** Deferred until labels exist. **Consequence,
stated plainly: the 0.60 threshold is not yet enforced by any code.** Nothing wires
`endpoint_clause` into `compose_flag` until that script clears it, so the clause stays
visibly missing by design.

### 12.9 Decisions settled AFTER §12.8 was written, on the sponsor-side reference

**[DECISION 1] An analysis carrying neither a p-value nor an interval is a THIRD state,**
`threshold_unknown`, counted separately and never folded into "no threshold applied." If it
were folded into "no", the sponsor reference would agree with the keyword rule everywhere the
rule says pharmacokinetic — for a reason that has nothing to do with endpoint type — and the
independence the whole reference rests on would evaporate. Tri-state rule, §10, applied one
level down.

**[DECISION 2] Several analyses on ONE outcome that disagree: ANY.** If any analysis on the
endpoint carries a threshold test, the endpoint was threshold-tested. Note this is a
DIFFERENT level from §8.4 — analyses within one outcome, not outcomes within a trial — so it
is a separate decision that happens to take the same reading. The alternative says an
endpoint stops being threshold-tested because the sponsor also posted a descriptive summary
of it.

**[DECISION 3] The sponsor reference is TIER-DERIVED, reusing `endpoint_label.py`, not a
fresh reader of the raw fields.** Tier A/B/C/E/D already encodes "did this analysis support a
verdict", and `resolve_null_value`, `contrast_family` and `ratio_scale` already handle the
bioequivalence trap where an interval sits against containment bounds rather than a null. A
second reader would reimplement all of it and get the BE case wrong — the lesson-16 adjacency
inherited by construction. Tier E and the percent-scaled ratio refusals map to
`threshold_unknown`, not to "no".

**The cost, accepted explicitly:** the reference is then no longer independent of the LABEL
pipeline. It remains independent of the KEYWORD RULE, which is the thing being validated, and
that is the independence that matters here. But any future claim that this reference validates
the label machinery itself is circular and must be refused.

**[DECISION 4] Build the falsification cross-tab FIRST**, before the full script: the keyword
gate against the tier-derived reference, reading two cells — trials the rule refuses that the
sponsor clearly threshold-tested, and the reverse. 152 phase-3 all-PK-titled trials already
carry headline A/B labels. **If the over-refusal cell is large, the six-class scheme is
carving endpoints wrongly and no amount of hand labelling rescues it.** Stop on failure rather
than proceeding to the sample.

**[DECISION 5] Sizing as recommended in §12.6**: three phase groups rather than five, decisive
cells boosted to ~25 once real cell sizes are known, continuation rule pre-registered at
0.55–0.75.

**[DECISION 6] `--duplicates 12`, not 40.** 40 repeats on ~70 adjudications is 57% overhead
spent measuring the labeller rather than the rule. Stated cost: a self-agreement kappa on 12
items has SE around 0.2, so it is a smell test rather than a measurement — it catches
inconsistent labelling, it does not give a usable ceiling. If the main kappa lands borderline,
the continuation tranche carries more repeats, which is when the ceiling is actually needed to
interpret it.

**[DECISION 7] TWO labelling frames, not one.** Cells are defined by both verdicts:

| cell | labels |
| --- | --- |
| both refuse | small audit sample |
| both allow | small audit sample |
| **rule refuses, sponsor tested** (over-refusal) | heavy |
| **rule allows, sponsor did not** (under-refusal) | heavy |
| sponsor `threshold_unknown` | original phase x class stratification |

The agreement cells get a small audit sample rather than none: **two wrong readings agreeing
with each other is the failure mode that looks exactly like success**, and a design that never
looks at agreements cannot detect it.

The sponsor reference exists only for the 24% of drug trials that posted, skewed 2.31x toward
phase 3. **The trials with no sponsor verdict are where the gate does most of its work** — a
phase 1 trial with nothing posted is the case the flag exists for — and they can be validated
only by hand labels. So the disagreement-adjudication frame sits BESIDE the original
stratification, it does not replace it. Indicative shape, to be set from real cell counts:
~50 adjudications, ~10 agreement-cell audits, ~30 on the unposted frame, ~12 repeats, so about
100 rows.


---

## 13. CORRECTION LEDGER, rev 6 -> rev 7

Verified by recomputing from `data/aact/trial_labels.csv` (460,569 rows, 2026-09-20 snapshot)
and `data/aact/results_raw_outcomes.csv` (207,481 rows), and by re-running
`audit_posting_bias.py` and `validate_interval_rule.py`.

**Reproduced EXACTLY, recorded so they are not re-verified:** 460,569 rows; results posted
75,434 (16.4%); >=1 primary analysis 24,228 (5.3%); strict labelled 21,412 at 60.7% positive;
headline 20,043 = 12,475/7,568; drug-only headline 14,368 = 8,797/5,571 at 61.2%; tiers A
18,391 / B 1,652 / C 1,369 / E 1,322; `endpoint_na_reason` 1,843 = 1,552/200/91;
`is_drug_trial` true 221,887; all of §8.1d–f cell for cell (eligibility 14,368 / 95,926 /
25,332; window 25,332 / 22,589 / 15,813; phase composition 55.8 / 19.4 / 13.9 / 10.9; FDAAA
verdicts 32,647 / 122,111 / 67,129; disclosure-inside-verdict 80.8 / 25.1 / 31.0 vs 16.1 /
3.7 / 23.0); §8.1e representation figures (phase3 17.5% -> 40.3%, 2.31x; phase1 23.2% ->
4.2%, 0.18x); all three bug tables in §3.1.

**Corrected:**

| where | rev 6 said | actual | cause |
| --- | --- | --- | --- |
| §3 era | fdaaa_801 117,593 / final_rule 297,742 | **117,613 / 306,291** | 452,000-run block; §6.1's own table already said 306,291 |
| §3 `why_stopped` | 411,786 / 15,402 / 13,865 / 6,530 / 2,599 | **420,311 / 15,412 / 13,883 / 6,541 / 2,603** | same block |
| §3 results agreement | 452,000/452,000 | **460,569/460,569** | same block |
| §3 multi-endpoint | 20,828 of 23,019 (90.5%), 2,191 differ, mean 0.567 | **19,532 of 21,412 (91.2%), 1,880 differ, mean 0.561** | same block |
| §8.3 futility | 2,599 | **2,603** | same block |
| §3 broad label | 2,244 entered | **2,246** | same block |
| §8.4 | "differs on 2,191" | **1,880** | same block |
| §3 tier table | `tier_min D` 439,157 | **437,835** | 439,157 is unlabelled = D + E; the difference is exactly the 1,322 tier-E trials, contradicting §8.1b's own account of the tier-E fix |
| lesson 47 | 439,157 in tier D | **437,835** | same |
| §1.2.3 | 5,768 of 25,332 (22.8%), leaving 19,564 | **5,822 (23.0%), leaving 19,510** | §8.1d had it right |
| §5 | phase N/A 231,038 (51.1%) | **236,949 (51.4%)** | also §5.2's own `phase_is_drug_like` false count |
| §3.1 | 1,966 C / 231 D / 122 headline | **1,322 E / 521 D / 249 C / 227 headline** | predates tier E; same union 2,319 |
| §8.1f | reading "moves 14,740 trials" | **moves 9,219 verdicts** | affected-rows vs changed-verdicts (lesson 51) |
| §8.1e / lesson 40 | phase-1 gap 5.5x | **8.0x** on the 14,368 modelling population, where phase1 = 423 trials (2.9%) | disclosure denominator vs modelling denominator (lesson 52) |
| §1.2.2 gate | "target 3 proceeds" | ceiling cleared on both-MeSH PRESENCE; **resolution gate still pending §8.1c** | lesson 50 |

**Figures that exist NOWHERE in the repo and are therefore not reproducible:** the 63% phase-1
PK figure and the 7% / 20% / 6% / 31% / 64% endpoint-type figures. They appear only as prose
in `fdaaa.py:238` and in this document; the generating script is gone. An independent crude
regex gave phase-1 PK+dose 51.3% (other 26.3%) and phase-3 trial-level all-PK 2.4% / any-PK
3.9%, which shows the figures are **rule-dependent** rather than properties of the data.
**Do not quote any of them again.** §12 is the replacement, and it is unscored until labelled.
Also found: **152 phase-3 trials with all-PK-titled primaries carrying headline A/B labels**,
so "PK endpoint" does not imply "no threshold tested".

---

## 14. NEXT ACTIONS, in order. Supersedes §8's ordering.

0. **Build the falsification cross-tab** (§12.9 decision 4). ~40 lines. If the over-refusal
   cell is large, stop: the class scheme is wrong and nothing downstream is worth building.
1. **Build the sponsor-analysis endpoint-type reference**, tier-derived per §12.9. No new
   pull, runs on data already on disk, independent of the keyword rule, and its output
   decides the labelling frame — so it comes BEFORE the `design_outcomes` pull.
2. **Run `pull_design_outcomes.py --probe-only`, then the pull.** Standalone PowerShell
   window, not VS Code (lesson 13).
3. **Run `build_endpoint_type_sample.py --audit-only`** and paste the output. The strata
   collapse (§12.6 item 1) and the cell boosts (item 2) get set from the real cell sizes in
   one edit, not guessed from the results-side file.
4. **Write `score_endpoint_type.py`.** Until it exists the 0.60 gate is not enforced by code.
5. **The ~25-endpoint scheme ratification** (§12.7). Fifteen minutes, and it is the one step
   no automated reference can replace.
6. **The DrugCentral probe, §8.1c** — `scripts/probe_drugcentral.py`, not yet written. It
   ratifies or kills the 50% resolution gate AND delivers the readout-to-approval lag
   distribution that confirms or kills the 3-year market window. That window is currently an
   assumption holding up a whole target.
7. **Fix the three script bugs in §0.1**: the lying caption, the unconditioned bug-3 table,
   and the three verdict-producing flags missing from the manifest.