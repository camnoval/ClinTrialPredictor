#!/usr/bin/env python3
"""Training-design probe (rev 11 section 7 item 5). Reads only; writes nothing. Scratch.

Answers, on the frozen labels (D-53), what the pre-model decisions still need:
  1. each target's positive rate by readout band, and endpoint met by phase (the
     phase-rate baseline, D-55) with the all-met sensitivity beside it (D-54);
  2. advancement's early bands (D-57): does the registry's start explain them? The
     recomputed W=3 exact label must equal trial_targets.csv (fail closed); then, by band,
     the share of 1s whose linking trial was ALREADY RUNNING when the focal trial
     registered, and the share of trials removed by the D-38 floor;
  3. drug history (D-57): prior registered trials sharing the trial's most-registered name
     key, by band. A registry-start truncation shows as more zeros in early bands;
  4. temporal splits (rev 7 section 4.3, services/temporal_split.py): per target and
     boundary year, train and test sizes with their events count, under both readings of
     "outcome determined", with the gain if unconfirmed starts were allowed in test;
  5. feature coverage (D-56): every fields-file column with its provenance, non-blank
     share in each target's labelled population, and its range across readout bands.

Usage (PowerShell), one line:
  python audit\\probe_training_design.py | Out-File -Encoding utf8 probe_training_design_20261001.txt
Optional: --left-cut-year Y adds advancement's split counts with readouts before Y removed.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import read_csv  # noqa: E402
from build_targets import (  # noqa: E402
    CONDITIONS_FILE, D40_WINDOW as MARKET_HEADLINE_W, ERA_BAND_YEARS, MESH_ANCESTOR_TYPE,
    MESH_LIST_TYPE, OUT_TARGETS, advancement_columns, market_columns,
)
from pull_aact_fields import OUT_FIELDS, OUT_PROVENANCE  # noqa: E402
from resolve_trial_drugs import OUT_AGENTS  # noqa: E402
from trial_pos.services.aact_fields import output_name  # noqa: E402
from trial_pos.services.advancement_label import (  # noqa: E402
    DISEASE_EXACT, PHASE_RANK, REASON_FLOOR, AdvancementIndex, TrialFacts, linked_trials,
    name_keys, trial_advancement_label,
)
from trial_pos.services.eligibility import (  # noqa: E402
    DEFAULT_ADVANCEMENT_WINDOW_YEARS, ELIGIBLE, REGISTRATION_SUBMITTED_FIELD, normalize_phase,
    readout_date,
)
from trial_pos.services.population import parse_date, tribool  # noqa: E402
from trial_pos.services.temporal_split import (  # noqa: E402
    SIDE_STRADDLER, SIDE_TEST, SIDE_TRAIN, SIDE_UNDETERMINABLE, SPLIT_REASONS, START_ACTUAL,
    START_ANY, TRAIN_RULES, split_counts, split_side,
)

csv.field_size_limit(10 ** 9)

ADV_W = DEFAULT_ADVANCEMENT_WINDOW_YEARS          # D-51 headline
LABELS_FILE = "trial_labels.csv"                  # pull_aact_results.py's output
NO_BAND = "none"
# Boundary years printed for every target. A range, not a recommendation: the split year is
# chosen from this printout.
DEFAULT_FIRST_BOUNDARY, DEFAULT_LAST_BOUNDARY = 2008, 2022
# A band's share is printed in the coverage range only from this many trials: below it the
# standard error of a share near one half exceeds 5 points, so a range would show noise.
MIN_BAND_TRIALS = 100
# Distinct values are counted up to this many, then printed as '>=' it.
DISTINCT_CAP = 1000

TARGETS = (  # (name, label column, label lag in years for the split)
    ("endpoint_met", "endpoint_met_label", 0),
    (f"advancement_w{ADV_W}_{DISEASE_EXACT}", advancement_columns(ADV_W, DISEASE_EXACT)[2],
     ADV_W),
    (f"market_w{MARKET_HEADLINE_W}", market_columns(MARKET_HEADLINE_W)[2], MARKET_HEADLINE_W),
)
ADV_NAME = TARGETS[1][0]
ADV_REASON_COL = advancement_columns(ADV_W, DISEASE_EXACT)[1]


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def band_of(d) -> str:
    if d is None:
        return NO_BAND
    b = d.year - d.year % ERA_BAND_YEARS
    return f"{b}-{b + ERA_BAND_YEARS - 1}"


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--first-boundary", type=int, default=DEFAULT_FIRST_BOUNDARY)
    ap.add_argument("--last-boundary", type=int, default=DEFAULT_LAST_BOUNDARY)
    ap.add_argument("--left-cut-year", type=int, default=None,
                    help="advancement only: also print train counts without readouts "
                         "before this year (D-57)")
    args = ap.parse_args()
    paths = {"targets": args.labels_dir / OUT_TARGETS, "agents": args.labels_dir / OUT_AGENTS,
             "labels": args.aact_dir / LABELS_FILE, "fields": args.aact_dir / OUT_FIELDS,
             "provenance": args.aact_dir / OUT_PROVENANCE,
             "conditions": args.aact_dir / CONDITIONS_FILE}
    for p in paths.values():
        if not p.exists():
            raise SystemExit(f"!! {p} not found. Nothing printed.")

    _rule("SETTINGS")
    print(f"  targets {[t[0] for t in TARGETS]} | label lags {[t[2] for t in TARGETS]} | "
          f"bands {ERA_BAND_YEARS}y | boundaries {args.first_boundary}-{args.last_boundary} | "
          f"left cut {args.left_cut_year} | min band trials {MIN_BAND_TRIALS}")

    # ---- inputs -------------------------------------------------------------------------
    readout, adv_reason, label = {}, {}, {t[0]: {} for t in TARGETS}
    for r in read_csv(paths["targets"]):         # only the fields used: the file is wide
        n = r["nct_id"]
        readout[n] = parse_date(r["readout_date"])
        adv_reason[n] = r[ADV_REASON_COL]
        for name, col, _lag in TARGETS:
            if r[col] != "":
                label[name][n] = int(r[col])
    labelled_any = set().union(*(set(v) for v in label.values()))
    trial = {}
    for r in read_csv(paths["labels"]):
        if tribool(r.get("is_drug_trial")) is True:
            trial[r["nct_id"]] = (normalize_phase(r.get("phase")), readout_date(r),
                                  parse_date(r.get("start_date")), r.get("start_date_type"),
                                  r.get("all_primary_met", ""))
    prov = {r["column"]: r for r in read_csv(paths["provenance"])}
    submitted_col = output_name("studies", REGISTRATION_SUBMITTED_FIELD)
    names = defaultdict(set)
    for r in read_csv(paths["agents"]):
        names[r["nct_id"]] |= name_keys(r["name_used"])
    mesh, ancestors = defaultdict(set), defaultdict(set)
    for r in read_csv(paths["conditions"]):
        if r.get("mesh_type") == MESH_LIST_TYPE:
            mesh[r["nct_id"]].add(r["downcase_mesh_term"])
        elif r.get("mesh_type") == MESH_ANCESTOR_TYPE:
            ancestors[r["nct_id"]].add(r["downcase_mesh_term"])

    # one pass over the fields file: first-submitted dates for every trial, coverage for
    # the labelled ones
    submitted, field_cols = {}, None
    nonblank = defaultdict(Counter)         # (target, column) -> band -> n non-blank
    distinct = defaultdict(set)              # column -> values, endpoint population
    for r in read_csv(paths["fields"]):
        n = r["nct_id"]
        submitted[n] = parse_date(r.get(submitted_col))
        if field_cols is None:
            field_cols = sorted(c for c in r if c != "nct_id")
            missing = [c for c in field_cols if c not in prov]
            if missing:
                raise SystemExit(f"!! fields columns with no provenance row: {missing[:5]}")
        if n not in labelled_any:
            continue
        band = band_of(readout[n])
        for c in field_cols:
            v = r[c].strip()
            if not v:
                continue
            for name, _col, _lag in TARGETS:
                if n in label[name]:
                    nonblank[(name, c)][band] += 1
            if n in label[TARGETS[0][0]] and len(distinct[c]) < DISTINCT_CAP:
                distinct[c].add(v)
    print(f"  targets rows {len(readout)}; drug trials {len(trial)}; fields rows "
          f"{len(submitted)}; agents with a name key {len(names)}; labelled: "
          + ", ".join(f"{k} {len(v)}" for k, v in label.items()))

    facts = {n: TrialFacts(n, ph, submitted.get(n), ro, frozenset(names.get(n, ())),
                           frozenset(mesh.get(n, ())), frozenset(ancestors.get(n, ())))
             for n, (ph, ro, _s, _st, _a) in trial.items()}
    index = AdvancementIndex(facts.values())

    # ---- 1. positive rate by readout band ----------------------------------------------
    _rule("1. POSITIVE RATE BY READOUT BAND (endpoint met also by phase: the phase-rate "
          "baseline, D-55; all-met is the D-54 sensitivity)")
    for name, _col, _lag in TARGETS:
        by = defaultdict(Counter)
        for n, y in label[name].items():
            by[band_of(readout[n])][y] += 1
        print(f"\n  {name}: labelled {len(label[name])}, positive "
              f"{_pct(sum(label[name].values()), len(label[name]))}")
        for b in sorted(by):
            c = by[b]
            print(f"    {b:10s} labelled {c[0] + c[1]:6d}  positive {_pct(c[1], c[0] + c[1])}")
    ep = TARGETS[0][0]
    by_phase, all_met = defaultdict(Counter), defaultdict(Counter)
    for n, y in label[ep].items():
        ph, ro, _s, _st, a = trial.get(n, (None, None, None, None, ""))
        by_phase[ph or NO_BAND][y] += 1
        if a.strip() in ("0", "1"):
            all_met[band_of(readout[n])][int(a)] += 1
    print(f"\n  {ep} by phase (n, any-met rate):")
    for ph in sorted(by_phase):
        c = by_phase[ph]
        print(f"    {ph:14s} {c[0] + c[1]:6d}  {_pct(c[1], c[0] + c[1])}")
    print(f"  {ep}, all-primaries-met reading on the same trials, by band:")
    for b in sorted(all_met):
        c = all_met[b]
        print(f"    {b:10s} {c[0] + c[1]:6d}  {_pct(c[1], c[0] + c[1])}")

    # ---- 2. advancement early bands ----------------------------------------------------
    _rule(f"2. ADVANCEMENT ({ADV_NAME}): recomputed label check, then the floor mechanism")
    mismatch = [n for n, y in label[ADV_NAME].items()
                if trial_advancement_label({"verdict": ELIGIBLE, "reason": ""}, facts[n],
                                           index, ADV_W, DISEASE_EXACT)["label"] != y]
    print(f"  recomputed labels differing from {OUT_TARGETS}: {len(mismatch)}")
    if mismatch:
        raise SystemExit(f"!! the probe does not reproduce the frozen label: {mismatch[:5]}")
    print("  among 1s, by band: linking trials ALREADY RUNNING (started before the focal trial "
          "was registered) all / some / none / start unknown; and the floor's share")
    mech, floor = defaultdict(Counter), defaultdict(Counter)
    for n, why in adv_reason.items():
        if why == REASON_FLOOR:
            floor[band_of(readout[n])]["floor"] += 1
    for n, y in label[ADV_NAME].items():
        b = band_of(readout[n])
        floor[b]["labelled"] += 1
        if y != 1:
            continue
        t = facts[n]
        starts = [trial.get(o.nct_id, (None, None, None))[2]
                  for o in linked_trials(t, index, ADV_W, DISEASE_EXACT).within]
        if any(s is None for s in starts):
            mech[b]["start unknown"] += 1
        else:
            running = sum(s < t.submitted for s in starts)
            mech[b]["all" if running == len(starts) else "some" if running else "none"] += 1
    for b in sorted(set(mech) | set(floor)):
        m, f = mech[b], floor[b]
        k = sum(m.values())
        print(f"    {b:10s} 1s {k:5d}  all {_pct(m['all'], k):>7s}  some {_pct(m['some'], k):>7s}"
              f"  none {_pct(m['none'], k):>7s}  unknown {_pct(m['start unknown'], k):>7s}"
              f"  | floor {f['floor']:6d} of {f['floor'] + f['labelled']:6d} "
              f"({_pct(f['floor'], f['floor'] + f['labelled'])})")

    # ---- 3. drug history ---------------------------------------------------------------
    _rule("3. DRUG HISTORY: prior registered trials sharing the most-registered name key "
          "(registered before this trial), by band")
    dates = {k: sorted(o.submitted for o in v) for k, v in index.by_key.items()}
    for name, _col, _lag in TARGETS:
        by = defaultdict(list)
        no_key = 0
        for n in label[name]:
            t = facts.get(n)
            if t is None or not t.names or t.submitted is None:
                no_key += 1
                continue
            prior = max(bisect_left(dates.get(k, []), t.submitted) for k in t.names)
            by[band_of(readout[n])].append(prior)
        print(f"\n  {name}: no name key or registration date {no_key}")
        for b in sorted(by):
            v = by[b]
            print(f"    {b:10s} n {len(v):6d}  none prior {_pct(sum(x == 0 for x in v), len(v)):>7s}"
                  f"  median {statistics.median(v):7.1f}")

    # ---- 4. temporal splits ------------------------------------------------------------
    _rule("4. TEMPORAL SPLITS (rev 7 section 4.3): train | test | dropped, events = minority "
          "class; start rule actual, '+any' = test trials gained if unconfirmed starts count")
    for name, _col, lag in TARGETS:
        for train_rule in TRAIN_RULES:
            print(f"\n  {name}, outcome determined by '{train_rule}' (label lag {lag}y):")
            print("    year   train n events  pos%  |  test n events  pos%  | straddle  "
                  "undet  +any" + ("  | train n events with left cut"
                                   if name == ADV_NAME and args.left_cut_year else ""))
            for year in range(args.first_boundary, args.last_boundary + 1):
                boundary = date(year, 1, 1)
                items, reasons, gain, cut = [], Counter(), 0, []
                for n, y in sorted(label[name].items()):
                    ro = readout[n]
                    _ph, _ro, start, stype, _a = trial.get(n, (None, None, None, None, ""))
                    side, why = split_side(ro, start, stype, boundary, lag, train_rule,
                                           START_ACTUAL)
                    items.append((side, y))
                    reasons[why] += bool(why)
                    if side != SIDE_TEST and split_side(ro, start, stype, boundary, lag,
                                                        train_rule, START_ANY)[0] == SIDE_TEST:
                        gain += 1
                    if side == SIDE_TRAIN and args.left_cut_year and ro and \
                            ro.year >= args.left_cut_year:
                        cut.append(y)
                s = split_counts(items)
                c, e = s["counts"], s["events"]
                tr, te = c[SIDE_TRAIN], c[SIDE_TEST]
                line = (f"    {year}  {tr[0] + tr[1]:7d} {e[SIDE_TRAIN]:6d} "
                        f"{_pct(tr[1], tr[0] + tr[1]):>6s} | {te[0] + te[1]:7d} {e[SIDE_TEST]:6d} "
                        f"{_pct(te[1], te[0] + te[1]):>6s} | {sum(c[SIDE_STRADDLER].values()):8d} "
                        f"{sum(c[SIDE_UNDETERMINABLE].values()):6d} {gain:5d}")
                if name == ADV_NAME and args.left_cut_year:
                    line += f"  | {len(cut):7d} {min(cut.count(0), cut.count(1)):6d}"
                print(line)
                if year == args.last_boundary:
                    print("    undeterminable reasons at the last boundary: "
                          + ", ".join(f"{k} {reasons[k]}" for k in SPLIT_REASONS))

    # ---- 5. feature coverage -----------------------------------------------------------
    _rule(f"5. FEATURE COVERAGE ({OUT_FIELDS}): non-blank share per target, overall [band "
          f"min-max over bands with >= {MIN_BAND_TRIALS} trials]; distinct values in the "
          f"{ep} population")
    band_n = {name: Counter(band_of(readout[n]) for n in label[name])
              for name, _c, _l in TARGETS}
    for provenance in sorted({prov[c]["provenance"] for c in field_cols}):
        print(f"\n  provenance {provenance}:")
        for col in [c for c in field_cols if prov[c]["provenance"] == provenance]:
            parts = []
            for name, _c, _l in TARGETS:
                counts, tot = nonblank[(name, col)], len(label[name])
                rates = [counts[b] / band_n[name][b] for b in band_n[name]
                         if band_n[name][b] >= MIN_BAND_TRIALS]
                rng = (f"[{100 * min(rates):.0f}-{100 * max(rates):.0f}]" if rates else "[-]")
                parts.append(f"{_pct(sum(counts.values()), tot):>7s} {rng:9s}")
            d = len(distinct[col])
            print(f"    {col[:52]:52s} {prov[col]['kind'][:7]:7s} " + " ".join(parts)
                  + f"  distinct {'>=' if d >= DISTINCT_CAP else ''}{d}")
    print("\n  column order of the three shares: " + ", ".join(t[0] for t in TARGETS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
