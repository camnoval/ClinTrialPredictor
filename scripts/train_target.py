#!/usr/bin/env python3
"""Train and score one target against its phase-rate reference (D-55). Thin: the split is
services/training_design.py, the features services/trial_features.py, the fitting and
metrics services/modeling.py.

Order of work, fixed before any test trial is scored:
  1. the features file must match the code's feature list, and the leakage registry must
     pass for this target, or nothing runs;
  2. labelled trials are placed by the decided split (D-57 to D-59);
  3. the model family is chosen on a VALIDATION slice of the training side (its latest
     outcomes), by Brier score; ties go to the logistic family;
  4. the chosen family is refitted on the whole training side and scored ONCE on the test
     side: Brier skill against the training-side phase rate, with a paired-bootstrap
     interval (the bar, D-55), and against the test side's own phase rate (the drift
     check, not a bar);
  5. variants on the same test trials: registration-only (D-56), with calendar (D-60), with
     entity experience (D-62), with the post-2017 fields (D-65), the other family; and for
     endpoint met the all-primaries-met label (D-54);
  6. D-67 diagnostics, beside the headline and never instead of it: the same rules at other
     cut-off dates (training_design.ROLLING_BOUNDARIES; the headline date must reproduce the
     headline exactly, or the run stops), and a mixed-era check over every labelled trial
     with drug- and sponsor-grouped folds (grouped_split.py);
  7. the FINAL model: the chosen family refitted on every labelled trial (all eras; for
     advancement after D-57's cut). It has no held-out score of its own; its expected
     performance is the headline's.

Writes data/models/<target>/predictions.csv, metrics.json and final_model.pkl (via .partial
files).

Usage (PowerShell), one line each:
  python scripts\\train_target.py --target endpoint_met | Out-File -Encoding utf8 train_endpoint_met_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np  # noqa: E402
import sklearn  # noqa: E402

from audit_posting_bias import read_csv  # noqa: E402
from build_features import LABEL_COLUMN, LABELS_FILE, OUT_FEATURES, OUT_MANIFEST  # noqa: E402
from build_targets import OUT_TARGETS  # noqa: E402
from pull_aact_fields import OUT_FIELDS  # noqa: E402
from resolve_trial_drugs import OUT_AGENTS  # noqa: E402
from trial_pos.services import grouped_split as G  # noqa: E402
from trial_pos.services import modeling as M  # noqa: E402
from trial_pos.services import reference_rates as R  # noqa: E402
from trial_pos.services import trial_features as F  # noqa: E402
from trial_pos.services import training_design as D  # noqa: E402
from trial_pos.services.aact_fields import output_name  # noqa: E402
from trial_pos.services.aact_rows import file_name  # noqa: E402
from trial_pos.services.advancement_label import name_keys  # noqa: E402
from trial_pos.services.eligibility import years_after  # noqa: E402
from trial_pos.services.population import parse_date  # noqa: E402
from trial_pos.services.temporal_split import (  # noqa: E402
    SIDE_TEST, SIDE_TRAIN, outcome_known_on,
)

csv.field_size_limit(10 ** 9)

ALL_MET_COLUMN = "all_primary_met"        # trial_labels.csv, the D-54 sensitivity label
PHASE_FEATURE = "phase"
SHOW_COEFFICIENTS = 25
MARKET_CAVEAT = (
    "EXPERIMENTAL. Market labels exist only for trials whose tested agents resolve to "
    "DrugCentral, a database of approved drugs, so labelled trials over-represent drugs later "
    "approved somewhere (Handoff section 6). Its positive rate and any skill score here do not "
    "transfer to an unselected pivotal trial.")
VARIANTS = (  # (name, groups added to the served ones; None = registration-only)
    ("registration_only", None),
    ("with_calendar", (F.GROUP_CALENDAR,)),
    ("with_entity_experience", (F.GROUP_ENTITY,)),
    ("with_post_2017_fields", (F.GROUP_LATE_FIELDS,)),
)
# D-68: served features less these. Rule fixed before the result: if the interval of its
# Brier minus the headline's includes 0 or lies below it, the counts leave the served model.
DROP_VARIANTS = (("without_registered_outcome_counts", F.REGISTERED_OUTCOME_COUNT_FEATURES),)


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


def _ci(iv) -> str:
    return f"[{iv[0]:+.4f}, {iv[1]:+.4f}]"


def _events(y) -> str:
    y = list(y)
    pos = sum(y)
    return f"n {len(y)}, positive {pos} ({100 * pos / len(y):.1f}%), events {min(pos, len(y) - pos)}"


def score(name, p, ref, oracle, y) -> dict:
    bss = M.brier_skill(p, ref, y)
    iv = M.paired_bootstrap(M.brier_skill, (p, ref, y))
    bss_o = M.brier_skill(p, oracle, y)
    iv_o = M.paired_bootstrap(M.brier_skill, (p, oracle, y))
    out = {"name": name, "brier": M.brier(p, y), "brier_skill": bss, "brier_skill_ci": iv,
           "passes_bar": M.passes_bar(bss, iv), "brier_skill_vs_test_phase_rate": bss_o,
           "brier_skill_vs_test_phase_rate_ci": iv_o, "log_loss": M.log_loss(p, y),
           "auroc": M.auroc(p, y), "ece": M.ece(p, y)}
    print(f"  {name:28s} Brier {out['brier']:.4f}  BSS {bss:+.4f} {_ci(iv)}  "
          f"bar {'PASS' if out['passes_bar'] else 'FAIL' if out['passes_bar'] is False else 'n/a'}"
          f"  | vs test phase rate {bss_o:+.4f} {_ci(iv_o)}  | log loss {out['log_loss']:.4f}"
          f"  AUROC {out['auroc']:.3f}  ECE {out['ece']:.4f}")
    return out


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, choices=F.TARGETS)
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--features-dir", type=Path, default=Path("data") / "features")
    ap.add_argument("--out-dir", type=Path, default=Path("data") / "models")
    args = ap.parse_args()
    target = args.target
    paths = {"features": args.features_dir / OUT_FEATURES,
             "manifest": args.features_dir / OUT_MANIFEST,
             "targets": args.labels_dir / OUT_TARGETS, "fields": args.aact_dir / OUT_FIELDS,
             "labels": args.aact_dir / LABELS_FILE, "agents": args.labels_dir / OUT_AGENTS,
             "sponsors": args.aact_dir / file_name("sponsors")}
    for p in paths.values():
        if not p.exists():
            raise SystemExit(f"!! {p} not found. Nothing written.")

    # ---- 1. the features file must be the code's, and the registry must pass ---------------
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    names = [f["name"] for f in manifest["features"]]
    codes = F.disease_codes_from_names(names)
    specs = F.build_specs(codes)
    if [s.name for s in specs] != names:
        raise SystemExit("!! trial_features.manifest.json does not match the code's feature "
                         "list: rebuild with scripts\\build_features.py")
    bad = F.leakage_violations(specs, target)
    if bad:
        raise SystemExit(f"!! leakage registry refuses {target}: {bad[:5]}")
    # D-69: two pre-registered attempts. Attempt 2 adds the bundle; it is ADOPTED only if its
    # best validation Brier is strictly lower than attempt 1's. Both are scored on test and
    # reported; the adopted one is the headline.
    attempts = (("attempt_1", F.served(specs)), ("attempt_2_bundle", F.served(specs, True)))

    _rule("SETTINGS")
    print(f"  target {target} | label {LABEL_COLUMN[target]} | split {D.SPLIT_BOUNDARY} "
          f"{D.TRAIN_RULE} {D.START_RULE} lag {D.LABEL_LAG_YEARS[target]}y"
          + (f" left cut {D.ADVANCEMENT_LEFT_CUT}" if target == F.TARGET_ADVANCEMENT else "")
          + f" | attempt features {[len(a_) for _n, a_ in attempts]} of {len(specs)} | "
          f"families {M.FAMILIES} | "
          f"validation last {M.VALIDATION_YEARS}y of training | bootstrap "
          f"{M.BOOTSTRAP_REPLICATES} seed {M.BOOTSTRAP_SEED}")
    print("  leakage registry: no violation")
    if target == F.TARGET_MARKET:
        print(f"\n  {MARKET_CAVEAT}")

    # ---- 2. labelled trials on the decided split -------------------------------------------
    label, readout = {}, {}
    for r in read_csv(paths["targets"]):
        if r[LABEL_COLUMN[target]] != "":
            label[r["nct_id"]] = int(r[LABEL_COLUMN[target]])
            readout[r["nct_id"]] = parse_date(r["readout_date"])
    all_met = {}
    if target == F.TARGET_ENDPOINT_MET:
        for r in read_csv(paths["labels"]):
            v = (r.get(ALL_MET_COLUMN) or "").strip()
            if r["nct_id"] in label and v in ("0", "1"):
                all_met[r["nct_id"]] = int(v)
    start_col, type_col = output_name("studies", "start_date"), output_name("studies",
                                                                            "start_date_type")
    side, start = {}, {}
    for r in read_csv(paths["fields"]):
        n = r["nct_id"]
        if n in label:
            start[n] = (parse_date(r.get(start_col)), r.get(type_col))
            side[n] = D.split_side_for(target, readout[n], *start[n])[0]
    rows = {}
    for r in read_csv(paths["features"]):
        if r["nct_id"] in label:
            rows[r["nct_id"]] = r
    train = sorted(n for n in label if side.get(n) == SIDE_TRAIN and n in rows)
    test = sorted(n for n in label if side.get(n) == SIDE_TEST and n in rows)
    if not train or not test:
        raise SystemExit(f"!! empty side: train {len(train)}, test {len(test)}")
    valid_from = years_after(D.SPLIT_BOUNDARY, -M.VALIDATION_YEARS)
    lag = D.LABEL_LAG_YEARS[target]
    valid = [n for n in train
             if outcome_known_on(readout[n], lag, D.TRAIN_RULE) >= valid_from]
    inner = [n for n in train if n not in set(valid)]
    y = lambda ids: np.array([label[n] for n in ids])            # noqa: E731
    R_ = lambda ids: [rows[n] for n in ids]                       # noqa: E731
    sides = {}
    for n in label:
        sides[side.get(n, "no_features_row")] = sides.get(side.get(n, "no_features_row"), 0) + 1
    print(f"  sides {dict(sorted(sides.items()))}")
    print(f"  train {_events(y(train))}\n  test  {_events(y(test))}\n  validation (inside train, "
          f"outcome from {valid_from}) {_events(y(valid))}; inner train {_events(y(inner))}")

    # ---- 3. the family, chosen on validation only --------------------------------------------
    _rule("3. FAMILY AND ATTEMPT, CHOSEN ON THE VALIDATION SLICE (test side untouched)")
    vref = M.rate_predict(M.rate_table([rows[n][PHASE_FEATURE] for n in inner], y(inner)),
                          [rows[n][PHASE_FEATURE] for n in valid])
    vbrier, best = {}, {}
    for aname, aspecs in attempts:
        for fam in M.FAMILIES:
            pv = M.predict(M.fit(fam, aspecs, R_(inner), y(inner)), R_(valid))
            vbrier[(aname, fam)] = M.brier(pv, y(valid))
            print(f"  {aname:18s} {fam:18s} {len(aspecs):3d} features  validation Brier "
                  f"{vbrier[(aname, fam)]:.4f}  BSS vs inner phase rate "
                  f"{M.brier_skill(pv, vref, y(valid)):+.4f}")
        best[aname] = min(M.FAMILIES, key=lambda f: (vbrier[(aname, f)], M.FAMILIES.index(f)))
    adopt2 = vbrier[(attempts[1][0], best[attempts[1][0]])] < \
        vbrier[(attempts[0][0], best[attempts[0][0]])]
    adopted = attempts[1] if adopt2 else attempts[0]
    headline, family = adopted[1], best[adopted[0]]
    with_bundle = adopt2
    print(f"  D-69 rule: attempt 2 {'ADOPTED' if adopt2 else 'NOT adopted'} (best validation "
          f"Brier {vbrier[(attempts[1][0], best[attempts[1][0]])]:.4f} against "
          f"{vbrier[(attempts[0][0], best[attempts[0][0]])]:.4f}); headline = {adopted[0]}, "
          f"family {family}. Attempts on this test side so far: 2")

    # ---- 4. headline on the test side ---------------------------------------------------------
    _rule("4. HEADLINE: chosen family, served features, scored once on the test side")
    phases_tr = [rows[n][PHASE_FEATURE] for n in train]
    phases_te = [rows[n][PHASE_FEATURE] for n in test]
    table = M.rate_table(phases_tr, y(train))
    ref = M.rate_predict(table, phases_te)
    oracle = M.rate_predict(M.rate_table(phases_te, y(test)), phases_te)
    print("  reference (training-side rate by phase; overall "
          f"{table.overall:.3f} for an unseen phase):")
    for g in sorted(table.rates):
        print(f"    {g or '(blank)':16s} n {table.counts[g]:6d}  rate {table.rates[g]:.3f}")
    head = M.fit(family, headline, R_(train), y(train))
    p = M.predict(head, R_(test))
    results = {"headline": score("headline", p, ref, oracle, y(test))}
    results["headline"]["attempt"] = adopted[0]
    score("reference itself", ref, ref, oracle, y(test))
    other_attempt = attempts[0] if adopt2 else attempts[1]
    po = M.predict(M.fit(best[other_attempt[0]], other_attempt[1], R_(train), y(train)),
                   R_(test))
    results[other_attempt[0]] = score(f"{other_attempt[0]} (not adopted)", po, ref, oracle,
                                      y(test))

    # D-69: the stronger reference, phase x disease area (printed beside; the bar is D-55's)
    code_of = lambda n: {c for c in codes if rows[n][F.disease_feature_name(c)] == "1"}  # noqa: E731
    atab = M.area_table(phases_tr, [code_of(n) for n in train], y(train))
    aref = M.area_predict(atab, phases_te, [code_of(n) for n in test])
    used = sum(1 for n in test if any((rows[n][PHASE_FEATURE], c) in atab.cells and
                                      atab.cells[(rows[n][PHASE_FEATURE], c)][1] >=
                                      M.MIN_AREA_CELL for c in code_of(n)))
    a_bss, a_iv = M.brier_skill(aref, ref, y(test)), M.paired_bootstrap(
        M.brier_skill, (aref, ref, y(test)))
    m_bss, m_iv = M.brier_skill(p, aref, y(test)), M.paired_bootstrap(
        M.brier_skill, (p, aref, y(test)))
    results["vs_phase_x_area_reference"] = {"area_ref_vs_phase_ref": a_bss,
                                            "area_ref_vs_phase_ref_ci": a_iv,
                                            "headline_vs_area_ref": m_bss,
                                            "headline_vs_area_ref_ci": m_iv,
                                            "test_trials_with_a_usable_cell": used}
    print(f"  phase x disease-area reference (cells of at least {M.MIN_AREA_CELL} training "
          f"trials; {used} of {len(test)} test trials have one): its own BSS vs the phase "
          f"rate {a_bss:+.4f} {_ci(a_iv)}; headline BSS vs it {m_bss:+.4f} {_ci(m_iv)}")
    print("  reliability (test): bin | n | mean predicted | observed")
    for lo, hi, k, mp, oy in M.reliability(p, y(test)):
        print(f"    {lo:.1f}-{hi:.1f}  {k:6d}  {mp:.3f}  {oy:.3f}")
    print("  by phase (test): n | events | BSS vs reference")
    for g in sorted(set(phases_te)):
        idx = [i for i, ph in enumerate(phases_te) if ph == g]
        yy = y(test)[idx]
        ev = min(int(yy.sum()), len(idx) - int(yy.sum()))
        print(f"    {g or '(blank)':16s} {len(idx):6d} {ev:6d}  "
              f"{M.brier_skill(p[idx], ref[idx], yy):+.4f}")
    if family == M.FAMILY_LOGISTIC:
        print(f"  largest logistic coefficients (standardised inputs), top {SHOW_COEFFICIENTS}:")
        for c, b in M.logistic_coefficients(head)[:SHOW_COEFFICIENTS]:
            print(f"    {b:+.3f}  {c}")

    # ---- 5. variants and the sensitivity -------------------------------------------------------
    _rule("5. VARIANTS ON THE SAME TEST TRIALS (difference = variant Brier minus headline; "
          "negative is better)")
    diff = lambda a, b, yy: M.brier(a, yy) - M.brier(b, yy)       # noqa: E731
    variant_specs = [(n, tuple(s for s in headline if F.is_registration_only(s)) if g is None
                      else tuple(s for s in specs if s.group in F.SERVED_GROUPS + g
                                 + (F.BUNDLE_GROUPS if with_bundle else ())))
                     for n, g in VARIANTS]
    variant_specs += [(n, tuple(s for s in headline if s.name not in set(drop)))
                      for n, drop in DROP_VARIANTS]
    other = [f for f in M.FAMILIES if f != family][0]
    for vname, vspecs in variant_specs + [(f"other_family_{other}", headline)]:
        fam = other if vname.startswith("other_family") else family
        pv = M.predict(M.fit(fam, vspecs, R_(train), y(train)), R_(test))
        results[vname] = score(vname, pv, ref, oracle, y(test))
        d, iv = diff(pv, p, y(test)), M.paired_bootstrap(diff, (pv, p, y(test)))
        results[vname]["brier_minus_headline"], results[vname]["brier_minus_headline_ci"] = d, iv
        print(f"    {len(vspecs)} features; Brier minus headline {d:+.4f} {_ci(iv)}")
        if vname in dict(DROP_VARIANTS):
            drop_them = iv[0] <= 0
            results[vname]["d68_drop_from_served"] = drop_them
            print(f"    D-68 rule: interval {'includes 0 or lies below it' if drop_them else 'lies above 0'}"
                  f" -> {'DROP the counts from the served model' if drop_them else 'KEEP them, flagged as a serving-time risk'}")
    if target == F.TARGET_ENDPOINT_MET:
        tr_a = [n for n in train if n in all_met]
        te_a = [n for n in test if n in all_met]
        ya = lambda ids: np.array([all_met[n] for n in ids])     # noqa: E731
        print(f"\n  D-54 sensitivity, all primaries met: train {_events(ya(tr_a))}; test "
              f"{_events(ya(te_a))}")
        ph_a = [rows[n][PHASE_FEATURE] for n in te_a]
        ref_a = M.rate_predict(M.rate_table([rows[n][PHASE_FEATURE] for n in tr_a], ya(tr_a)),
                               ph_a)
        orc_a = M.rate_predict(M.rate_table(ph_a, ya(te_a)), ph_a)
        pa = M.predict(M.fit(family, headline, R_(tr_a), ya(tr_a)), R_(te_a))
        results["all_primaries_met"] = score("all_primaries_met", pa, ref_a, orc_a, ya(te_a))

    # ---- 6. D-67 diagnostics -----------------------------------------------------------------
    _rule("6a. D-67 ROLLING CUT-OFF DATES: the headline's rules at other boundaries")
    rolling = {}
    for b in D.ROLLING_BOUNDARIES:
        sd = {n: D.split_side_for(target, readout[n], *start[n], boundary=b)[0]
              for n in label if n in start}
        tr = sorted(n for n in label if sd.get(n) == SIDE_TRAIN and n in rows)
        te = sorted(n for n in label if sd.get(n) == SIDE_TEST and n in rows)
        if not tr or not te or len(set(y(tr))) < 2:
            print(f"  {b}: train {len(tr)}, test {len(te)}: cannot be scored")
            continue
        rf = M.rate_predict(M.rate_table([rows[n][PHASE_FEATURE] for n in tr], y(tr)),
                            [rows[n][PHASE_FEATURE] for n in te])
        pb = M.predict(M.fit(family, headline, R_(tr), y(tr)), R_(te))
        bss, iv = M.brier_skill(pb, rf, y(te)), M.paired_bootstrap(M.brier_skill, (pb, rf, y(te)))
        rolling[b.isoformat()] = {"train": _events(y(tr)), "test": _events(y(te)),
                                  "brier_skill": bss, "brier_skill_ci": iv,
                                  "passes_bar": M.passes_bar(bss, iv)}
        print(f"  {b}: train {_events(y(tr))}\n              test  {_events(y(te))}\n"
              f"              BSS {bss:+.4f} {_ci(iv)}  bar "
              f"{'PASS' if M.passes_bar(bss, iv) else 'FAIL'}")
        if b == D.SPLIT_BOUNDARY and not math.isclose(bss, results["headline"]["brier_skill"],
                                                      rel_tol=0, abs_tol=1e-12):
            raise SystemExit("!! the headline boundary does not reproduce the headline: "
                             "the rolling diagnostic and the headline disagree on the split")

    _rule(f"6b. D-67 MIXED-ERA CHECK: {D.GROUPED_FOLDS} grouped folds over every labelled "
          "trial (sister trials kept together)")
    drug_keys = {}
    for r in read_csv(paths["agents"]):
        if r["nct_id"] in label:
            drug_keys.setdefault(r["nct_id"], set()).update(name_keys(r["name_used"]))
    sponsor = {}
    for r in read_csv(paths["sponsors"]):
        if r["nct_id"] in label and (r.get("lead_or_collaborator") or "").lower() == "lead":
            sponsor[r["nct_id"]] = F.entity_key(r.get("name"))
    pool = sorted(n for n in label if n in rows and not D.in_left_cut(target, readout[n]))
    folds = G.assign_folds(pool, drug_keys, sponsor, D.GROUPED_FOLDS)
    print(f"  pool {_events(y(pool))}; grouping tried (largest group): {list(folds.tried)}; "
          f"limit {len(pool) / D.GROUPED_FOLDS:.0f} trials")
    mixed = None
    if folds.grouping is None:
        print("  REFUSED: every grouping has one group larger than a fold; no mixed check")
    else:
        oof_p, oof_r, oof_y = np.zeros(len(pool)), np.zeros(len(pool)), y(pool)
        index = {n: i for i, n in enumerate(pool)}
        for f in range(D.GROUPED_FOLDS):
            held = [n for n in pool if folds.fold_of[n] == f]
            fit_on = [n for n in pool if folds.fold_of[n] != f]
            ii = [index[n] for n in held]
            oof_r[ii] = M.rate_predict(M.rate_table([rows[n][PHASE_FEATURE] for n in fit_on],
                                                    y(fit_on)),
                                       [rows[n][PHASE_FEATURE] for n in held])
            oof_p[ii] = M.predict(M.fit(family, headline, R_(fit_on), y(fit_on)), R_(held))
            print(f"    fold {f}: {_events(y(held))}")
        bss = M.brier_skill(oof_p, oof_r, oof_y)
        iv = M.paired_bootstrap(M.brier_skill, (oof_p, oof_r, oof_y))
        gap = bss - results["headline"]["brier_skill"]
        mixed = {"grouping": folds.grouping, "groups": folds.n_groups,
                 "largest_group": folds.largest_group, "pool": _events(oof_y),
                 "brier_skill": bss, "brier_skill_ci": iv, "minus_headline": gap}
        print(f"  grouping used: {folds.grouping} ({folds.n_groups} groups, largest "
              f"{folds.largest_group})")
        print(f"  mixed-era BSS {bss:+.4f} {_ci(iv)}; headline (temporal) "
              f"{results['headline']['brier_skill']:+.4f}; mixed minus temporal {gap:+.4f}")
        print("  reading: a mixed score far above the temporal one means the model leans on "
              "era; the temporal score is the one that predicts a new trial")

    # ---- 7. the final model -----------------------------------------------------------------
    _rule("7. FINAL MODEL: chosen family, headline features, every labelled trial")
    served_specs = headline
    for vname, drop in DROP_VARIANTS:
        if results.get(vname, {}).get("d68_drop_from_served"):
            served_specs = tuple(s_ for s_ in served_specs if s_.name not in set(drop))
            print(f"  D-68 applied: {', '.join(drop)} left out of the final model")
    final = M.fit(family, served_specs, R_(pool), y(pool))
    print(f"  {len(served_specs)} features; fitted on {_events(y(pool))}; no held-out score of "
          "its own: its expected performance is the headline's (section 4), or the D-68 "
          "variant's when D-68 applied")

    # ---- anchors ---------------------------------------------------------------------------------
    _rule("PUBLISHED RATES, beside the reference, never instead of it (D-55)")
    for a in R.anchors_for(target):
        print("  " + "\n  ".join(R.format_anchor(a)))
    _rule("LITERATURE BEHIND THE D-69 BUNDLE AND THE PHASE x AREA REFERENCE")
    for x in R.FEATURE_LITERATURE:
        print("  " + "\n  ".join(R.format_support(x)))

    # ---- write -----------------------------------------------------------------------------------
    out = args.out_dir / target
    out.mkdir(parents=True, exist_ok=True)
    pred, met, mod = out / "predictions.csv", out / "metrics.json", out / "final_model.pkl"
    tmp_p, tmp_m, tmp_k = (Path(str(x) + ".partial") for x in (pred, met, mod))
    versions = {"numpy": np.__version__, "sklearn": sklearn.__version__,
                "python": sys.version.split()[0]}
    try:
        with open(tmp_k, "wb") as f:
            pickle.dump({"target": target, "family": family, "fitted": final,
                         "features": [s_.name for s_ in served_specs], "trained_on": len(pool),
                         "split": {"boundary": D.SPLIT_BOUNDARY.isoformat(),
                                   "advancement_left_cut": D.ADVANCEMENT_LEFT_CUT.isoformat()},
                         "versions": versions}, f, protocol=pickle.HIGHEST_PROTOCOL)
        with open(tmp_p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["nct_id", "label", "reference", "headline"])
            for n, r_, h in zip(test, ref, p):
                w.writerow([n, label[n], f"{r_:.6f}", f"{h:.6f}"])
        clean = lambda v: None if isinstance(v, float) and math.isnan(v) else v  # noqa: E731
        with open(tmp_m, "w", encoding="utf-8") as f:
            json.dump({"target": target, "family": family, "adopted_attempt": adopted[0],
                       "attempts_on_this_test_side": 2,
                       "validation_brier": {f"{a}|{f}": v for (a, f), v in vbrier.items()},
                       "train_n": len(train), "test_n": len(test),
                       "results": {k: {kk: clean(vv) for kk, vv in v.items()}
                                   for k, v in results.items()},
                       "rolling": {k: {kk: clean(vv) for kk, vv in v.items()}
                                   for k, v in rolling.items()},
                       "mixed_era": None if mixed is None else
                       {k: clean(v) for k, v in mixed.items()},
                       "final_model": {"trained_on": len(pool), "versions": versions},
                       "features_manifest_inputs": manifest.get("inputs", {})},
                      f, indent=1, sort_keys=True)
        os.replace(tmp_p, pred)
        os.replace(tmp_m, met)
        os.replace(tmp_k, mod)
    except BaseException:
        for x in (tmp_p, tmp_m, tmp_k):
            x.unlink(missing_ok=True)
        raise
    _rule("WRITTEN")
    print(f"  {pred}\n  {met}\n  {mod}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
