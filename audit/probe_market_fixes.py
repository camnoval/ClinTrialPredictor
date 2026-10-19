#!/usr/bin/env python3
"""Size each market fix before building it (owner, 2026-10-13: fix everything). READ-ONLY.

  1. MICRO SIGN. drug_names.key() applies NFKC, which turns the micro sign (U+00B5) into the
     Greek mu (U+03BC). DOSE_UNITS lists only the micro sign, so 'µg' is never stripped as
     a dose. Counts the unresolved agents with a mu in their name that resolve once 'µg'
     reads as a unit (simulated by spelling it 'mcg'). An assistant's bug, earlier session.
  2. REGISTRY-LEARNED CODES. A code name often sits beside a resolvable name in the same
     intervention ('LCZ696' with other name 'sacubitril/valsartan'). Learns key -> drugs
     from every RESOLVED tested agent's names, and keeps a key only when all its evidence
     names one drug set. Counts what that would resolve among the unresolved, with
     examples and conflicts.
  3. CBER-TYPE PRODUCTS among the unresolved market trials (vaccines, blood, cell and gene
     therapy): AACT intervention type and name words. Sizes the Purple Book's role.
  4. TAIL GUARDS on pivotal agents read as NOT approved before readout:
     a. DrugCentral has an FDA approval of a moiety dated BEFORE the readout (withdrawn and
        reapproved drugs: gemtuzumab ozogamicin, daclizumab);
     b. multi-moiety agents whose every moiety DrugCentral dates as FDA-approved before
        the readout (old + old: amoxicillin + omeprazole);
     c. a moiety with an UNDATED DrugCentral FDA row (pre-1938 candidates: phenobarbital).
     Each with how many W=5 market labels it would touch.

Usage (PowerShell):
  python audit\\probe_market_fixes.py |
      Out-File -Encoding utf8 probe_market_fixes_20261001.txt
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
from match_form_approvals import OUT_APPROVALS  # noqa: E402
from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, OUT_AGENTS, load_fda, read_rows,
)
from trial_pos.services.drug_dictionary import build_dictionary, parse_struct_id  # noqa: E402
from trial_pos.services.drug_names import key, variants  # noqa: E402
from trial_pos.services.drug_resolution import (  # noqa: E402
    AGENT_RESOLVED, DRUG_SEP, resolve_one_name,
)
from trial_pos.services.drugsatfda import parse_date  # noqa: E402
from trial_pos.services.form_approval import (  # noqa: E402
    STATUS_NOT, before_status, parse_approval_row,
)
from trial_pos.services.market_label import REASON_PARTIAL_ONLY, REASON_UNRESOLVED  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_SHOW = 25
WINDOW = 5                                   # D-36 headline
PIVOTAL = "pivotal"
FDA_AGENCY = "FDA"
MU = "\u03bc"                               # what NFKC makes of the micro sign
MICRO_AS_UNIT = "mcg"
LEARNED_MIN_EVIDENCE = (1, 2)                # reported both ways; neither is a verdict
BIOLOGICAL_TYPES = ("BIOLOGICAL", "GENETIC")
CBER_WORDS = ("vaccine", "toxoid", "plasma", "immune globulin", "immunoglobulin",
              "antitoxin", "antivenom", "antivenin", "allergen", "car-t", "car t",
              "cell therapy", "cells", "gene therapy", "blood", "platelet")
_CBER = re.compile(r"\b(" + "|".join(re.escape(w) for w in CBER_WORDS) + r")\b")


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def name_keys(name: str) -> set:
    return {(v.index, v.key) for v in variants(name)}


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    print(f"window {WINDOW} | learned evidence thresholds {LEARNED_MIN_EVIDENCE} | "
          f"CBER words {CBER_WORDS}")

    _rule("INPUTS")
    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    dc = {t: list(read_rows(args.dc_dir / m["tables"][t]["file"], n)) for t, n in DC_NEED.items()}
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"],
                         load_fda(args.fda_dir, FDA_PRODUCTS),
                         load_fda(args.fda_dir, FDA_APPLICATIONS))
    name_of = {parse_struct_id(r["id"]): r["name"] for r in dc["structures"]}
    fda_dates, fda_undated = defaultdict(list), set()
    for r in read_csv(args.dc_dir / m["tables"]["approval"]["file"]):
        if (r.get("type") or "").strip() != FDA_AGENCY:
            continue
        sid, when = parse_struct_id(r["struct_id"]), parse_date(r.get("approval"))
        if sid is None:
            continue
        if when is None:
            fda_undated.add(sid)
        else:
            fda_dates[sid].append(when)
    fda_first = {s: min(v) for s, v in fda_dates.items()}

    def names(ids) -> str:
        return " + ".join(sorted(name_of.get(int(x), str(x)) for x in ids))

    targets = {r["nct_id"]: r for r in read_csv(args.labels_dir / OUT_TARGETS)}
    _vc, rc, lc = market_columns(WINDOW)
    unresolved_market = {n for n, t in targets.items()
                         if t[rc] in (REASON_UNRESOLVED, REASON_PARTIAL_ONLY)}
    labelled = {n for n, t in targets.items() if t[lc] != ""}
    itype, iname = {}, {}
    for r in read_csv(args.aact_dir / "rows__interventions.csv"):
        itype[(r["nct_id"], r["id"])] = r.get("intervention_type", "")
        iname[(r["nct_id"], r["id"])] = r.get("name", "")
    others = defaultdict(list)
    for r in read_csv(args.aact_dir / "rows__intervention_other_names.csv"):
        others[(r["nct_id"], r["intervention_id"])].append(r["name"])
    agents = list(read_csv(args.labels_dir / OUT_AGENTS))
    print(f"  agents {len(agents)}; W={WINDOW} market trials unresolved or partly resolved "
          f"{len(unresolved_market)}; labelled {len(labelled)}; DrugCentral FDA dated "
          f"{len(fda_first)}, undated {len(fda_undated)}")

    # ---- 1. micro sign ----------------------------------------------------------------------
    _rule("1. MICRO SIGN: unresolved agents with a mu that resolve once it is a unit")
    tried = fixed = fixed_market = 0
    ex = Counter()
    for r in agents:
        if r["agent_status"] == AGENT_RESOLVED or MU not in key(r["name_used"]):
            continue
        tried += 1
        res = resolve_one_name(d, key(r["name_used"]).replace(MU + "g", MICRO_AS_UNIT))
        if res.status == AGENT_RESOLVED:
            fixed += 1
            fixed_market += r["nct_id"] in unresolved_market
            ex[(key(r["name_used"])[:50], names(res.drugs)[:40])] += 1
    print(f"  not-resolved agents with a mu {tried}; would resolve {fixed}; of them in W={WINDOW} "
          f"unresolved market trials {fixed_market}")
    for (nm, dr), n in sorted(ex.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {n:4d}  {nm:50s} -> {dr}")

    # ---- 2. registry-learned codes -----------------------------------------------------------
    _rule("2. REGISTRY-LEARNED CODES: key -> drugs from resolved agents' own names")
    evidence = defaultdict(Counter)
    for r in agents:
        if r["agent_status"] != AGENT_RESOLVED:
            continue
        drugs = frozenset(int(x) for x in r["drugs"].split(DRUG_SEP) if x)
        k = (r["nct_id"], r["intervention_id"])
        for n in [iname.get(k, ""), r["name_used"]] + others.get(k, []):
            for nk in name_keys(n):
                evidence[nk][drugs] += 1
    conflicts = {nk: c for nk, c in evidence.items() if len(c) > 1}
    print(f"  learned keys {len(evidence)}; with conflicting drug sets {len(conflicts)}")
    for t in LEARNED_MIN_EVIDENCE:
        learned = {nk: next(iter(c)) for nk, c in evidence.items()
                   if len(c) == 1 and sum(c.values()) >= t}
        got = got_market = got_trials = 0
        trial_all = defaultdict(list)
        examples = Counter()
        for r in agents:
            if r["agent_status"] == AGENT_RESOLVED:
                continue
            k = (r["nct_id"], r["intervention_id"])
            hits = set()
            for n in [iname.get(k, ""), r["name_used"]] + others.get(k, []):
                hits |= {learned[nk] for nk in name_keys(n) if nk in learned}
            ok = len(hits) == 1
            trial_all[r["nct_id"]].append(ok)
            if ok:
                got += 1
                got_market += r["nct_id"] in unresolved_market
                examples[(key(r["name_used"])[:44], names(next(iter(hits)))[:40])] += 1
        got_trials = sum(1 for n, v in trial_all.items() if n in unresolved_market and all(v))
        print(f"\n  evidence >= {t}: keys {len(learned)}; not-resolved agents it would "
              f"resolve {got}; in W={WINDOW} unresolved market trials {got_market}; such "
              f"trials whose every not-resolved agent resolves {got_trials} of "
              f"{len(unresolved_market)}")
        for (nm, dr), n in sorted(examples.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {n:4d}  {nm:44s} -> {dr}")
    print(f"\n  conflicting keys, top {args.show} by evidence (key | drug sets):")
    ranked = sorted(conflicts.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    for nk, c in ranked[:args.show]:
        sets = "; ".join(f"{names(s)[:30]} x{v}" for s, v in c.most_common(3))
        print(f"    {nk[1][:30]:30s} {sets}")

    # ---- 3. CBER-type ------------------------------------------------------------------------
    _rule(f"3. CBER-TYPE PRODUCTS among W={WINDOW} unresolved market trials")
    by_type, by_word, trial_cber = Counter(), Counter(), set()
    for r in agents:
        if r["agent_status"] == AGENT_RESOLVED or r["nct_id"] not in unresolved_market:
            continue
        k = (r["nct_id"], r["intervention_id"])
        t = itype.get(k, "")
        by_type[t] += 1
        hit = _CBER.search(key(r["name_used"]))
        if hit:
            by_word[hit.group(1)] += 1
        if t in BIOLOGICAL_TYPES or hit:
            trial_cber.add(r["nct_id"])
    print(f"  not-resolved agents by AACT intervention type: {dict(by_type.most_common())}")
    print(f"  by CBER word: {dict(by_word.most_common())}")
    print(f"  trials with a biological-typed or CBER-worded unresolved agent: {len(trial_cber)} "
          f"of {len(unresolved_market)} ({_pct(len(trial_cber), len(unresolved_market))}). "
          "BIOLOGICAL also covers CDER antibodies: an upper bound.")

    # ---- 4. tail guards ----------------------------------------------------------------------
    _rule("4. TAIL GUARDS on pivotal agents read as NOT approved before readout")
    guard = {"a": Counter(), "b": Counter(), "c": Counter()}
    trials_hit = {"a": set(), "b": set(), "c": set()}
    n_new = 0
    for r in read_csv(args.labels_dir / OUT_APPROVALS):
        t = targets.get(r["nct_id"])
        if t is None or t["phase_class"] != PIVOTAL or not t["readout_date"]:
            continue
        readout = date.fromisoformat(t["readout_date"])
        if before_status(parse_approval_row(r), readout) != STATUS_NOT:
            continue
        n_new += 1
        ms = [int(x) for x in r["drugs"].split(DRUG_SEP) if x]
        stated = bool(r["stated_salts"] or r["stated_form_words"] or r["brand"])
        early = [x for x in ms if x in fda_first and fda_first[x] < readout]
        label = names(ms)[:40]
        if early and not stated and len(ms) == 1:
            guard["a"][(label, str(fda_first[early[0]]))] += 1
            trials_hit["a"].add(r["nct_id"])
        if len(ms) > 1 and len(early) == len(ms):
            guard["b"][(label, "")] += 1
            trials_hit["b"].add(r["nct_id"])
        if any(x in fda_undated for x in ms):
            guard["c"][(label, "")] += 1
            trials_hit["c"].add(r["nct_id"])
    print(f"  pivotal agents not approved before readout: {n_new}")
    titles = {"a": "a. single moiety, no stated form, DrugCentral FDA date before readout",
              "b": "b. every moiety DrugCentral-FDA-dated before readout (old + old)",
              "c": "c. a moiety with an undated DrugCentral FDA row"}
    for g in ("a", "b", "c"):
        touched = len(trials_hit[g] & labelled)
        print(f"\n  {titles[g]}: agents {sum(guard[g].values())}; W={WINDOW} labelled trials "
              f"touched {touched}")
        for (nm, when), n in sorted(guard[g].items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {n:4d}  {nm:40s} {when}")
    return 0


if __name__ == "__main__":
    sys.exit(main())