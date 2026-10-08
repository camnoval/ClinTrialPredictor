#!/usr/bin/env python3
"""Where does resolution leak, and which misses matter? READ-ONLY. SCRATCH.

Reads the resolver's own outputs (data/labels/trial_drug_*.csv) and rebuilds the same
dictionary with the same services, then asks:

  1. REVERSE SALT: would the unresolved agents resolve if the DICTIONARY's keys were also
     salt-stripped (DrugCentral says 'fludarabine phosphate', the trial says
     'fludarabine')? Also with dose and form stripped from the dictionary keys. Counted
     as agents and trials, unique vs ambiguous, and how many recovered drugs carry an
     FDA approval in DrugCentral's approval table.
  2. MESH-NAMED MISSES: unmatched trials whose indexed MeSH resolves to a drug. The most
     frequent (agent name -> MeSH drug) pairs, and how many of those drugs are
     FDA-approved. This is where recall leaks on approved drugs.
  3. NOT CORROBORATED: matched trials whose indexed MeSH shares no drug with the
     resolver's. The most frequent resolver drugs among them: the first place to look
     for precision errors.

Nothing here is a rule. Section 1 measures a candidate rewrite before it is built.

Usage (PowerShell):
  python audit\\probe_unresolved.py | Out-File -Encoding utf8 probe_unresolved_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, LABELS_FILE, load_fda, read_rows,
)
from trial_pos.services.aact_fields import MESH_LIST  # noqa: E402
from trial_pos.services.aact_rows import file_name  # noqa: E402
from trial_pos.services.drug_dictionary import (  # noqa: E402
    SOURCES, build_dictionary, parse_struct_id,
)
from trial_pos.services.drug_names import (  # noqa: E402
    INDEX_EXACT, drop_dose_and_form, drop_salt, key, variants,
)
from trial_pos.services.drug_resolution import AGENT_UNRESOLVED, DRUG_SEP  # noqa: E402
from trial_pos.services.eligibility import phase_class  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_AACT = Path("data") / "aact"
DEFAULT_DC = Path("data") / "drugcentral"
DEFAULT_FDA = Path("data") / "drugsatfda"
DEFAULT_LABELS_DIR = Path("data") / "labels"
DEFAULT_SHOW = 30
FDA_AGENCY = "FDA"
PIVOTAL = "pivotal"
REWRITE_SALT = "dictionary_salt_stripped"
REWRITE_SALT_FORM = "dictionary_salt_and_form_stripped"
REWRITES = (REWRITE_SALT, REWRITE_SALT_FORM)


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def drugs_of(text: str) -> frozenset:
    return frozenset(int(x) for x in (text or "").split(DRUG_SEP) if x)


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=DEFAULT_AACT)
    ap.add_argument("--dc-dir", type=Path, default=DEFAULT_DC)
    ap.add_argument("--fda-dir", type=Path, default=DEFAULT_FDA)
    ap.add_argument("--labels-dir", type=Path, default=DEFAULT_LABELS_DIR)
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    print(f"aact {args.aact_dir} | drugcentral {args.dc_dir} | drugs@fda {args.fda_dir} | "
          f"resolver outputs {args.labels_dir} | show {args.show} | FDA agency token "
          f"{FDA_AGENCY!r}")

    manifest = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    dc = {t: list(read_rows(args.dc_dir / manifest["tables"][t]["file"], need))
          for t, need in DC_NEED.items()}
    approval = list(read_rows(args.dc_dir / manifest["tables"]["approval"]["file"],
                              ("struct_id", "type")))
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"],
                         load_fda(args.fda_dir, FDA_PRODUCTS),
                         load_fda(args.fda_dir, FDA_APPLICATIONS))
    name_of = {d.canonical(parse_struct_id(r["id"])): r["name"] for r in dc["structures"]}
    agencies = Counter((r.get("type") or "").strip() for r in approval)
    fda = {d.canonical(parse_struct_id(r["struct_id"])) for r in approval
           if (r.get("type") or "").strip() == FDA_AGENCY and parse_struct_id(r["struct_id"])}
    print(f"approval.type vocabulary: {dict(sorted(agencies.items()))}")
    print(f"drugs with an FDA approval row (parent-mapped): {len(fda)}")

    def label(drugs) -> str:
        return " + ".join(sorted(name_of.get(x, str(x)) for x in drugs))

    phase = {r["nct_id"]: phase_class(r["phase"])
             for r in read_rows(args.aact_dir / LABELS_FILE, ("nct_id", "is_drug_trial",
                                                               "phase"))
             if tribool(r["is_drug_trial"]) is True}
    agents = list(read_rows(args.labels_dir / "trial_drug_agents.csv",
                            ("nct_id", "agent_status", "name_used", "drugs")))
    trials = {r["nct_id"]: r for r in read_rows(args.labels_dir / "trial_drug_resolution.csv",
                                                ("nct_id", "matched", "drugs"))}

    _rule("1. REVERSE SALT: unresolved agents against salt-stripped DICTIONARY keys")
    reverse = {rw: defaultdict(set) for rw in REWRITES}
    for src in SOURCES:
        for k, sets in d.index[src][INDEX_EXACT].items():
            for rw, fn in ((REWRITE_SALT, drop_salt),
                           (REWRITE_SALT_FORM, lambda s: drop_salt(drop_dose_and_form(s)))):
                stripped = fn(k)
                if stripped and stripped != k:
                    reverse[rw][stripped] |= sets
    unresolved = [a for a in agents if a["agent_status"] == AGENT_UNRESOLVED]
    print(f"  unresolved agents {len(unresolved)} in {len({a['nct_id'] for a in unresolved})} "
          f"trials")
    for rw in REWRITES:
        unique, ambiguous, trials_hit, approved, names = 0, 0, set(), 0, Counter()
        for a in unresolved:
            for v in variants(a["name_used"]):
                if v.index != INDEX_EXACT:
                    continue
                found = reverse[rw].get(v.key)
                if not found:
                    continue
                if len(found) == 1:
                    unique += 1
                    trials_hit.add(a["nct_id"])
                    drugs = next(iter(found))
                    approved += bool(drugs & fda)
                    names[(key(a["name_used"]), label(drugs))] += 1
                else:
                    ambiguous += 1
                break
        pivotal = sum(1 for t in trials_hit if phase.get(t) == PIVOTAL
                      and trials[t]["matched"] == "False")
        print(f"\n  {rw}: agents resolved uniquely {unique}, ambiguously {ambiguous}; "
              f"recovered drug FDA-approved {approved} ({_pct(approved, unique)})")
        print(f"    trials touched {len(trials_hit)}; currently-unmatched pivotal trials "
              f"that would match {pivotal}")
        for (n, drug), c in sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {c:6d}  {n}  ->  {drug}")

    _rule("2. UNMATCHED TRIALS WHOSE INDEXED MESH NAMES A DRUG")
    by_trial_names = defaultdict(list)
    for a in agents:
        by_trial_names[a["nct_id"]].append(key(a["name_used"]))
    pairs, approved_trials, pivotal_approved = Counter(), 0, 0
    leaking = [t for t, r in trials.items() if r["matched"] == "False"
               and r.get("mesh_drugs_when_unmatched")]
    for t in leaking:
        drugs = drugs_of(trials[t]["mesh_drugs_when_unmatched"])
        is_approved = bool(drugs & fda)
        approved_trials += is_approved
        pivotal_approved += is_approved and phase.get(t) == PIVOTAL
        for n in by_trial_names.get(t, ["(no agent row)"]):
            pairs[(n, label(drugs), is_approved)] += 1
    print(f"  trials {len(leaking)}; MeSH drug FDA-approved in {approved_trials} "
          f"({_pct(approved_trials, len(leaking))}); of those pivotal {pivotal_approved}")
    print(f"\n  top (agent name -> MeSH drug) pairs, FDA-approved MeSH drugs only:")
    shown = [(k, c) for k, c in pairs.items() if k[2]]
    for (n, drug, _a), c in sorted(shown, key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {c:6d}  {n}  ->  {drug}")

    _rule("3. MATCHED BUT NOT CORROBORATED BY MESH")
    disagree = Counter()
    for t, r in trials.items():
        if r.get("mesh_corroborated") == "False":
            disagree[label(drugs_of(r["drugs"]))] += 1
    print(f"  trials {sum(disagree.values())}; most frequent resolver drugs among them:")
    for drug, c in sorted(disagree.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {c:6d}  {drug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())