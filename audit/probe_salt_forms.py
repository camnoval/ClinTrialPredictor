#!/usr/bin/env python3
"""Are salts and formulations separate drugs in our sources, and how often do trials
compare one form of a drug against another? READ-ONLY. SCRATCH.

  1. DRUGCENTRAL: synonyms that carry a salt word, by what they point at: a structure
     that is a salt CHILD in struct2parent, a PARENT with salt children, or a structure
     with no parent relation at all (the salt is just a synonym of the moiety, so no
     DrugCentral id can tell the salts apart)
  2. NAMED EXAMPLES (--examples): for each moiety word, every DrugCentral structure and
     synonym, Orange Book ingredient and Drugs@FDA ingredient string containing it, with
     the drug ids, and per Drugs@FDA ingredient string its NDA/BLA applications and the
     earliest approved original. Different salts dated differently means product-level
     dating is possible from Drugs@FDA even where DrugCentral merges them
  3. RESOLVER OUTPUT: matched agents whose trial name STATES a salt or formulation word,
     by the step that matched them. A stating name matched by a stripping step is a
     match at a less specific level than the trial gave
  4. FORM-VS-FORM TRIALS: drug trials with two or more drug interventions that are the
     same moiety once salt and formulation words are removed, but differ before. By phase
     class, with examples. These are the trials a moiety-level identity mislabels.

Usage (PowerShell):
  python audit\\probe_salt_forms.py | Out-File -Encoding utf8 probe_salt_forms_20261001.txt
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

from resolve_trial_drugs import DC_MANIFEST, LABELS_FILE, read_rows  # noqa: E402
from trial_pos.services.aact_rows import file_name  # noqa: E402
from trial_pos.services.drug_dictionary import parse_struct_id  # noqa: E402
from trial_pos.services.drug_names import (  # noqa: E402
    FORM_WORDS, SALT_WORDS, drop_dose_and_form, drop_salt, key,
)
from trial_pos.services.drug_resolution import MATCHING_STATUSES  # noqa: E402
from trial_pos.services.drugsatfda import (  # noqa: E402
    APPL_BLA, APPL_NDA, APPROVED, ORIGINAL, decode, norm_appl, parse_date, parse_tab,
)
from trial_pos.services.eligibility import phase_class  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402
from trial_pos.services.tested_agent import is_drug_type  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_EXAMPLES = ("metoprolol", "fluticasone", "testosterone", "bupivacaine",
                    "doxorubicin", "fludarabine", "diclofenac", "methylphenidate",
                    "paliperidone", "amphotericin")
DEFAULT_SHOW = 15
# Words that name a different PRODUCT rather than packaging or schedule. Diagnostic only.
FORMULATION_WORDS = frozenset({
    "extended-release", "delayed-release", "modified-release", "er", "xr", "sr", "xl", "cr",
    "dr", "liposomal", "transdermal", "patch", "inhalation", "inhaler", "nasal",
    "ophthalmic", "topical", "intravenous", "iv", "subcutaneous", "sc", "sq",
    "intramuscular", "im", "depot", "long-acting", "film", "gel", "cream", "ointment",
})
STRIPPING_STEPS = ("no_dose_or_form", "no_salt", "dictionary_no_salt",
                   "no_biosimilar_suffix", "compact")


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def stated_forms(name: str) -> tuple:
    words = [w for w in key(name).replace("(", " ").replace(")", " ").split()]
    return (tuple(sorted({w for w in words if w in SALT_WORDS})),
            tuple(sorted({w for w in words if w in FORMULATION_WORDS})))


def moiety_key(name: str) -> str:
    """Salt and formulation words removed, for grouping only."""
    k = drop_salt(drop_dose_and_form(key(name)))
    return " ".join(w for w in k.split() if w not in SALT_WORDS and w not in FORM_WORDS
                    and w not in FORMULATION_WORDS)


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--examples", nargs="*", default=list(DEFAULT_EXAMPLES))
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    print(f"examples {args.examples} | show {args.show} | salt words {len(SALT_WORDS)} | "
          f"formulation words {sorted(FORMULATION_WORDS)}")

    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    def dc(t, need):
        return list(read_rows(args.dc_dir / m["tables"][t]["file"], need))
    syn = dc("synonyms", ("id", "lname"))
    structs = dc("structures", ("id", "name"))
    s2p = dc("struct2parent", ("struct_id", "parent_id"))
    ob = dc("ob_product", ("id", "ingredient", "appl_no"))
    s2o = dc("struct2obprod", ("struct_id", "prod_id"))
    children = {parse_struct_id(r["struct_id"]) for r in s2p}
    parents = {parse_struct_id(r["parent_id"]) for r in s2p}
    name_of = {parse_struct_id(r["id"]): r["name"] for r in structs}

    _rule("1. DRUGCENTRAL: synonyms carrying a salt word, by what they point at")
    kinds = Counter()
    for r in syn:
        sid = parse_struct_id(r["id"])
        if sid is None or not any(w in SALT_WORDS for w in key(r["lname"]).split()):
            continue
        kinds["salt child in struct2parent" if sid in children else
              "parent with salt children" if sid in parents else
              "no parent relation (salt is a synonym of the moiety)"] += 1
    for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {k:56s} {n:7d}")
    print(f"  struct2parent rows {len(s2p)}; structures {len(structs)}")

    def fda(name):
        text, _enc = decode((args.fda_dir / name).read_bytes())
        return parse_tab(text)["rows"]
    apps = {norm_appl(r["ApplNo"]): r["ApplType"] for r in fda("Applications.txt")}
    first = {}
    for r in fda("Submissions.txt"):
        if r["SubmissionType"] == ORIGINAL and r["SubmissionStatus"] == APPROVED:
            d = parse_date(r["SubmissionStatusDate"])
            a = norm_appl(r["ApplNo"])
            if d and (a not in first or d < first[a]):
                first[a] = d
    products = fda("Products.txt")
    drugs_of_prod = defaultdict(set)
    for r in s2o:
        drugs_of_prod[parse_struct_id(r["prod_id"])].add(parse_struct_id(r["struct_id"]))

    _rule("2. NAMED EXAMPLES")
    for word in args.examples:
        w = key(word)
        print(f"\n  --- {w}")
        for r in structs:
            if w in key(r["name"]).split():
                sid = parse_struct_id(r["id"])
                rel = "child" if sid in children else "parent" if sid in parents else "-"
                print(f"    structure {sid:6d} {rel:7s} {r['name']}")
        hits = Counter((key(r["lname"]), parse_struct_id(r["id"])) for r in syn
                       if w in key(r["lname"]).split())
        for (n, sid), _c in sorted(hits.items(), key=lambda kv: kv[0][0])[:args.show]:
            print(f"    synonym   {n:44s} -> {sid} {name_of.get(sid, '')}")
        ob_ing = defaultdict(set)
        for r in ob:
            if w in key(r["ingredient"]).split():
                ob_ing[key(r["ingredient"])] |= drugs_of_prod.get(parse_struct_id(r["id"]), set())
        for ing, ds in sorted(ob_ing.items())[:args.show]:
            print(f"    orange book ingredient {ing:36s} -> {sorted(ds)}")
        by_ing = defaultdict(set)
        for r in products:
            if w in key(r["ActiveIngredient"]).replace(";", " ").split():
                by_ing[key(r["ActiveIngredient"])].add(norm_appl(r["ApplNo"]))
        for ing, nos in sorted(by_ing.items())[:args.show]:
            innov = [a for a in nos if apps.get(a) in (APPL_NDA, APPL_BLA)]
            dates = sorted(first[a] for a in innov if a in first)
            print(f"    drugs@fda {ing:44s} NDA/BLA {len(innov):3d}  earliest original "
                  f"{dates[0] if dates else '-'}")

    _rule("3. RESOLVER OUTPUT: matched agents whose name states a salt or formulation")
    tally, examples = Counter(), defaultdict(Counter)
    for r in read_rows(args.labels_dir / "trial_drug_agents.csv",
                       ("agent_status", "name_used", "step")):
        if r["agent_status"] not in MATCHING_STATUSES:
            continue
        salts, forms = stated_forms(r["name_used"])
        stripping = any(s in r["step"] for s in STRIPPING_STEPS)
        for label, stated in (("salt", salts), ("formulation", forms)):
            if stated:
                kind = f"{label} stated, matched by a stripping step" if stripping else \
                    f"{label} stated, matched without stripping"
                tally[kind] += 1
                examples[kind][key(r["name_used"])] += 1
    for k in sorted(tally):
        print(f"\n  {k}: {tally[k]}")
        for n, c in sorted(examples[k].items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {c:6d}  {n}")

    _rule("4. FORM-VS-FORM TRIALS: two drug interventions, one moiety, different forms")
    phase = {r["nct_id"]: phase_class(r["phase"])
             for r in read_rows(args.aact_dir / LABELS_FILE, ("nct_id", "is_drug_trial",
                                                               "phase"))
             if tribool(r["is_drug_trial"]) is True}
    by_trial = defaultdict(list)
    for r in read_rows(args.aact_dir / file_name("interventions"),
                       ("nct_id", "intervention_type", "name")):
        if r["nct_id"] in phase and is_drug_type(r["intervention_type"]):
            by_trial[r["nct_id"]].append(r["name"])
    found, by_phase, pairs = [], Counter(), Counter()
    for nct, names in by_trial.items():
        groups = defaultdict(set)
        for n in names:
            mk = moiety_key(n)
            if mk:
                groups[mk].add(stated_forms(n))
        for mk, forms in groups.items():
            if len(forms) > 1:
                found.append(nct)
                by_phase[phase[nct]] += 1
                pairs[(mk, " vs ".join(sorted("/".join(s + f) or "(none)" for s, f in forms)))] += 1
                break
    print(f"  trials {len(found)}; by phase class {dict(sorted(by_phase.items()))}")
    for (mk, vs), c in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show * 2]:
        print(f"    {c:5d}  {mk}: {vs}")
    print(f"  sample trials: {sorted(found)[:args.show]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())