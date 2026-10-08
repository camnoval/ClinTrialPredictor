#!/usr/bin/env python3
"""Evidence for two decisions on the tested form. READ-ONLY. SCRATCH.

  1. Drugs@FDA Products.Form vocabulary, split into its dosage-form and route halves
     ('TABLET, EXTENDED RELEASE;ORAL'), over NDA and BLA products: the strings that
     formulation words would have to be mapped onto
  2. NAMED EXAMPLES: every NDA/BLA product of each example moiety with its ingredient
     string, form, brand and the earliest approved original of its application: whether
     formulations (liposomal, extended release, subcutaneous) are dated apart
  3. D1, UNSTATED FORM: for matched tested agents whose names state no salt and no
     formulation, how many distinct approved (ingredient, dosage form, route) products their
     moiety has. One means the unstated form is the form; more means it is ambiguous.
     Counted over agents and over pivotal trials
  4. D2, DESCRIPTIONS: for the same agents, how often the intervention's description states
     a formulation or route word the name and other names do not, and which words

Moiety -> products uses ob_product (NDA, product level) and, for BLAs, the Drugs@FDA
ingredient string resolved against DrugCentral names, as drug_dictionary does.

Usage (PowerShell):
  python audit\\probe_form_vocab.py | Out-File -Encoding utf8 probe_form_vocab_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
for p in (ROOT / "src", ROOT / "scripts", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from probe_salt_forms import FORMULATION_WORDS, stated_forms  # noqa: E402
from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, LABELS_FILE, load_fda, read_rows,
)
from trial_pos.services.aact_rows import file_name  # noqa: E402
from trial_pos.services.drug_dictionary import (  # noqa: E402
    DRUGCENTRAL_NAME_SOURCES, HIT_RESOLVED, build_dictionary, parse_struct_id,
)
from trial_pos.services.drug_names import key  # noqa: E402
from trial_pos.services.drug_resolution import DRUG_SEP, MATCHING_STATUSES  # noqa: E402
from trial_pos.services.drugsatfda import (  # noqa: E402
    APPL_BLA, APPL_NDA, APPROVED, INGREDIENT_SEPARATORS, ORIGINAL, decode, norm_appl,
    norm_product, parse_date, parse_tab,
)
from trial_pos.services.eligibility import phase_class  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_EXAMPLES = ("doxorubicin", "bupivacaine", "methylphenidate", "metoprolol",
                    "vedolizumab", "amphotericin", "paliperidone", "glucagon")
DEFAULT_SHOW = 40
FORM_SEP = ";"
PIVOTAL = "pivotal"
INNOVATOR = (APPL_NDA, APPL_BLA)


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def split_form(form: str) -> tuple:
    dosage, _sep, route = (form or "").partition(FORM_SEP)
    return dosage.strip().upper(), route.strip().upper()


def ingredient_parts(value: str) -> list:
    parts = [value or ""]
    for sep in INGREDIENT_SEPARATORS:
        parts = [p for part in parts for p in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


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
    print(f"examples {args.examples} | show {args.show} | innovator types {INNOVATOR} | "
          f"formulation words {len(FORMULATION_WORDS)}")

    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    dc = {t: list(read_rows(args.dc_dir / m["tables"][t]["file"], need))
          for t, need in DC_NEED.items()}
    products = load_fda(args.fda_dir, FDA_PRODUCTS)
    applications = load_fda(args.fda_dir, FDA_APPLICATIONS)
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"], products, applications)
    appl_type = {norm_appl(r["ApplNo"]): r["ApplType"] for r in applications}
    text, _enc = decode((args.fda_dir / "Submissions.txt").read_bytes())
    first = {}
    for r in parse_tab(text)["rows"]:
        if r["SubmissionType"] == ORIGINAL and r["SubmissionStatus"] == APPROVED:
            when = parse_date(r["SubmissionStatusDate"])
            a = norm_appl(r["ApplNo"])
            if when and (a not in first or when < first[a]):
                first[a] = when

    # moiety -> innovator products, product level
    drugs_of_prod = defaultdict(set)
    for r in dc["struct2obprod"]:
        drugs_of_prod[parse_struct_id(r["prod_id"])].add(
            d.canonical(parse_struct_id(r["struct_id"])))
    ob_drugs = {}
    for r in dc["ob_product"]:
        ds = drugs_of_prod.get(parse_struct_id(r["id"]))
        if ds:
            ob_drugs[(norm_appl(r["appl_no"]), norm_product(r["product_no"]))] = frozenset(ds)
    innovator, moiety_products = [], defaultdict(set)
    for r in products:
        a = norm_appl(r["ApplNo"])
        if appl_type.get(a) not in INNOVATOR:
            continue
        ds = ob_drugs.get((a, norm_product(r["ProductNo"])))
        if ds is None:
            got = set()
            for part in ingredient_parts(r["ActiveIngredient"]):
                hit, _v = d.resolve_name(part, DRUGCENTRAL_NAME_SOURCES)
                if hit.status != HIT_RESOLVED:
                    got = None
                    break
                got |= hit.drugs
            ds = frozenset(got) if got else None
        dosage, route = split_form(r["Form"])
        innovator.append((r, a, ds, dosage, route))
        if ds:
            identity = (key(r["ActiveIngredient"]), dosage, route)
            for x in ds:
                moiety_products[x].add(identity)

    _rule("1. Products.Form VOCABULARY over NDA/BLA products (dosage form ; route)")
    dosages = Counter(x[3] for x in innovator)
    routes = Counter(x[4] for x in innovator)
    print(f"  NDA/BLA products {len(innovator)}; distinct dosage forms {len(dosages)}; "
          f"routes {len(routes)}; unlinked to a moiety {sum(1 for x in innovator if not x[2])}")
    print("\n  dosage forms:")
    for f, n in sorted(dosages.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {n:6d}  {f}")
    print("\n  routes:")
    for f, n in sorted(routes.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {n:6d}  {f}")

    _rule("2. NAMED EXAMPLES: NDA/BLA products and the first approved original")
    for word in args.examples:
        w = key(word)
        rows = [x for x in innovator if w in key(x[0]["ActiveIngredient"]).replace(";", " ").split()]
        print(f"\n  --- {w}: {len(rows)} products")
        for r, a, ds, dosage, route in sorted(rows, key=lambda x: (str(first.get(x[1])), x[1])):
            print(f"    {str(first.get(a, '-')):10s} {appl_type.get(a, '?'):4s} {a} "
                  f"{key(r['ActiveIngredient'])[:34]:34s} {dosage[:30]:30s} {route[:16]:16s} "
                  f"{r['DrugName'][:20]}")

    phase = {r["nct_id"]: phase_class(r["phase"])
             for r in read_rows(args.aact_dir / LABELS_FILE, ("nct_id", "is_drug_trial",
                                                               "phase"))
             if tribool(r["is_drug_trial"]) is True}
    agents = [r for r in read_rows(args.labels_dir / "trial_drug_agents.csv",
                                   ("nct_id", "intervention_id", "agent_status", "name_used",
                                    "drugs"))
              if r["agent_status"] in MATCHING_STATUSES]
    unstated = [r for r in agents if stated_forms(r["name_used"]) == ((), ())]

    _rule("3. D1: matched agents stating no form -> distinct approved products of the moiety")
    buckets, pivotal_trials = Counter(), defaultdict(set)
    for r in unstated:
        ds = [int(x) for x in r["drugs"].split(DRUG_SEP) if x]
        n = max((len(moiety_products.get(x, ())) for x in ds), default=0)
        b = "0 (no NDA/BLA product)" if n == 0 else "1" if n == 1 else "2-3" if n <= 3 \
            else "4+"
        buckets[b] += 1
        if phase.get(r["nct_id"]) == PIVOTAL:
            pivotal_trials[b].add(r["nct_id"])
    print(f"  matched agents {len(agents)}; stating no salt and no formulation "
          f"{len(unstated)} ({_pct(len(unstated), len(agents))})")
    for b in ("0 (no NDA/BLA product)", "1", "2-3", "4+"):
        print(f"    products {b:24s} agents {buckets[b]:8d} ({_pct(buckets[b], len(unstated))})"
              f"   pivotal trials {len(pivotal_trials[b]):6d}")

    _rule("4. D2: does the description state a form the name does not?")
    names_by_iv = {(r["nct_id"], r["intervention_id"]): r for r in unstated}
    other = defaultdict(list)
    for r in read_rows(args.aact_dir / file_name("intervention_other_names"),
                       ("nct_id", "intervention_id", "name")):
        if (r["nct_id"], r["intervention_id"]) in names_by_iv:
            other[(r["nct_id"], r["intervention_id"])].append(r["name"])
    words, with_desc, stating = Counter(), 0, 0
    for r in read_rows(args.aact_dir / file_name("interventions"),
                       ("nct_id", "id", "description")):
        k = (r["nct_id"], r["id"])
        if k not in names_by_iv:
            continue
        if any(stated_forms(o) != ((), ()) for o in other.get(k, ())):
            continue
        desc = (r["description"] or "").strip()
        if not desc:
            continue
        with_desc += 1
        found = set(stated_forms(desc)[1])
        if found:
            stating += 1
            words.update(found)
    print(f"  unstated agents with a description {with_desc}; description states a "
          f"formulation or route word {stating} ({_pct(stating, with_desc)})")
    for w, n in sorted(words.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {n:7d}  {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())