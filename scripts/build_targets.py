#!/usr/bin/env python3
"""Labels for all three targets: endpoint met, market (with the D-10 lag), advancement.
Reads only; writes its own two files into --out-dir:

  trial_targets.csv                one row per trial in trial_labels.csv
  trial_targets.manifest.json      inputs (with hashes), settings, counts

Endpoint met: services/eligibility.eligible_for_endpoint_met and its strict label, exactly as
audit_posting_bias.py computes them (same loaders). Market: services/market_label, for every
window in MARKET_WINDOW_YEARS_REPORTED, over trial_drug_form_approvals.csv. Advancement:
services/advancement_label, for every window in ADVANCEMENT_WINDOW_YEARS_REPORTED and both
disease rules (D-37 exact headline, family sensitivity; D-38 floor).

--snapshot is REQUIRED (lesson 15) and must not be later than either source's currency:
the AACT pull's as_of and Drugs@FDA's latest approval. A window cannot close past the data.

Usage (PowerShell), one per block:
  python scripts\\build_targets.py --snapshot 2026-10-01 |
      Out-File -Encoding utf8 build_targets_20261001.txt
  (one line in PowerShell)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import load_primary_classes, load_submitted_dates, read_csv  # noqa: E402
from pull_aact_results import manifest_path  # noqa: E402  the writer's own naming rule
from match_form_approvals import OUT_APPROVALS, OUT_MANIFEST as APPROVALS_MANIFEST  # noqa: E402
from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, OUT_AGENTS, OUT_TRIALS, PARTIAL, PURPLEBOOK_FILE, sha256_of,
)
from trial_pos.services.drug_dictionary import parse_struct_id  # noqa: E402
from trial_pos.services.drug_resolution import DRUG_SEP  # noqa: E402
from trial_pos.services.drugsatfda import APPROVED, decode, parse_date, parse_tab  # noqa: E402
from trial_pos.services.eligibility import (  # noqa: E402
    ELIGIBILITY_VERDICTS, ELIGIBLE, MARKET_WINDOW_YEARS_REPORTED, eligible_for_endpoint_met,
    eligible_for_market, merge_gate_exclusion, merge_registration_timing, phase_class,
    readout_date, strict_outcome, years_after, ADVANCEMENT_WINDOW_YEARS_REPORTED,
    DEFAULT_ADVANCEMENT_WINDOW_YEARS, REGISTRATION_SUBMITTED_FIELD, eligible_for_advancement,
    normalize_phase,
)
from trial_pos.services.advancement_label import (  # noqa: E402
    DISEASE_EXACT, DISEASE_FAMILY, DISEASE_RULES, TARGET_NAME as ADV_TARGET, AdvancementIndex,
    TrialFacts, name_keys, trial_advancement_label,
)
from trial_pos.services.population import parse_date, tribool  # noqa: E402
from trial_pos.services.purplebook import (  # noqa: E402
    purple_book_id, purple_book_identity_key, read_purplebook,
)
from trial_pos.services.form_approval import (  # noqa: E402
    STATUS_NOT, before_status, fda_approval_dates, parse_approval_row,
)
from trial_pos.services.market_label import (  # noqa: E402
    is_loose_regimen,
    BOUNDS, D10_LOWER_YEARS, D10_UPPER_YEARS, LABEL_NEGATIVE, LABEL_POSITIVE,
    LAG_DECISION_PERCENTILE, TARGET_NAME, decision_line, decisive_cases,
    identity_key, lag_summary, trial_market_label,
)

csv.field_size_limit(10 ** 9)

DEFAULT_SHOW = 25
PIVOTAL = "pivotal"
SUBMISSIONS = "Submissions.txt"
FDA_AGENCY = "FDA"                 # DrugCentral approval.type token
D40_WINDOW = 5                     # D-36 headline
OUT_TARGETS = "trial_targets.csv"
CONDITIONS_FILE = "rows__browse_conditions.csv"
MESH_LIST_TYPE, MESH_ANCESTOR_TYPE = "mesh-list", "mesh-ancestor"
OUT_TARGETS_MANIFEST = "trial_targets.manifest.json"
FOLLOW_UP_YEARS = (10, 5)          # D-10: H=10 headline, H=5 sensitivity
LAG_WINDOWS = (D10_LOWER_YEARS, D10_UPPER_YEARS)
# Readout-year bands for the censoring check: a positive rate that falls toward recent
# readouts would mean the window or a source's currency is labelling "not yet" as 0.
ERA_BAND_YEARS = 3


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def moieties(row: dict) -> list:
    return [int(x) for x in row["drugs"].split(DRUG_SEP) if x]


def market_columns(w: int) -> tuple:
    return (f"market_w{w}_verdict", f"market_w{w}_reason", f"market_w{w}_label")


def advancement_columns(w: int, rule: str) -> tuple:
    return tuple(f"advancement_w{w}_{rule}_{x}" for x in ("verdict", "reason", "label"))


TARGET_COLUMNS = (("nct_id", "phase_class", "readout_date", "endpoint_met_verdict",
                   "endpoint_met_reason", "endpoint_met_label")
                  + tuple(c for w in MARKET_WINDOW_YEARS_REPORTED for c in market_columns(w))
                  + tuple(c for w in ADVANCEMENT_WINDOW_YEARS_REPORTED for rule in DISEASE_RULES
                          for c in advancement_columns(w, rule)))


def drugsatfda_currency(path: Path) -> date:
    if not path.exists():
        raise SystemExit(f"!! {path} not found")
    text, _enc = decode(path.read_bytes())
    dates = [parse_date(r.get("SubmissionStatusDate")) for r in parse_tab(text)["rows"]
             if r.get("SubmissionStatus") == APPROVED]
    dates = [d for d in dates if d is not None]
    if not dates:
        raise SystemExit(f"!! {path} has no dated approval")
    return max(dates)


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True,
                    help="ISO date fixing 'now' for every window (lesson 15)")
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--purplebook", type=Path,
                    default=Path("data") / "purplebook" / PURPLEBOOK_FILE)
    ap.add_argument("--out-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    snapshot = date.fromisoformat(args.snapshot)
    outs = {k: args.out_dir / k for k in (OUT_TARGETS, OUT_TARGETS_MANIFEST)}
    if any(p.exists() for p in outs.values()) and not args.overwrite:
        raise SystemExit("!! outputs exist; pass --overwrite: "
                         + ", ".join(str(p) for p in outs.values() if p.exists()))
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    labels_path = args.aact_dir / "trial_labels.csv"
    fields_path = args.aact_dir / "trial_registration_fields.csv"
    titles_path = args.aact_dir / "trial_design_outcomes.csv"
    resolution_path = args.labels_dir / OUT_TRIALS
    approvals_path = args.labels_dir / OUT_APPROVALS
    for p in (labels_path, fields_path, titles_path, resolution_path, approvals_path):
        if not p.exists():
            raise SystemExit(f"!! {p} not found")
    labels_manifest = manifest_path(labels_path)    # 'trial_labels.csv.manifest.json'
    if not labels_manifest.exists():
        raise SystemExit(f"!! {labels_manifest} not found: the AACT as_of date bounds the "
                         "snapshot. Nothing written.")
    as_of = json.loads(labels_manifest.read_text(encoding="utf-8")).get("as_of")
    aact_currency = date.fromisoformat(as_of) if as_of else None
    fda_currency = drugsatfda_currency(args.fda_dir / SUBMISSIONS)
    approvals_manifest = json.loads((args.labels_dir / APPROVALS_MANIFEST)
                                    .read_text(encoding="utf-8"))

    _rule("SETTINGS")
    print(f"  snapshot {snapshot} | AACT as_of {aact_currency} | Drugs@FDA latest approval "
          f"{fda_currency} | market windows {MARKET_WINDOW_YEARS_REPORTED} | lag follow-up "
          f"{FOLLOW_UP_YEARS} | show {args.show}")
    print(f"  approvals file settings: {approvals_manifest.get('settings')}")
    if aact_currency is None or snapshot > min(aact_currency, fda_currency):
        raise SystemExit(f"!! snapshot {snapshot} is later than a source's currency "
                         f"(AACT {aact_currency}, Drugs@FDA {fda_currency}). Nothing written.")

    submitted = load_submitted_dates(fields_path)
    rows = merge_registration_timing(read_csv(labels_path), submitted)
    rows = merge_gate_exclusion(rows, load_primary_classes(titles_path))
    match_class = {r["nct_id"]: r["match_class"] for r in read_csv(resolution_path)}
    agents = defaultdict(list)
    for r in read_csv(approvals_path):
        agents[r["nct_id"]].append((r, parse_approval_row(r)))
    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    name_of = {parse_struct_id(r["id"]): r["name"]
               for r in read_csv(args.dc_dir / m["tables"]["structures"]["file"])}
    approval_rows = read_csv(args.dc_dir / m["tables"]["approval"]["file"])
    fda_first, _undated = fda_approval_dates(approval_rows, FDA_AGENCY)
    if not args.purplebook.exists():
        raise SystemExit(f"!! {args.purplebook} not found (D-46 names). Nothing written.")
    with args.purplebook.open(encoding="utf-8", newline="") as f:
        for r in read_purplebook(csv.DictReader(f))[0]:     # D-46 identities print by name
            ik = purple_book_identity_key(r.proper)
            name_of.setdefault(purple_book_id(ik), ik)

    # ---- advancement facts (D-37): every drug trial, as a focal trial and a later rung ----
    agent_names = defaultdict(set)
    for r in read_csv(args.labels_dir / OUT_AGENTS):
        agent_names[r["nct_id"]] |= name_keys(r["name_used"])
    mesh, ancestors = defaultdict(set), defaultdict(set)
    for r in read_csv(args.aact_dir / CONDITIONS_FILE):
        if r.get("mesh_type") == MESH_LIST_TYPE:
            mesh[r["nct_id"]].add(r["downcase_mesh_term"])
        elif r.get("mesh_type") == MESH_ANCESTOR_TYPE:
            ancestors[r["nct_id"]].add(r["downcase_mesh_term"])
    facts = {}
    for row in rows:
        if tribool(row.get("is_drug_trial")) is True:
            n = row["nct_id"]
            facts[n] = TrialFacts(n, normalize_phase(row.get("phase")),
                                  parse_date(row.get(REGISTRATION_SUBMITTED_FIELD)),
                                  readout_date(row), frozenset(agent_names.get(n, ())),
                                  frozenset(mesh.get(n, ())), frozenset(ancestors.get(n, ())))
    adv_index = AdvancementIndex(facts.values())
    adv = {(w, rule): {"verdict": Counter(), "reason": Counter(), "label": Counter(),
                       "era": defaultdict(Counter)}
           for w in ADVANCEMENT_WINDOW_YEARS_REPORTED for rule in DISEASE_RULES}

    def names(drugs: str) -> str:
        return " + ".join(sorted(name_of.get(int(x), x) for x in drugs.split(DRUG_SEP) if x))

    print(f"  label rows {len(rows)}; trials with a resolution row {len(match_class)}; "
          f"trials with resolved agents {len(agents)}")

    # ---- endpoint met -----------------------------------------------------------------------
    out_rows, ep = [], Counter()
    ep_reasons, ep_outcome = Counter(), Counter()
    market = {w: {"verdict": Counter(), "reason": Counter(), "label": Counter(),
                  "era": defaultdict(Counter)} for w in MARKET_WINDOW_YEARS_REPORTED}
    lag_records, n_loose_lag = [], 0
    for row in rows:
        nct = row["nct_id"]
        readout = readout_date(row)
        klass = phase_class(row.get("phase"))
        e = eligible_for_endpoint_met(row)
        ep[e["verdict"]] += 1
        if e["reason"]:
            ep_reasons[e["reason"]] += 1
        label = ""
        if e["verdict"] == ELIGIBLE:
            outcome = strict_outcome(row)
            ep_outcome[outcome] += 1
            label = row["endpoint_met_strict"].strip()
        out = {"nct_id": nct, "phase_class": klass,
               "readout_date": readout.isoformat() if readout else "",
               "endpoint_met_verdict": e["verdict"], "endpoint_met_reason": e["reason"],
               "endpoint_met_label": label}
        items = agents.get(nct, ())
        trial_agents = [a for _r, a in items]
        loose = ([is_loose_regimen(a, moieties(r), readout, fda_first) for r, a in items]
                 if readout is not None else [False] * len(items))
        for w in MARKET_WINDOW_YEARS_REPORTED:
            rec = trial_market_label(eligible_for_market(row, snapshot, w),
                                     match_class.get(nct, ""), trial_agents, readout, w, loose)
            vc, rc, lc = market_columns(w)
            out[vc], out[rc] = rec["verdict"], rec["reason"]
            out[lc] = "" if rec["label"] is None else str(rec["label"])
            mk = market[w]
            mk["verdict"][rec["verdict"]] += 1
            if rec["reason"]:
                mk["reason"][(rec["verdict"], rec["reason"])] += 1
            if rec["label"] is not None:
                mk["label"][rec["label"]] += 1
                band = readout.year - readout.year % ERA_BAND_YEARS
                mk["era"][band][rec["label"]] += 1
        for w in ADVANCEMENT_WINDOW_YEARS_REPORTED:
            elig = eligible_for_advancement(row, snapshot, w)
            for rule in DISEASE_RULES:
                vc, rc, lc = advancement_columns(w, rule)
                if nct in facts:
                    rec = trial_advancement_label(elig, facts[nct], adv_index, w, rule)
                else:
                    rec = {"verdict": elig["verdict"], "reason": elig["reason"], "label": None}
                out[vc], out[rc] = rec["verdict"], rec["reason"]
                out[lc] = "" if rec["label"] is None else str(rec["label"])
                ak = adv[(w, rule)]
                ak["verdict"][rec["verdict"]] += 1
                if rec["reason"]:
                    ak["reason"][(rec["verdict"], rec["reason"])] += 1
                if rec["label"] is not None:
                    ak["label"][rec["label"]] += 1
                    band = readout.year - readout.year % ERA_BAND_YEARS
                    ak["era"][band][rec["label"]] += 1
        out_rows.append(out)
        if klass == PIVOTAL and readout is not None:
            for (r, a), lo in zip(items, loose):
                if lo:                                 # D-45: never a lag, never approved
                    n_loose_lag += 1
                    continue
                lag_records.append((identity_key(r["drugs"], r["stated_salts"],
                                                 r["stated_form_words"], r["brand"]),
                                    nct, readout, a))

    _rule("1. ENDPOINT MET (eligibility.eligible_for_endpoint_met, strict label)")
    for v in ELIGIBILITY_VERDICTS:
        print(f"  {v:16s} {ep[v]:8d}")
    for k, n in sorted(ep_reasons.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {k:52s} {n:8d}")
    print(f"  eligible labelled: {dict(sorted(ep_outcome.items()))}")

    # ---- the lag ----------------------------------------------------------------------------
    _rule("2. READOUT-TO-FIRST-APPROVAL LAG (D-10), new-at-readout identities, pivotal")
    print(f"  D-45 loose regimens left out of the lag: {n_loose_lag} pivotal agent records")
    print(f"  pivotal agent records with an actual readout {len(lag_records)}; "
          f"identities {len({r[0] for r in lag_records})}")
    for h in FOLLOW_UP_YEARS:
        cases = decisive_cases(lag_records, snapshot, h)
        s = lag_summary(cases, h, LAG_WINDOWS)
        print(f"\n  H={h}: decisive identities {s['identities']} (readout on or before "
              f"{years_after(snapshot, -h)})")
        for b in BOUNDS:
            sb = s[b]
            n = sb["approved_within_follow_up"]
            pct = "  ".join(f"p{p} {v:.2f}y" for p, v in sorted(sb["percentiles"].items()))
            win = "  ".join(f"<= {w}y {k} ({_pct(k, n)})" for w, k in sb["within_window"].items())
            p90 = sb["percentiles"].get(LAG_DECISION_PERCENTILE)
            print(f"    {b:7s} approved within {h}y: {n} ({_pct(n, s['identities'])})  "
                  f"{pct or '-'}")
            print(f"            {win}")
            print(f"            D-10: {decision_line(p90)}")
        tail = sorted((c for c in cases if c.first_strict is not None
                       and c.first_strict > years_after(c.readout, D10_LOWER_YEARS)),
                      key=lambda c: (c.readout - c.first_strict, c.nct_id))
        print(f"    tail: strict lag over {D10_LOWER_YEARS}y, {len(tail)} identities; "
              f"longest {args.show} (nct | readout | first loose | first strict | drug "
              f"[salts|words|brand]):")
        for c in tail[:args.show]:
            drugs, salts, words, brand = c.identity
            print(f"      {c.nct_id} {c.readout} {c.first_loose} {c.first_strict}  "
                  f"{names(drugs)[:40]} [{salts}|{words}|{brand}]")
        bad = [c for c in cases if (c.first_loose and c.first_loose < c.readout)
               or (c.first_strict and c.first_strict < c.readout)
               or (c.first_loose and c.first_strict and c.first_loose > c.first_strict)]
        print(f"    impossible cases (approval before readout, or loose after strict): "
              f"{len(bad)}")
        if bad:
            raise SystemExit("!! impossible lag cases: the matcher's output is inconsistent. "
                             "Nothing written.")

    # ---- market -----------------------------------------------------------------------------
    _rule("3. MARKET LABEL (" + ", ".join(TARGET_NAME.format(w=w)
                                         for w in MARKET_WINDOW_YEARS_REPORTED) + ")")
    for w in MARKET_WINDOW_YEARS_REPORTED:
        mk = market[w]
        pos, neg = mk["label"][LABEL_POSITIVE], mk["label"][LABEL_NEGATIVE]
        print(f"\n  W={w}: " + "  ".join(f"{v} {mk['verdict'][v]}" for v in ELIGIBILITY_VERDICTS)
              + f"   labelled 1: {pos}  0: {neg}  positive rate {_pct(pos, pos + neg)}")
        for (v, k), n in sorted(mk["reason"].items(), key=lambda kv: (kv[0][0], -kv[1])):
            print(f"    {v:15s} {k:52s} {n:8d}")
        print(f"    positive rate by readout band ({ERA_BAND_YEARS}-year bands):")
        for band in sorted(mk["era"]):
            c = mk["era"][band]
            n = c[LABEL_POSITIVE] + c[LABEL_NEGATIVE]
            print(f"      {band}-{band + ERA_BAND_YEARS - 1}  labelled {n:6d}  "
                  f"positive {c[LABEL_POSITIVE]:5d}  {_pct(c[LABEL_POSITIVE], n)}")

    # ---- D-40 / D-45: combinations of drugs each already approved -------------------------
    _rule(f"3b. D-40/D-45 at W={D40_WINDOW}: labelled trials holding a combination of drugs "
          "each already FDA-approved (loose regimens are now excluded, D-45)")
    _vc, _rc, lc40 = market_columns(D40_WINDOW)
    by_nct = {r["nct_id"]: r for r in out_rows}
    d40 = Counter()
    for nct, items in agents.items():
        row = by_nct.get(nct)
        if row is None or row[lc40] == "":
            continue
        readout = date.fromisoformat(row["readout_date"])
        for r, a in items:
            ms = [int(x) for x in r["drugs"].split(DRUG_SEP) if x]
            if len(ms) < 2 or before_status(a, readout) != STATUS_NOT:
                continue
            if all(x in fda_first and fda_first[x] < readout for x in ms):
                combo = "a combination product exists" if a.n_identity else \
                    "no combination product ever (a loose regimen)"
                d40[(combo, row[lc40])] += 1
    if not d40:
        print("  none: no labelled trial rests on such an agent")
    for (combo, label), n in sorted(d40.items()):
        print(f"  {combo:48s} label {label}: {n:6d} agents")

    _rule("4. ADVANCEMENT (" + ", ".join(ADV_TARGET.format(w=w, rule=r)
                                         for w in ADVANCEMENT_WINDOW_YEARS_REPORTED
                                         for r in DISEASE_RULES) + ")")
    print(f"  headline: W={DEFAULT_ADVANCEMENT_WINDOW_YEARS}, disease rule '{DISEASE_EXACT}' "
          f"(D-37, owner's B); '{DISEASE_FAMILY}' is the sensitivity (C)")
    for w in ADVANCEMENT_WINDOW_YEARS_REPORTED:
        for rule in DISEASE_RULES:
            ak = adv[(w, rule)]
            pos, neg = ak["label"][LABEL_POSITIVE], ak["label"][LABEL_NEGATIVE]
            print(f"\n  W={w} {rule}: " + "  ".join(f"{v} {ak['verdict'][v]}"
                                                     for v in ELIGIBILITY_VERDICTS)
                  + f"   labelled 1: {pos}  0: {neg}  positive rate {_pct(pos, pos + neg)}")
            for (v, k), n in sorted(ak["reason"].items(), key=lambda kv: (kv[0][0], -kv[1])):
                print(f"    {v:15s} {k:52s} {n:8d}")
            if w == DEFAULT_ADVANCEMENT_WINDOW_YEARS:
                print(f"    positive rate by readout band ({ERA_BAND_YEARS}-year bands):")
                for band in sorted(ak["era"]):
                    c = ak["era"][band]
                    n = c[LABEL_POSITIVE] + c[LABEL_NEGATIVE]
                    print(f"      {band}-{band + ERA_BAND_YEARS - 1}  labelled {n:6d}  "
                          f"positive {c[LABEL_POSITIVE]:5d}  {_pct(c[LABEL_POSITIVE], n)}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    partial = {k: p.with_name(p.name + PARTIAL) for k, p in outs.items()}
    try:
        with partial[OUT_TARGETS].open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=TARGET_COLUMNS)
            wr.writeheader()
            for r in sorted(out_rows, key=lambda r: r["nct_id"]):
                wr.writerow(r)
        manifest = {
            "script": "build_targets.py", "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "snapshot": snapshot.isoformat(), "aact_as_of": as_of,
            "drugsatfda_latest_approval": fda_currency.isoformat(),
            "approvals_settings": approvals_manifest.get("settings"),
            "market_windows": list(MARKET_WINDOW_YEARS_REPORTED),
            "targets": {"endpoint_met": "built", "market": "built (market_fda_any, D-32)",
                        "advancement": "built (D-37 same disease by name key, D-38 floor; "
                                       "phase 3 out)"},
            "inputs_sha256": {str(p): sha256_of(p) for p in (
                labels_path, fields_path, titles_path, resolution_path, approvals_path)},
            "counts": {"rows": len(out_rows),
                       "endpoint_met": {**ep, "labelled": dict(ep_outcome)},
                       "market": {w: {"verdicts": dict(market[w]["verdict"]),
                                      "labels": {str(k): v for k, v in market[w]["label"].items()}}
                                  for w in MARKET_WINDOW_YEARS_REPORTED}},
            "outputs": {OUT_TARGETS: {"bytes": partial[OUT_TARGETS].stat().st_size,
                                      "sha256": sha256_of(partial[OUT_TARGETS])}},
        }
        partial[OUT_TARGETS_MANIFEST].write_text(json.dumps(manifest, indent=2, default=str),
                                                 encoding="utf-8")
        for k, p in outs.items():
            os.replace(partial[k], p)
    except BaseException:
        for p in partial.values():
            if p.exists():
                p.unlink()
        raise
    _rule("WRITTEN")
    for p in outs.values():
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
