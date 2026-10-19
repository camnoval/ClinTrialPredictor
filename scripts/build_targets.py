#!/usr/bin/env python3
"""Labels for the targets that can be built: endpoint met, and market (with the D-10 lag).
Reads only; writes its own two files into --out-dir:

  trial_targets.csv                one row per trial in trial_labels.csv
  trial_targets.manifest.json      inputs (with hashes), settings, counts

Endpoint met: services/eligibility.eligible_for_endpoint_met and its strict label, exactly as
audit_posting_bias.py computes them (same loaders). Market: services/market_label, for every
window in MARKET_WINDOW_YEARS_REPORTED, over trial_drug_form_approvals.csv. Advancement is NOT
built: its linkage key is undecided (rev 7 section 1.3 item 1, rev 10 section 14 item 3).

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
from resolve_trial_drugs import DC_MANIFEST, OUT_TRIALS, PARTIAL, sha256_of  # noqa: E402
from trial_pos.services.drug_dictionary import parse_struct_id  # noqa: E402
from trial_pos.services.drug_resolution import DRUG_SEP  # noqa: E402
from trial_pos.services.drugsatfda import APPROVED, decode, parse_date, parse_tab  # noqa: E402
from trial_pos.services.eligibility import (  # noqa: E402
    ELIGIBILITY_VERDICTS, ELIGIBLE, MARKET_WINDOW_YEARS_REPORTED, eligible_for_endpoint_met,
    eligible_for_market, merge_gate_exclusion, merge_registration_timing, phase_class,
    readout_date, strict_outcome, years_after,
)
from trial_pos.services.form_approval import (  # noqa: E402
    STATUS_NOT, before_status, fda_approval_dates, parse_approval_row,
)
from trial_pos.services.market_label import (  # noqa: E402
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


def market_columns(w: int) -> tuple:
    return (f"market_w{w}_verdict", f"market_w{w}_reason", f"market_w{w}_label")


TARGET_COLUMNS = (("nct_id", "phase_class", "readout_date", "endpoint_met_verdict",
                   "endpoint_met_reason", "endpoint_met_label")
                  + tuple(c for w in MARKET_WINDOW_YEARS_REPORTED for c in market_columns(w)))


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

    def names(drugs: str) -> str:
        return " + ".join(sorted(name_of.get(int(x), x) for x in drugs.split(DRUG_SEP) if x))

    print(f"  label rows {len(rows)}; trials with a resolution row {len(match_class)}; "
          f"trials with resolved agents {len(agents)}")

    # ---- endpoint met -----------------------------------------------------------------------
    out_rows, ep = [], Counter()
    ep_reasons, ep_outcome = Counter(), Counter()
    market = {w: {"verdict": Counter(), "reason": Counter(), "label": Counter(),
                  "era": defaultdict(Counter)} for w in MARKET_WINDOW_YEARS_REPORTED}
    lag_records = []
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
        trial_agents = [a for _r, a in agents.get(nct, ())]
        for w in MARKET_WINDOW_YEARS_REPORTED:
            rec = trial_market_label(eligible_for_market(row, snapshot, w),
                                     match_class.get(nct, ""), trial_agents, readout, w)
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
        out_rows.append(out)
        if klass == PIVOTAL and readout is not None:
            for r, a in agents.get(nct, ()):
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

    # ---- D-40: labels resting on combinations of drugs each already approved -------------
    _rule(f"3b. D-40 at W={D40_WINDOW}: labels resting on combinations of drugs each "
          "already FDA-approved")
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

    _rule("4. ADVANCEMENT")
    print("  NOT BUILT: the linkage key is undecided (rev 7 section 1.3 item 1). No column.")

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
                        "advancement": "not built: linkage key undecided"},
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
