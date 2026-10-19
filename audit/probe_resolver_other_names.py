#!/usr/bin/env python3
"""How often does the resolver's other-name fallback pick a PARTNER drug? READ-ONLY. SCRATCH.

drug_resolution.resolve_agent tries the intervention's own name first and, when that does
not resolve, its AACT other names. The market-fixes probe showed other-names fields often
list combination partners ('veliparib' with 'lapatinib'), so an agent can be resolved as
its partner and labelled with the partner's approvals. Endpoint met does not depend on
resolution; market and advancement do.

Every agent resolved through an other name is put in the FIRST matching class:
  1. the intervention's own name partly resolves to drugs DISJOINT from the ones used
  2. its other names resolve to two or more different drug sets (visible partner listing)
  3. its own name holds an INN-shaped token (an INN_STEMS suffix) that names none of the
     drugs used ('veliparib' resolved as lapatinib)
  4. its own name is a development code (likely an alias: 'ABT-888' -> veliparib)
  5. anything else: a brand or descriptor (likely an alias: 'Keytruda' -> pembrolizumab)
Classes 1 to 3 are the risk. Counts are given overall, for pivotal trials, and for W=5
market-labelled trials, with examples to read.

Usage (PowerShell):
  python audit\\probe_resolver_other_names.py |
      Out-File -Encoding utf8 probe_resolver_other_names_20261001.txt
  (one line in PowerShell)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import read_csv  # noqa: E402
from build_targets import OUT_TARGETS, market_columns  # noqa: E402
from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, OUT_AGENTS, load_fda, read_rows,
)
from trial_pos.services.drug_dictionary import build_dictionary, parse_struct_id  # noqa: E402
from trial_pos.services.drug_names import key  # noqa: E402
from trial_pos.services.drug_resolution import (  # noqa: E402
    AGENT_RESOLVED, DRUG_SEP, NAME_OTHER, resolve_one_name,
)

csv.field_size_limit(10 ** 9)

DEFAULT_SHOW = 15
WINDOW = 5                                   # D-36 headline
PIVOTAL = "pivotal"
# INN suffixes for this probe's class 3 only. Suffix match on a token of at least
# MIN_INN_TOKEN letters, so short words never count.
INN_STEMS = ("mab", "nib", "parib", "ciclib", "lisib", "rafenib", "degib", "pril", "sartan",
             "olol", "dipine", "gliptin", "gliflozin", "glutide", "previr", "asvir", "buvir",
             "navir", "tegravir", "conazole", "floxacin", "cycline", "mycin", "platin",
             "taxel", "rubicin", "dronate", "lukast", "setron", "prazole", "tidine", "oxetine",
             "azepam", "caftor", "sentan", "xaban", "gatran", "parin", "fungin", "penem",
             "cillin", "vastatin", "lutamide", "zomib", "lidomide", "tinib", "afil")
MIN_INN_TOKEN = 7
_CODE = re.compile(r"\b[a-z]{1,5}[\s\-]?\d{2,}\b")
_TOKEN = re.compile(r"[a-z]+")
C_PRIMARY_DISJOINT = "1. own name partly resolves to OTHER drugs"
C_OTHERS_DISAGREE = "2. other names resolve to several drug sets"
C_INN_MISMATCH = "3. own name is an INN-shaped word naming none of the drugs used"
C_CODE = "4. own name is a code (likely alias)"
C_WORD = "5. own name is a brand or descriptor (likely alias)"
CLASSES = (C_PRIMARY_DISJOINT, C_OTHERS_DISAGREE, C_INN_MISMATCH, C_CODE, C_WORD)
RISK = CLASSES[:3]


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def inn_tokens(k: str) -> list:
    return [t for t in _TOKEN.findall(k)
            if len(t) >= MIN_INN_TOKEN and any(t.endswith(s) for s in INN_STEMS)]


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    print(f"INN stems {len(INN_STEMS)} (min token {MIN_INN_TOKEN}) | window {WINDOW}")

    _rule("INPUTS")
    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    dc = {t: list(read_rows(args.dc_dir / m["tables"][t]["file"], n)) for t, n in DC_NEED.items()}
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"],
                         load_fda(args.fda_dir, FDA_PRODUCTS),
                         load_fda(args.fda_dir, FDA_APPLICATIONS))
    name_of = {parse_struct_id(r["id"]): key(r["name"]) for r in dc["structures"]}
    targets = {r["nct_id"]: r for r in read_csv(args.labels_dir / OUT_TARGETS)}
    _vc, _rc, lc = market_columns(WINDOW)
    iname = {(r["nct_id"], r["id"]): r.get("name", "")
             for r in read_csv(args.aact_dir / "rows__interventions.csv")}
    others = defaultdict(list)
    for r in read_csv(args.aact_dir / "rows__intervention_other_names.csv"):
        others[(r["nct_id"], r["intervention_id"])].append(r["name"])
    agents = [r for r in read_csv(args.labels_dir / OUT_AGENTS)
              if r["agent_status"] == AGENT_RESOLVED]
    via_other = [r for r in agents if r["name_kind"] == NAME_OTHER]
    print(f"  resolved agents {len(agents)}; through an other name {len(via_other)} "
          f"({_pct(len(via_other), len(agents))})")

    _rule("1. CLASSES")
    counts = {g: Counter() for g in ("all", "pivotal", f"W={WINDOW} labelled")}
    examples = defaultdict(Counter)
    risky_labelled = set()
    cache = {}

    def resolve(n: str):
        if n not in cache:
            cache[n] = resolve_one_name(d, n)
        return cache[n]

    for r in via_other:
        k = (r["nct_id"], r["intervention_id"])
        own = iname.get(k, "")
        used = frozenset(int(x) for x in r["drugs"].split(DRUG_SEP) if x)
        used_names = {name_of.get(x, "") for x in used}
        p = resolve_one_name(d, own, whole_intervention_name=True)
        sets = {resolve(o).drugs for o in others.get(k, ())
                if resolve(o).status == AGENT_RESOLVED}
        mismatched = [t for t in inn_tokens(key(own))
                      if not any(t in nm for nm in used_names)]
        if p.drugs and not (p.drugs & used):
            c = C_PRIMARY_DISJOINT
        elif len(sets) > 1:
            c = C_OTHERS_DISAGREE
        elif mismatched:
            c = C_INN_MISMATCH
        elif _CODE.search(key(own)):
            c = C_CODE
        else:
            c = C_WORD
        t = targets.get(r["nct_id"], {})
        counts["all"][c] += 1
        if t.get("phase_class") == PIVOTAL:
            counts["pivotal"][c] += 1
        if t.get(lc, "") != "":
            counts[f"W={WINDOW} labelled"][c] += 1
            if c in RISK:
                risky_labelled.add(r["nct_id"])
        used_label = " + ".join(sorted(name_of.get(x, str(x)) for x in used))
        examples[c][(key(own)[:36], key(r["name_used"])[:30], used_label[:30])] += 1
    for g, c in counts.items():
        n = sum(c.values())
        print(f"\n  {g}: {n}")
        for k_ in CLASSES:
            print(f"    {k_:66s} {c[k_]:7d}  {_pct(c[k_], n)}")
    labelled = sum(1 for t in targets.values() if t.get(lc, "") != "")
    print(f"\n  W={WINDOW} labelled trials holding an agent in classes 1-3: {len(risky_labelled)} "
          f"of {labelled} ({_pct(len(risky_labelled), labelled)})")

    _rule(f"2. EXAMPLES, top {args.show} per class (own name | other name used | drugs)")
    for c in CLASSES:
        print(f"\n  {c}:")
        for (own, used_name, dr), n in sorted(examples[c].items(),
                                             key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {n:4d}  {own:36s} | {used_name:30s} | {dr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())