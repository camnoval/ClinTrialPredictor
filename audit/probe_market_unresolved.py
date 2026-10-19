#!/usr/bin/env python3
"""What is in the market trials whose tested agent did not resolve? READ-ONLY. SCRATCH.

Decides whether those trials can be labelled 0 (not FDA-approved), or must stay out. The
risk in labelling them 0 is an unresolved name that IS an FDA-approved drug: a false 0
(approved within W) or a trial the floor should have removed (approved before readout).
This probe measures a floor on that risk without DrugCentral. Every unresolved tested-agent
name is checked, through drug_names.variants, against Drugs@FDA's OWN NDA/BLA product names
(brand and ingredient, whole and per part). A name that does not textually match is not
caught, so the hit counts are a FLOOR on the error, never its size.

Sections:
  1. per readout band: labelled vs unresolved vs partly resolved market-eligible trials
     (is the unresolved share higher for recent readouts, as a "no time yet" story predicts?)
  2. DrugCentral's currency: unresolved trials whose window closes after DrugCentral's last
     FDA approval, where an approved drug cannot resolve however it is spelled
  3. unresolved agent names against Drugs@FDA, dated against the readout: before readout /
     within W / after W / no hit; trial-level, i.e. what labelling them 0 would get wrong
  4. the most frequent unresolved names, code-like or word-like, with any Drugs@FDA hit

Usage (PowerShell):
  python audit\\probe_market_unresolved.py |
      Out-File -Encoding utf8 probe_market_unresolved_20261001.txt
  (one line in PowerShell)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import read_csv  # noqa: E402
from build_targets import OUT_TARGETS, market_columns  # noqa: E402
from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, FDA_APPLICATIONS, FDA_PRODUCTS, OUT_AGENTS, load_fda,
)
from trial_pos.services.drug_names import (  # noqa: E402
    drop_parentheticals, key, variants,
)
from trial_pos.services.drug_resolution import AGENT_RESOLVED  # noqa: E402
from trial_pos.services.drugsatfda import (  # noqa: E402
    APPL_BLA, APPL_NDA, decode, norm_appl, parse_date, parse_tab,
)
from trial_pos.services.eligibility import years_after  # noqa: E402
from trial_pos.services.form_approval import (  # noqa: E402
    ingredient_parts, original_dates, tentative_only,
)
from trial_pos.services.market_label import (  # noqa: E402
    REASON_PARTIAL_ONLY, REASON_UNRESOLVED,
)

csv.field_size_limit(10 ** 9)

WINDOWS = (3, 5)
DEFAULT_SHOW = 30
BAND_YEARS = 3
SUBMISSIONS = "Submissions.txt"
FDA_AGENCY = "FDA"
INNOVATOR = (APPL_NDA, APPL_BLA)
# A development code: a short letter prefix at a word start, then a number of 2+ digits,
# joined or split by a space or hyphen ('bms-986016', 'mk3475', 'xyz 123', 'pf-07321332').
# A dose after a full word ('sintilimab 200') is not one: the prefix is capped in length.
CODE_PREFIX_MAX = 5
_CODE = re.compile(r"\b[a-z]{1,%d}[\s\-]?\d{2,}\b" % CODE_PREFIX_MAX)


def code_like(name: str) -> bool:
    return bool(_CODE.search(name))
HIT_BEFORE, HIT_WITHIN, HIT_AFTER, HIT_UNDATED, NO_HIT = (
    "Drugs@FDA approval BEFORE readout (floor should have removed it)",
    "Drugs@FDA approval WITHIN W (a false 0 if labelled 0)",
    "Drugs@FDA approval after W (0 is right)",
    "Drugs@FDA product, undated",
    "no Drugs@FDA name hit")
HIT_ORDER = (HIT_WITHIN, HIT_BEFORE, HIT_UNDATED, HIT_AFTER, NO_HIT)   # worst first


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()

    _rule("INPUTS")
    targets = list(read_csv(args.labels_dir / OUT_TARGETS))
    agents = defaultdict(list)
    for r in read_csv(args.labels_dir / OUT_AGENTS):
        if r["agent_status"] != AGENT_RESOLVED:
            agents[r["nct_id"]].append(r["name_used"])
    products = load_fda(args.fda_dir, FDA_PRODUCTS)
    applications = load_fda(args.fda_dir, FDA_APPLICATIONS)
    text, _enc = decode((args.fda_dir / SUBMISSIONS).read_bytes())
    submissions = parse_tab(text)["rows"]
    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    dc_rows = read_csv(args.dc_dir / m["tables"]["approval"]["file"])
    dc_dates = [parse_date(r["approval"]) for r in dc_rows
                if (r.get("type") or "").strip() == FDA_AGENCY]
    dc_latest = max(d for d in dc_dates if d)
    print(f"  targets rows {len(targets)}; DrugCentral latest FDA approval {dc_latest}")

    # Drugs@FDA name index: exact and compact keys -> innovator applications
    appl_type = {norm_appl(r["ApplNo"]): r["ApplType"] for r in applications}
    originals, tentative = original_dates(submissions), tentative_only(submissions)
    index = defaultdict(set)
    for r in products:
        a = norm_appl(r["ApplNo"])
        if appl_type.get(a) not in INNOVATOR or a in tentative:
            continue
        names = {key(r["DrugName"]), drop_parentheticals(key(r["DrugName"])),
                 key(r["ActiveIngredient"])}
        names |= {key(p) for p in ingredient_parts(r["ActiveIngredient"])}
        for n in names:
            if n:
                for v in variants(n):
                    index[(v.index, v.key)].add(a)
    print(f"  Drugs@FDA innovator name keys indexed {len(index)}")

    def hit_date(name: str):
        apps = set()
        for v in variants(name):
            apps |= index.get((v.index, v.key), set())
        if not apps:
            return None, False
        dated = [originals[a] for a in apps if a in originals]
        return (min(dated) if dated else None), True

    for w in WINDOWS:
        vc, rc, _lc = market_columns(w)
        _rule(f"W={w}")
        bands = defaultdict(Counter)
        trial_hits, after_dc, name_counts, name_hit = Counter(), 0, Counter(), {}
        for t in targets:
            reason, verdict = t[rc], t[vc]
            if not t["readout_date"]:
                continue
            readout = date.fromisoformat(t["readout_date"])
            band = readout.year - readout.year % BAND_YEARS
            if verdict == "eligible":
                bands[band]["labelled"] += 1
            elif reason in (REASON_UNRESOLVED, REASON_PARTIAL_ONLY):
                bands[band][reason] += 1
            else:
                continue
            if reason not in (REASON_UNRESOLVED, REASON_PARTIAL_ONLY):
                continue
            end = years_after(readout, w)
            after_dc += end > dc_latest
            outcomes = []
            for name in agents.get(t["nct_id"], ()):
                d, hit = hit_date(name)
                name_counts[key(name)] += 1
                if not hit:
                    outcomes.append(NO_HIT)
                    name_hit.setdefault(key(name), "")
                    continue
                o = (HIT_UNDATED if d is None else HIT_BEFORE if d < readout
                     else HIT_WITHIN if d <= end else HIT_AFTER)
                outcomes.append(o)
                name_hit[key(name)] = f"{o.split(' (')[0]} {d}"
            worst = min(outcomes, key=HIT_ORDER.index) if outcomes else NO_HIT
            trial_hits[(reason, worst)] += 1

        print("  1. market-eligible trials by readout band (labelled | unresolved | partly "
              "resolved | unresolved share of the three)")
        for band in sorted(bands):
            c = bands[band]
            n = c["labelled"] + c[REASON_UNRESOLVED] + c[REASON_PARTIAL_ONLY]
            print(f"    {band}-{band + BAND_YEARS - 1}  {c['labelled']:6d}  "
                  f"{c[REASON_UNRESOLVED]:6d}  {c[REASON_PARTIAL_ONLY]:5d}  "
                  f"{_pct(c[REASON_UNRESOLVED] + c[REASON_PARTIAL_ONLY], n)}")
        n_unres = sum(trial_hits.values())
        print(f"\n  2. unresolved or partly resolved trials {n_unres}; window closes after "
              f"DrugCentral's last FDA approval ({dc_latest}): {after_dc}")
        print("\n  3. worst Drugs@FDA outcome over each trial's unresolved agents (a FLOOR):")
        for reason in (REASON_UNRESOLVED, REASON_PARTIAL_ONLY):
            n = sum(v for (r, _o), v in trial_hits.items() if r == reason)
            print(f"    {reason}: {n}")
            for o in HIT_ORDER:
                k = trial_hits[(reason, o)]
                print(f"      {o:62s} {k:6d}  {_pct(k, n)}")
        print(f"\n  4. most frequent unresolved names, top {args.show} (agents | shape | "
              "Drugs@FDA hit)")
        for nm, n in sorted(name_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            shape = "code" if code_like(nm) else "word"
            print(f"    {n:5d}  {nm[:50]:50s} {shape:4s}  {name_hit.get(nm, '')}")
        codes = sum(n for nm, n in name_counts.items() if code_like(nm))
        print(f"    code-like share of unresolved agent names (letter prefix of up to "
              f"{CODE_PREFIX_MAX}, then 2+ digits; also catches e.g. 'covid 19'): "
              f"{_pct(codes, sum(name_counts.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())