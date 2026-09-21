#!/usr/bin/env python3
"""Step 6.3, slice one: WHO is in each target's population, and can it be matched. OFFLINE.

WHY THIS COMES BEFORE ANY POSTING RATE
======================================
6.3 exists to report posting rate by phase, era, sponsor class and FDAAA component. Every
one of those is a fraction, and a fraction needs a denominator chosen on purpose. The three
targets do not share one, so eligibility is decided first and the rates are computed inside
it. Publishing a posting rate over "all trials" and an eligibility count over "drug trials,
pivotal phase, window closed" in the same report would put two incomparable percentages
side by side.

WHAT THIS ANSWERS
=================
  1. per-target eligible populations, with every exclusion counted BY REASON
  2. the market window series 3/5/10 on the SAME pivotal population, so the horizon
     question is finally a like-for-like comparison
  3. MeSH matchability inside the market-eligible cohort -- the number the resolution gate
     has to be read against, rather than the 63.0% measured over all drug trials
  4. the overlap between the market-eligible cohort and the endpoint-met labelled slice,
     which is what decides whether target 3 is complementary or a restatement of target 1

WHAT IT DOES NOT DO
===================
No reweighting. The applicability domain is reported and the model is left speaking for
the population it was fit on. Inverse-probability weights from a posting model would let
the endpoint-met model claim it transports while importing every bias in the posting model
unstated, and a weight that cannot be checked is worse than an honest restriction.
Reweighting is its own step with its own validation, and this script deliberately produces
the INPUTS it would need rather than the weights themselves.

No approval data, so no market LABEL. This answers "could this trial carry one".

No derivations live in this file. Everything is in `trial_pos.services.eligibility` and
`trial_pos.services.population`.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# Scripts self-bootstrap src/ because run_checks.py sets PYTHONPATH itself, so the gate can
# be green while a hand-run script fails on import (lesson 9).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trial_pos.services.eligibility import (  # noqa: E402
    DEFAULT_ADVANCEMENT_WINDOW_YEARS, DEFAULT_MARKET_WINDOW_YEARS, ELIGIBLE,
    ELIGIBILITY_DOC, ELIGIBILITY_VERDICTS, MARKET_WINDOW_YEARS_REPORTED, REASON_DOC,
    TARGETS, TARGET_ENDPOINT_MET, TARGET_MARKET, eligibility_coverage,
    eligibility_for_row, eligible_for_endpoint_met, phase_class,
)
from trial_pos.services.endpoint_label import HEADLINE_TIERS  # noqa: E402
from trial_pos.services.fdaaa import (  # noqa: E402
    APPLICABLE, APPLICABILITY_VERDICTS, NOT_APPLICABLE, PHASE_SPANNING_PHASE1,
    REASON_DOC as FDAAA_REASON_DOC, UNDETERMINABLE, VERDICT_DOC,
    applicability_coverage, applicability_for_row,
)
from trial_pos.services.posting_bias import (  # noqa: E402
    DISCLOSURE_ANALYSIS, DISCLOSURE_DOC, DISCLOSURE_RESULTS_ONLY, DISCLOSURE_STAGES,
    FDAAA_POSTING_DEADLINE_MONTHS, MIN_STRATUM_FOR_RATE, NOTABLE_SPREAD, cliff_spread,
    rate_spread, stratified_disclosure,
)
from trial_pos.services.population import (  # noqa: E402
    FDAAA_COMPONENTS, JOINT_MESH_FIELDS, UNKNOWN, empty_entity_coverage,
    entity_coverage, era_for_row, merge_entity_coverage, tribool,
)

csv.field_size_limit(10 ** 9)


def _rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{100.0 * n / d:5.1f}%"


def read_csv(path: Path):
    """Stream a CSV with the csv module, never pandas.

    pandas' default na_values contains "NA" and its empty cells become float NaN, which is
    truthy. Both have already produced silent disagreements between the live pull and the
    offline re-derive in this project (lessons 5 and 6).
    """
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            yield row


def load_entities(path: Path) -> dict:
    """nct_id -> entity row. Joined on the NATURAL key, the only kind safe across AACT
    snapshots (lesson 1)."""
    if not path.exists():
        return {}
    return {row["nct_id"]: row for row in read_csv(path)}


def report_eligibility(coverage: dict) -> None:
    total = coverage["total"]
    print(f"  trials read: {total}")
    print(f"  snapshot {coverage['snapshot']}   market window "
          f"{coverage['market_window_years']}y   advancement window "
          f"{coverage['advancement_window_years']}y")
    print(f"  endpoint-met requires a headline tier: "
          f"{coverage['endpoint_headline_only']}")
    for verdict in ELIGIBILITY_VERDICTS:
        print(f"    {verdict:16s} {ELIGIBILITY_DOC[verdict]}")
    for target in TARGETS:
        bucket = coverage["targets"][target]
        print(f"\n  {target.upper()}")
        for verdict in ELIGIBILITY_VERDICTS:
            n = bucket["verdicts"][verdict]
            print(f"    {verdict:16s} {n:8d}  {_pct(n, total)}")
        if bucket["reasons"]:
            print("    excluded / undeterminable, BY REASON:")
            for reason, n in sorted(bucket["reasons"].items(), key=lambda kv: -kv[1]):
                print(f"      {reason:34s} {n:8d}  {_pct(n, total)}")
                print(f"        {REASON_DOC.get(reason, '(undocumented)')}")
    print("\n  'ineligible' alone is not a finding. A scoping exclusion will not change;")
    print("  an open window shrinks every year the snapshot advances. Read the reasons.")



# ---- the strata the applicability domain has to be stated over ------------
# Each is a function of one label row. Declared as data so the same measurement code runs
# over every one of them and they cannot drift apart, and so a stratum added later is
# reported everywhere at once.
def _era_of(row: dict) -> str:
    era, _ = era_for_row(row, True)
    return era or UNKNOWN


def _tri(value) -> str:
    verdict = tribool(value)
    return "true" if verdict is True else "false" if verdict is False else UNKNOWN


STRATIFIERS = (
    ("phase", lambda r: (r.get("phase") or UNKNOWN)),
    ("era", _era_of),
    ("lead_sponsor_class", lambda r: (r.get("lead_sponsor_class") or UNKNOWN)),
    ("responsible_party_type", lambda r: (r.get("responsible_party_type") or UNKNOWN)),
    # FDAAA_COMPONENTS is a tuple of (name, table, column, kind, why) records, not bare
    # names -- read off the constant rather than assumed, after the first version of this
    # line tried to uppercase a tuple. Only the tri-state boolean components are
    # stratifiable; `phase` is already its own stratifier above and the date component is
    # not a category.
    *[(record[0], (lambda c: (lambda r: _tri(r.get(c))))(record[0]))
      for record in FDAAA_COMPONENTS if record[3] == "tribool"],
)


def report_disclosure(rows, snapshot: date, show: int) -> None:
    print(f"  DENOMINATOR: trials with an ACTUAL primary completion whose "
          f"{FDAAA_POSTING_DEADLINE_MONTHS}-month")
    print("  posting deadline has elapsed by the snapshot. A trial still inside its")
    print("  window has not failed to post, and counting it as a non-poster would mix")
    print("  non-disclosure with not-yet-due -- a mixture that is ERA-CORRELATED and")
    print("  would manufacture the very gradient this section exists to detect.")
    print("\n  THREE stages, because the label needs a posted ANALYSIS and the gap")
    print("  between that and posting at all is the coverage cliff:")
    for stage in DISCLOSURE_STAGES:
        print(f"    {stage:26s} {DISCLOSURE_DOC[stage]}")

    for name, key in STRATIFIERS:
        out = stratified_disclosure(rows, key, snapshot)
        strata = out["strata"]
        if not strata:
            continue
        posting = rate_spread(strata, DISCLOSURE_RESULTS_ONLY)
        analysis = rate_spread(strata, DISCLOSURE_ANALYSIS)
        cliff = cliff_spread(strata)
        print(f"\n  BY {name.upper()}   "
              f"(denominator {out['n_in_denominator']}, excluded "
              f"{out['excluded']['deadline_not_elapsed']} not-yet-due / "
              f"{out['excluded']['readout_not_actual']} no-actual-date)")
        print(f"    {'stratum':28s} {'n':>8s} {'posted':>9s} {'analysis':>9s} "
              f"{'cliff':>9s}")
        ordered = sorted(strata.items(), key=lambda kv: -sum(kv[1].values()))
        for stratum, bucket in ordered[:show]:
            n = sum(bucket.values())
            disclosed = (bucket[DISCLOSURE_RESULTS_ONLY] + bucket[DISCLOSURE_ANALYSIS])
            post_rate = analysis_rate = cliff_rate = "n/a"
            if n:
                post_rate = f"{100.0 * disclosed / n:8.1f}%"
                analysis_rate = f"{100.0 * bucket[DISCLOSURE_ANALYSIS] / n:8.1f}%"
            if disclosed:
                cliff_rate = f"{100.0 * bucket[DISCLOSURE_ANALYSIS] / disclosed:8.1f}%"
            flag = " " if n >= MIN_STRATUM_FOR_RATE else "*"
            print(f"    {stratum:28s} {n:8d} {post_rate:>9s} {analysis_rate:>9s} "
                  f"{cliff_rate:>9s}{flag}")
        if len(ordered) > show:
            print(f"    ... {len(ordered) - show} further strata not shown")
        print(f"    * below {MIN_STRATUM_FOR_RATE} trials: reported, excluded from spreads")
        for label, result in (("posting rate", posting), ("analysis rate", analysis),
                              ("the CLIFF, conditional", cliff)):
            if result["spread"] is None:
                print(f"    {label:24s} spread: not measurable (too few strata)")
                continue
            marker = "  <-- NOTABLE" if result["spread"] >= NOTABLE_SPREAD else ""
            print(f"    {label:24s} spread {result['spread']:6.1%}   "
                  f"low {result['lowest'][0]} {result['lowest'][1]:.1%}   "
                  f"high {result['highest'][0]} {result['highest'][1]:.1%}{marker}")
    print(f"\n  A spread at or above {NOTABLE_SPREAD:.0%} means the disclosed slice is")
    print("  GRADED on that variable and the applicability domain must name it. The")
    print("  CLIFF row is the one that matters most: a variable can leave the posting")
    print("  rate flat and still decide the label's availability entirely by acting only")
    print("  on the second step, and only the conditional rate separates the two.")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-target eligible populations and MeSH matchability. Reads only.")
    parser.add_argument("--labels", default=Path("data/aact/trial_labels.csv"), type=Path)
    parser.add_argument("--entities", default=Path("data/aact/trial_entities.csv"),
                        type=Path)
    parser.add_argument("--snapshot", required=True,
                        help="ISO date fixing 'now' for every window. REQUIRED and never "
                             "read from the clock: the same trial's eligibility would "
                             "otherwise change with the day the script ran (lesson 15).")
    parser.add_argument("--market-window", type=int,
                        default=DEFAULT_MARKET_WINDOW_YEARS,
                        help="years from readout within which approval counts. Produces "
                             "verdicts, so it is a flag.")
    parser.add_argument("--advancement-window", type=int,
                        default=DEFAULT_ADVANCEMENT_WINDOW_YEARS)
    parser.add_argument("--show", type=int, default=10,
                        help="strata listed per cross-tab before truncation")
    parser.add_argument("--include-tier-c", action="store_true",
                        help="count interval-only (tier C) verdicts as endpoint-met "
                             "eligible. OFF by default: tier C is a sensitivity stratum, "
                             "not the modelling population.")
    args = parser.parse_args()
    snapshot = date.fromisoformat(args.snapshot)

    _rule("SETTINGS -- every value that changes a number below")
    print(f"  labels             : {args.labels}")
    print(f"  entities           : {args.entities}")
    print(f"  snapshot ('now')   : {snapshot}")
    print(f"  market window      : {args.market_window} years")
    print(f"  advancement window : {args.advancement_window} years")
    if not args.labels.exists():
        raise SystemExit(f"!! not found: {args.labels}")
    entities = load_entities(args.entities)
    if not entities:
        print(f"\n  !! {args.entities} not found -- MeSH matchability will be skipped.")
        print("     Run the pull to produce it; the eligibility sections still hold.")

    rows = list(read_csv(args.labels))

    _rule("1. ELIGIBILITY BY TARGET (the denominator, decided before any rate)")
    report_eligibility(eligibility_coverage(rows, snapshot, args.market_window,
                                            args.advancement_window,
                                            not args.include_tier_c))
    if not args.include_tier_c:
        loose = eligibility_coverage(rows, snapshot, args.market_window,
                                     args.advancement_window, headline_only=False)
        tight = sum(1 for r in rows
                    if eligible_for_endpoint_met(r)["verdict"] == ELIGIBLE)
        wide = loose["targets"][TARGET_ENDPOINT_MET]["verdicts"][ELIGIBLE]
        print(f"\n  tier-C SENSITIVITY STRATUM: {wide - tight} additional drug trials")
        print("  carry a strict label from an interval-only verdict. Reported, and NOT")
        print("  in the modelling population: tier C under-calls positives relative to")
        print("  A/B, so pooling would shift the base rate. --include-tier-c to fold in.")

    _rule("2. MARKET WINDOW SERIES (3/5/10 on the SAME pivotal population)")
    print("  The horizon question, finally like-for-like. 3 vs 10 across DIFFERENT")
    print("  populations was not a comparison; 3 vs 5 on one pivotal population is.")
    print("  A flat series vindicates the intuition that the horizon does not matter.")
    print("  A steep one is a finding about how long approval takes, not about the flag.")
    print(f"\n    {'window':>8} {'market-eligible':>18}")
    for window in sorted(MARKET_WINDOW_YEARS_REPORTED):
        n = sum(1 for r in rows
                if eligibility_for_row(r, snapshot, window)[TARGET_MARKET]["verdict"]
                == ELIGIBLE)
        print(f"    {str(window) + 'y':>8} {n:18d}")
    print("\n  Eligibility is MONOTONE in the window: widening it can only close fewer")
    print("  trials' windows, so this series must be non-increasing. If it is not, the")
    print("  window arithmetic is wrong and nothing below can be trusted.")

    _rule("3. MeSH MATCHABILITY INSIDE THE MARKET-ELIGIBLE COHORT")
    print("  The 63.0% both-MeSH figure was measured over ALL drug trials. This cohort is")
    print("  pivotal-phase with a closed window, so its denominator is different and its")
    print("  MeSH coverage is a separate question -- older trials have had longer to be")
    print("  indexed, and indexing practice has also changed. Measured, not assumed.")
    if not entities:
        print("\n  (skipped: no entity file)")
    else:
        strata = {"market_eligible": empty_entity_coverage(),
                  "all_drug_trials": empty_entity_coverage()}
        era_counts: dict = {}
        for row in rows:
            entity = entities.get(row.get("nct_id"))
            if entity is None:
                continue
            is_drug = tribool(row.get("is_drug_trial")) is True
            if not is_drug:
                continue
            joined = {**entity, "is_drug_trial": True}
            one = entity_coverage([joined])
            strata["all_drug_trials"] = merge_entity_coverage(
                strata["all_drug_trials"], one)
            verdict = eligibility_for_row(row, snapshot,
                                          args.market_window)[TARGET_MARKET]["verdict"]
            if verdict != ELIGIBLE:
                continue
            strata["market_eligible"] = merge_entity_coverage(
                strata["market_eligible"], one)
            era, _ = era_for_row(row, True)
            key = era or UNKNOWN
            bucket = era_counts.setdefault(key, empty_entity_coverage())
            era_counts[key] = merge_entity_coverage(bucket, one)

        print(f"\n  {'':28s} {'all drug trials':>18s} {'MARKET-ELIGIBLE':>18s}")
        left, right = strata["all_drug_trials"], strata["market_eligible"]
        print(f"  {'':28s} {'(n=' + str(left['drug_total']) + ')':>18s} "
              f"{'(n=' + str(right['drug_total']) + ')':>18s}")
        for field in left["drug_present"]:
            a, b = left["drug_present"][field], right["drug_present"][field]
            print(f"    {field:26s} {a:9d} {_pct(a, left['drug_total']):>8s} "
                  f"{b:9d} {_pct(b, right['drug_total']):>8s}")
        print(f"    {'BOTH MeSH sides':26s} "
              f"{left['drug_both_mesh']:9d} "
              f"{_pct(left['drug_both_mesh'], left['drug_total']):>8s} "
              f"{right['drug_both_mesh']:9d} "
              f"{_pct(right['drug_both_mesh'], right['drug_total']):>8s}")
        print("\n  THE GATE NUMBER is 'BOTH MeSH sides' in the MARKET-ELIGIBLE column.")
        print("  It is still a CEILING: it bounds code-to-code matching before")
        print("  DrugCentral is involved at all. The realised rate is this, times the")
        print("  MeSH-to-DrugCentral entity rate, times the MeSH-to-UMLS crosswalk")
        print("  coverage, with DrugCentral's own error rate on top of whatever survives.")

        print("\n  by era within the market-eligible cohort:")
        for era, cov in sorted(era_counts.items(),
                               key=lambda kv: -kv[1]["drug_total"]):
            print(f"    {era:14s} n={cov['drug_total']:7d}  both-MeSH "
                  f"{cov['drug_both_mesh']:7d}  "
                  f"{_pct(cov['drug_both_mesh'], cov['drug_total'])}")
        print("  A coverage gradient across eras means the market model's matchable")
        print("  population is era-selected on TOP of being disclosure-selected, and the")
        print("  applicability domain has to say so.")

    _rule("4. MARKET vs ENDPOINT-MET: complementary, or a restatement?")
    print("  Target 3 earns a place on screen only if it answers trials target 1 cannot.")
    print("  If almost every market-eligible trial also carries an endpoint label, the")
    print("  third number is mostly the first one plus regulatory noise.")
    overlap = Counter()
    for row in rows:
        verdict = eligibility_for_row(row, snapshot,
                                      args.market_window)[TARGET_MARKET]["verdict"]
        if verdict != ELIGIBLE:
            continue
        overlap["market_eligible"] += 1
        if (row.get("tier_min") or "") in HEADLINE_TIERS:
            overlap["also_headline_labelled"] += 1
    n = overlap["market_eligible"]
    both = overlap["also_headline_labelled"]
    print(f"\n    market-eligible                  : {n:8d}")
    print(f"    of those, headline endpoint label: {both:8d}  {_pct(both, n)}")
    print(f"    market-ONLY (no endpoint label)  : {n - both:8d}  {_pct(n - both, n)}")
    print("\n  The market-ONLY figure is the answer. Those trials get a number from")
    print("  target 3 and from nothing else, which is the case FOR building it. The")
    print("  overlap becomes the post-hoc external validation set (approval as use C),")
    print("  never a training input.")

    _rule("5. PHASE COMPOSITION (what the restrictions actually removed)")
    composition = Counter()
    for row in rows:
        if tribool(row.get("is_drug_trial")) is not True:
            continue
        composition[phase_class(row.get("phase"))] += 1
    drug_total = sum(composition.values())
    for klass, count in sorted(composition.items(), key=lambda kv: -kv[1]):
        print(f"    {klass:16s} {count:8d}  {_pct(count, drug_total)}")
    print(f"    {'drug trials':16s} {drug_total:8d}")
    print("\n  post_approval is excluded from market and advancement and KEPT for")
    print("  endpoint-met: whether a phase 4 trial cleared its own primary analysis is a")
    print("  real question, while its drug's approval answers the other two in advance.")

    _rule("6. DISCLOSURE RATE BY STRATUM -- is the labelled slice selected?")
    print("  This is the project's credibility. The endpoint-met label exists for 14,368")
    print("  of 221,887 drug trials. If disclosure is flat across these variables the")
    print("  slice is near-random and the model can claim to transport; if it is graded,")
    print("  the model speaks for a sub-population and the domain has to say which.")
    print("  Computed INSIDE the endpoint-met scope (drug trials only), because a rate")
    print("  over all 460,569 would answer a question no model asks.")
    drug_rows = [r for r in rows if tribool(r.get("is_drug_trial")) is True]
    print(f"\n  drug trials: {len(drug_rows)}")
    report_disclosure(drug_rows, snapshot, args.show)

    _rule("7. FDAAA APPLICABILITY -- was this trial REQUIRED to post?")
    print("  Written once, here and nowhere else. The pull carries the components and")
    print("  refuses the verdict, because deciding it inside a pull would bury a judgment")
    print("  where nobody could audit it; a test fails if the pull imports this module.")
    print("\n  THE RULE IS ONE-DIRECTIONAL, and that is its main finding. The statute's")
    print("  three jurisdictional hooks are IND/IDE, a US study site, and US-manufactured")
    print("  export. AACT records the last two and NOT the first. So a visible hook gives")
    print("  a positive verdict, while NO visible hook cannot give a negative one -- the")
    print("  trial may have run under an IND. Confident NOT-APPLICABLE comes only from")
    print("  conditions AACT sees fully: the era, the phase-1 exclusion, product scope.")
    for verdict in APPLICABILITY_VERDICTS:
        print(f"\n    {verdict}: {VERDICT_DOC[verdict]}")

    cov = applicability_coverage(drug_rows)
    print(f"\n  over {cov['total']} drug trials:")
    for verdict in APPLICABILITY_VERDICTS:
        n = cov["verdicts"][verdict]
        print(f"    {verdict:18s} {n:8d}  {_pct(n, cov['total'])}")
    print("\n  by deciding condition:")
    for reason, n in sorted(cov["reasons"].items(), key=lambda kv: -kv[1]):
        print(f"    {reason:38s} {n:8d}  {_pct(n, cov['total'])}")
        print(f"      {FDAAA_REASON_DOC.get(reason, '(undocumented)')}")
    spanning = cov["phase_spanning_phase1"]
    print(f"\n  SENSITIVITY: {sorted(PHASE_SPANNING_PHASE1)} is treated as IN scope,")
    print("  because the statute excludes a study that is phase 1 ONLY. That is a")
    print(f"  READING, not a fact, and it moves {spanning['total']} trials:")
    for verdict, n in spanning["verdict"].items():
        if n:
            print(f"    {verdict:18s} {n:8d}")

    print("\n  DISCLOSURE RATE INSIDE THE VERDICT -- the reason for having one at all.")
    print("  Among trials REQUIRED to post, silence is non-compliance and is plausibly")
    print("  correlated with the result. Among trials under no obligation, silence is a")
    print("  free choice and says much less. If the two groups disclose at the same rate,")
    print("  the obligation is not what drives disclosure and this section is decoration.")
    out = stratified_disclosure(drug_rows,
                                lambda r: applicability_for_row(r)["verdict"],
                                snapshot)
    strata = out["strata"]
    print(f"\n    {'verdict':20s} {'n':>8s} {'posted':>9s} {'analysis':>9s} {'cliff':>9s}")
    for verdict in APPLICABILITY_VERDICTS:
        bucket = strata.get(verdict)
        if not bucket:
            continue
        n = sum(bucket.values())
        disclosed = bucket[DISCLOSURE_RESULTS_ONLY] + bucket[DISCLOSURE_ANALYSIS]
        print(f"    {verdict:20s} {n:8d} {100.0 * disclosed / n:8.1f}% "
              f"{100.0 * bucket[DISCLOSURE_ANALYSIS] / n:8.1f}% "
              f"{(100.0 * bucket[DISCLOSURE_ANALYSIS] / disclosed) if disclosed else 0:8.1f}%")
    for label, result in (("posting rate", rate_spread(strata, DISCLOSURE_RESULTS_ONLY)),
                          ("analysis rate", rate_spread(strata, DISCLOSURE_ANALYSIS)),
                          ("the CLIFF, conditional", cliff_spread(strata))):
        if result["spread"] is None:
            print(f"    {label:24s} spread: not measurable")
            continue
        marker = "  <-- NOTABLE" if result["spread"] >= NOTABLE_SPREAD else ""
        print(f"    {label:24s} spread {result['spread']:6.1%}   "
              f"low {result['lowest'][0]} {result['lowest'][1]:.1%}   "
              f"high {result['highest'][0]} {result['highest'][1]:.1%}{marker}")

    _rule("STILL MISSING BEFORE A MODEL")
    print("  - readout-to-approval lag, which is what confirms or kills the 3-year window")
    print("    on evidence rather than on reasoning. Needs DrugCentral.")
    print("  - reweighting, deliberately NOT done here. See the module docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())