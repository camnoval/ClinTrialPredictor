#!/usr/bin/env python3
"""Line-up of the stated form (salts, FORM_CLASS_WORDS classes) with Drugs@FDA Products.Form,
before the form-level approval matcher is built (rev 11 section 7 item 4). READ-ONLY. SCRATCH.

The CANDIDATE LINE-UP below is what is under test. It was written from the Products.Form
vocabulary in probe_form_vocab_20261001.txt section 1, and lives here, not in src/, until this
probe's output supports it. Each stated class meets a product form in one of three states:

  confirmed  the product's Form names the class          ('INTRAVENOUS' for intravenous)
  possible   the Form is silent on that dimension         ('INJECTION' for intravenous;
                                                            a blank route for any route)
  excluded   the Form names something else on it          ('ORAL' for intravenous;
                                                            plain 'INJECTABLE' for liposomal)

Two kinds of class. ROUTE classes are read from the route half of the Form only. MODIFIER
classes (release, liposomal, lipid complex, ODT, implant) are searched in the whole Form, and
their absence EXCLUDES (a plain 'INJECTABLE' is not liposomal: Doxil vs doxorubicin, D-12).
A stated salt is compared with the salt words of the ingredient part that is the moiety,
read by drug_names.stated_salts, so both sides are canonicalised the same way.

Sections:
  1. the line-up itself, over NDA/BLA products: per class, the Form strings it confirms and
     those it leaves possible; route items it does not list
  2. stated CLASSES of resolved agents against their moiety's products (exact moiety set):
     best state, and what the moiety does have when every product is excluded
  3. stated SALTS against the ingredient strings, same shape
  4. DATES: is application-original dating safe for a product? Key checks of ob_product
     against Products and struct2obprod, then Orange Book product dates against the
     application's original, and supplements that added a route
  5. SENSITIVITY: pivotal resolved agents with an ACTUAL readout; how many change new-molecule
     status (D-9: no NDA/BLA approval before the readout) between rules
       A moiety set contained, any form        B moiety set exact, any form
       C B + stated form possible or confirmed D B + stated form confirmed only
       C' C dated by Orange Book product dates where they exist
  6. BRANDS: resolved pivotal agents matched by a brand name and stating no form are judged
     at moiety level (D-14). How often the brand's own products are later

Counts only; no lag percentiles (that is Task 2's deliverable, not a probe's).

Usage (PowerShell):
  python audit\\probe_form_lineup.py | Out-File -Encoding utf8 probe_form_lineup_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, LABELS_FILE, load_fda, read_rows,
)
from trial_pos.services.drug_dictionary import (  # noqa: E402
    DRUGCENTRAL_NAME_SOURCES, HIT_RESOLVED, SRC_FDA_BRAND, SRC_OB_TRADE, build_dictionary,
    parse_struct_id,
)
from trial_pos.services.drug_names import (  # noqa: E402
    FORM_CLASSES, drop_parentheticals, key, stated_salts,
)
from trial_pos.services.drug_resolution import (  # noqa: E402
    AGENT_RESOLVED, DRUG_SEP, LIST_SEP, STEP_SEP,
)
from trial_pos.services.drugsatfda import (  # noqa: E402
    APPL_BLA, APPL_NDA, APPROVED, INGREDIENT_SEPARATORS, ORIGINAL, SUPPLEMENT, decode,
    norm_appl, norm_product, parse_date, parse_tab, require_columns,
)
from trial_pos.services.eligibility import phase_class, readout_date  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_SHOW = 15
PIVOTAL = "pivotal"
INNOVATOR = (APPL_NDA, APPL_BLA)          # D-9: ANDAs never count
FORM_SEP = ";"
ROUTE_ITEM_SEP = ","
SUBMISSIONS = "Submissions.txt"
JOIN_ACTIONS = "Join_Submission_ActionTypes_Lookup.txt"
ACTION_LOOKUP = "ActionTypes_Lookup.txt"
# ActionTypes_Lookup's level-2 code for "Efficacy-New Route Of Administration"
# (probe_source_files_20261001.txt section 3).
NEW_ROUTE_LEVEL2 = "ROUTE"
# Approval dates before the 1938 FD&C Act are placeholders (one ORIG is dated 1900-01-01,
# probe_source_files_20261001.txt 4c). Such a date is treated as undated.
EARLIEST_REAL_APPROVAL = date(1939, 1, 1)
# Report bucket only, not a verdict: date shifts are printed split at one year.
SHIFT_REPORT_DAYS = 365
AGENTS_FILE = "trial_drug_agents.csv"
AGENT_NEED = ("nct_id", "agent_status", "drugs", "name_used", "source", "matched_key",
              "stated_salts", "stated_form_classes", "stated_form_words", "salt_conflict")
OB_NEED = DC_NEED["ob_product"] + ("appl_type", "approval_date", "dose_form", "route")
OB_INNOVATOR_TYPE = "N"                  # ob_product.appl_type vocabulary {'A', 'N'}

# ---- the CANDIDATE line-up (under test) --------------------------------------------------
CONFIRMED, POSSIBLE, EXCLUDED = "confirmed", "possible", "excluded"
STATE_ORDER = (EXCLUDED, POSSIBLE, CONFIRMED)
_RANK = {s: i for i, s in enumerate(STATE_ORDER)}

# Modifier classes: a token anywhere in the Form (dosage ; route), upper case.
MODIFIER_TOKENS = {
    "extended_release": ("EXTENDED RELEASE",),
    "delayed_release": ("DELAYED RELEASE", "DELAYED REL"),
    # FDA has no 'modified release' dosage form; it is either of the two above.
    "modified_release": ("EXTENDED RELEASE", "DELAYED RELEASE", "DELAYED REL"),
    "orally_disintegrating": ("ORALLY DISINTEGRATING",),
    "liposomal": ("LIPOSOMAL",),
    "lipid_complex": ("LIPID COMPLEX",),
    "implant": ("IMPLANT",),             # dosage IMPLANT and route IMPLANTATION
}
# A dosage half that says nothing: modifiers are possible, not excluded.
SILENT_DOSAGE = frozenset({"", "N/A", "UNKNOWN"})

# Route classes: route items (route half split on ',') that confirm them.
ROUTE_CONFIRMS = {
    "INTRAVENOUS": ("intravenous",), "IV (INFUSION)": ("intravenous",),
    "IM-IV": ("intramuscular", "intravenous"),
    "SUBCUTANEOUS": ("subcutaneous",), "SUBCUTANEOUS LYOPHILIZED POWER": ("subcutaneous",),
    "INTRAMUSCULAR": ("intramuscular",),
    "ORAL": ("oral",), "ORAL-20": ("oral",), "ORAL-21": ("oral",), "ORAL-28": ("oral",),
    "ORAL SUSPENSION": ("oral",), "ORALLY DISINTEGRATING": ("oral",),
    "TOPICAL": ("topical",), "TRANSDERMAL": ("transdermal",), "NASAL": ("nasal",),
    "INHALATION": ("inhalation",), "OPHTHALMIC": ("ophthalmic",),
    "INTRATHECAL": ("intrathecal",), "SPINAL": ("intrathecal",),
    "INTRAVITREAL": ("intravitreal",), "VAGINAL": ("vaginal",), "RECTAL": ("rectal",),
    "SUBLINGUAL": ("sublingual",), "BUCCAL": ("buccal",),
}
# Route items that are a GENERIC for some route classes: possible for those, excluding others.
PARENTERAL = ("intravenous", "subcutaneous", "intramuscular", "intrathecal", "intravitreal")
ROUTE_GENERIC = {
    "INJECTION": PARENTERAL,
    "TRANSMUCOSAL": ("buccal", "sublingual"),
    "INTRAOCULAR": ("intravitreal",),
}
# Route items that say nothing about route: every route class possible.
ROUTE_SILENT = frozenset({"", "N/A", "SINGLE-USE", "SINGLE-DOSE", "MULTIDOSE", "PREFILLED",
                          "FOR RX COMPOUNDING", "SOLUTION", "SUSPENSION", "POWDER",
                          "FOR SOLUTION"})
# Specific routes no class names: they exclude every route class.
ROUTE_OTHER = frozenset({
    "INTRAPERITONEAL", "IRRIGATION", "OTIC", "INTRA-ARTERIAL", "INTRAUTERINE", "DENTAL",
    "URETHRAL", "URETERAL", "INTRATRACHEAL", "INTRAVESICAL", "INTRAPLEURAL", "PERIARTICULAR",
    "EPIDURAL", "PERIODONTAL", "INTRA-ARTICULAR", "INTRACAVITARY", "INTERSTITIAL",
    "IONTOPHORESIS", "ENDOCERVICAL", "ENTERAL", "INFILTRATION", "INTRA-ANAL", "INTRACAMERAL",
    "INTRACRANIAL", "INTRADERMAL", "INTRALYMPHATIC", "INTRAOSSEOUS", "ENDOTRACHEAL",
    "INTRAVESICULAR", "PERFUSION", "BILIARY", "CARDIAC", "PYELOCALYCEAL", "IMPLANTATION",
})
ROUTE_CLASSES = tuple(sorted({c for cs in ROUTE_CONFIRMS.values() for c in cs}))


def check_lineup() -> None:
    """Fail closed: every FORM_CLASS is in exactly one table, and no class is invented."""
    mods, routes = set(MODIFIER_TOKENS), set(ROUTE_CLASSES)
    generic = {c for cs in ROUTE_GENERIC.values() for c in cs}
    problems = []
    if mods & routes:
        problems.append(f"in both tables: {sorted(mods & routes)}")
    if (mods | routes) != set(FORM_CLASSES):
        problems.append(f"missing {sorted(set(FORM_CLASSES) - mods - routes)}, "
                        f"unknown {sorted((mods | routes) - set(FORM_CLASSES))}")
    if not generic <= routes:
        problems.append(f"generic names non-route classes {sorted(generic - routes)}")
    overlap = (set(ROUTE_CONFIRMS) | set(ROUTE_GENERIC)) & (ROUTE_SILENT | ROUTE_OTHER)
    if overlap or set(ROUTE_CONFIRMS) & set(ROUTE_GENERIC) or ROUTE_SILENT & ROUTE_OTHER:
        problems.append(f"route item listed twice: {sorted(overlap)}")
    if problems:
        raise SystemExit("!! line-up inconsistent: " + "; ".join(problems))


def split_form(form: str) -> tuple:
    dosage, _sep, route = (form or "").partition(FORM_SEP)
    items = tuple(sorted({i.strip() for i in route.upper().split(ROUTE_ITEM_SEP)}))
    return dosage.strip().upper(), route.strip().upper(), items


def unlisted_items(form: str) -> tuple:
    """Route items in no table. class_state reads them as silent (possible)."""
    known = set(ROUTE_CONFIRMS) | set(ROUTE_GENERIC) | ROUTE_SILENT | ROUTE_OTHER
    return tuple(i for i in split_form(form)[2] if i not in known)


def class_state(klass: str, form: str) -> str:
    dosage, route, items = split_form(form)
    if klass in MODIFIER_TOKENS:
        whole = f"{dosage};{route}"
        if any(t in whole for t in MODIFIER_TOKENS[klass]):
            return CONFIRMED
        return POSSIBLE if dosage in SILENT_DOSAGE else EXCLUDED
    state = EXCLUDED
    for item in items:
        if klass in ROUTE_CONFIRMS.get(item, ()):
            return CONFIRMED
        if item in ROUTE_GENERIC:
            if klass in ROUTE_GENERIC[item]:
                state = POSSIBLE
        elif item in ROUTE_SILENT:
            state = POSSIBLE
        elif item not in ROUTE_CONFIRMS and item not in ROUTE_OTHER:
            state = POSSIBLE              # unlisted: read as silent, printed in section 1
    return state


def salt_state(stated: tuple, part_salts) -> str:
    """`part_salts`: the salts of the moiety's ingredient part(s), or None when unknown."""
    if not stated:
        return CONFIRMED
    if part_salts is None:
        return POSSIBLE
    return CONFIRMED if set(stated) <= set(part_salts) else EXCLUDED


# ---- helpers --------------------------------------------------------------------------------
def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def ingredient_parts(value: str) -> list:
    parts = [value or ""]
    for sep in INGREDIENT_SEPARATORS:
        parts = [p for part in parts for p in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def real_date(raw):
    d = parse_date(raw)
    return d if d is not None and d >= EARLIEST_REAL_APPROVAL else None


def load_tab(folder: Path, name: str, need: tuple) -> list:
    path = folder / name
    if not path.exists():
        raise SystemExit(f"!! {path} not found")
    text, enc = decode(path.read_bytes())
    table = parse_tab(text)
    require_columns(table, need, name)
    print(f"  {name:38s} rows {len(table['rows']):8d}  malformed {table['malformed']}  {enc}")
    return table["rows"]


def split_list(raw) -> tuple:
    return tuple(x for x in (raw or "").split(LIST_SEP) if x)


class Product:
    __slots__ = ("appl", "prod", "appl_type", "form", "ingredient", "brand", "moieties",
                 "how", "part_salts", "app_date", "ob_date")


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    check_lineup()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = ap.parse_args()
    print(f"show {args.show} | innovator {INNOVATOR} | undated before {EARLIEST_REAL_APPROVAL} "
          f"| shift bucket {SHIFT_REPORT_DAYS} days | classes {len(FORM_CLASSES)} "
          f"(modifier {len(MODIFIER_TOKENS)}, route {len(ROUTE_CLASSES)})")

    _rule("INPUTS")
    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    need = dict(DC_NEED, ob_product=OB_NEED)
    dc = {t: list(read_rows(args.dc_dir / m["tables"][t]["file"], n)) for t, n in need.items()}
    for t, rows in dc.items():
        print(f"  dc {t:35s} rows {len(rows):8d}")
    products_raw = load_fda(args.fda_dir, FDA_PRODUCTS)
    applications = load_fda(args.fda_dir, FDA_APPLICATIONS)
    submissions = load_tab(args.fda_dir, SUBMISSIONS, ("ApplNo", "SubmissionType",
                                                      "SubmissionNo", "SubmissionStatus",
                                                      "SubmissionStatusDate"))
    joins = load_tab(args.fda_dir, JOIN_ACTIONS, ("ApplNo", "SubmissionType", "SubmissionNo",
                                                  "ActionTypes_LookupID"))
    actions = load_tab(args.fda_dir, ACTION_LOOKUP, ("ActionTypes_LookupID",
                                                     "SupplCategoryLevel2Code"))
    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"], products_raw, applications)
    name_of = {parse_struct_id(r["id"]): r["name"] for r in dc["structures"]}

    appl_type = {norm_appl(r["ApplNo"]): r["ApplType"] for r in applications}
    app_date, undated_apps = {}, set()
    for r in submissions:
        if r["SubmissionType"] == ORIGINAL and r["SubmissionStatus"] == APPROVED:
            a = norm_appl(r["ApplNo"])
            when = real_date(r["SubmissionStatusDate"])
            if when is None:
                undated_apps.add(a)
            elif a not in app_date or when < app_date[a]:
                app_date[a] = when

    # ob_product: product-level moieties (as drug_dictionary links them) and dates
    drugs_of_prod = defaultdict(set)
    for r in dc["struct2obprod"]:
        drugs_of_prod[parse_struct_id(r["prod_id"])].add(parse_struct_id(r["struct_id"]))
    ob_by_key, ob_app_sets, ob_key_rows = {}, defaultdict(set), Counter()
    for r in dc["ob_product"]:
        ds = frozenset(drugs_of_prod.get(parse_struct_id(r["id"]), ()))
        k = (norm_appl(r["appl_no"]), norm_product(r["product_no"]))
        ob_key_rows[k] += 1
        ob_by_key[k] = (r, ds)
        if ds:
            ob_app_sets[k[0]].add(ds)

    part_cache = {}

    def part_moiety(part):
        if part not in part_cache:
            hit, _v = d.resolve_name(part, DRUGCENTRAL_NAME_SOURCES)
            part_cache[part] = hit.drugs if hit.status == HIT_RESOLVED else None
        return part_cache[part]

    products, not_in_applications = [], 0
    for r in products_raw:
        a, pn = norm_appl(r["ApplNo"]), norm_product(r["ProductNo"])
        t = appl_type.get(a)
        if t is None:
            not_in_applications += 1
            continue
        if t not in INNOVATOR:
            continue
        p = Product()
        p.appl, p.prod, p.appl_type, p.form = a, pn, t, r["Form"]
        p.ingredient, p.brand = r["ActiveIngredient"], key(r["DrugName"])
        ob = ob_by_key.get((a, pn))
        parts = ingredient_parts(r["ActiveIngredient"])
        by_part = [(part, part_moiety(part)) for part in parts]
        if ob and ob[1]:
            p.moieties, p.how = ob[1], "ob_product"
        elif len(ob_app_sets.get(a, ())) == 1:
            p.moieties, p.how = next(iter(ob_app_sets[a])), "ob_application"
        elif parts and all(ms for _part, ms in by_part):
            p.moieties, p.how = frozenset().union(*(ms for _part, ms in by_part)), "ingredient"
        else:
            p.moieties, p.how = None, "unlinked"
        # salts per moiety: from the part naming it; a lone part stands for the whole product
        p.part_salts = {}
        if p.moieties:
            for part, ms in by_part:
                for x in (ms or ()):
                    p.part_salts.setdefault(x, set()).update(stated_salts(part))
            if len(parts) == 1 and len(p.moieties) == 1:
                x = next(iter(p.moieties))
                p.part_salts.setdefault(x, set()).update(stated_salts(parts[0]))
        p.app_date = app_date.get(a)
        p.ob_date = real_date(ob[0].get("approval_date")) if ob else None
        products.append(p)
    by_moiety = defaultdict(list)
    for p in products:
        for x in (p.moieties or ()):
            by_moiety[x].append(p)
    print(f"  NDA/BLA products {len(products)}; linked "
          f"{sum(1 for p in products if p.moieties)}; by "
          f"{dict(sorted(Counter(p.how for p in products).items()))}; products whose ApplNo "
          f"is absent from Applications (type unknown, not used) {not_in_applications}")
    print(f"  NDA/BLA products whose application has no dated approved original: "
          f"{sum(1 for p in products if p.app_date is None)}")

    # ---------------------------------------------------------------------------------------
    _rule("1. THE CANDIDATE LINE-UP over NDA/BLA products (Form = dosage ; route)")
    forms = Counter(p.form.upper() for p in products)
    unlisted = Counter()
    per_class = {c: {s: Counter() for s in STATE_ORDER} for c in FORM_CLASSES}
    for f, n in forms.items():
        for item in unlisted_items(f):
            unlisted[item] += n
        for c in FORM_CLASSES:
            per_class[c][class_state(c, f)][f] += n
    print(f"  distinct Form strings {len(forms)}")
    for c in FORM_CLASSES:
        kind = "modifier" if c in MODIFIER_TOKENS else "route"
        tot = {s: sum(per_class[c][s].values()) for s in STATE_ORDER}
        print(f"\n  {c} ({kind}): confirmed {tot[CONFIRMED]}, possible {tot[POSSIBLE]}, "
              f"excluded {tot[EXCLUDED]} products")
        for s in (CONFIRMED, POSSIBLE):
            top = sorted(per_class[c][s].items(), key=lambda kv: (-kv[1], kv[0]))
            for f, n in top[:args.show]:
                print(f"      {s:9s} {n:6d}  {f}")
            if len(top) > args.show:
                print(f"      {s:9s} ... {len(top) - args.show} more Form strings")
    print(f"\n  route items in no table (read as silent, so possible for every route class), "
          f"products: {dict(sorted(unlisted.items())) or 'none'}")
    no_class = Counter()
    for f, n in forms.items():
        if not any(class_state(c, f) == CONFIRMED for c in FORM_CLASSES):
            no_class[f] += n
    print(f"  Form strings confirming no class at all: {len(no_class)} strings, "
          f"{sum(no_class.values())} products; top:")
    for f, n in sorted(no_class.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"      {n:6d}  {f}")

    # ---------------------------------------------------------------------------------------
    labels = {}
    for r in read_rows(args.aact_dir / LABELS_FILE, ("nct_id", "is_drug_trial", "phase",
                                                      "primary_completion_date",
                                                      "primary_completion_date_type")):
        if tribool(r["is_drug_trial"]) is True:
            labels[r["nct_id"]] = (phase_class(r["phase"]), readout_date(r))
    agents = []
    for r in read_rows(args.labels_dir / AGENTS_FILE, AGENT_NEED):
        if r["agent_status"] != AGENT_RESOLVED:
            continue
        ms = frozenset(int(x) for x in r["drugs"].split(DRUG_SEP) if x)
        if not ms:
            raise SystemExit(f"!! resolved agent with no drug: {r}")
        ph, ro = labels.get(r["nct_id"], (None, None))
        agents.append({"nct": r["nct_id"], "moieties": ms, "name": r["name_used"],
                       "source": r["source"], "matched_key": r["matched_key"],
                       "salts": split_list(r["stated_salts"]),
                       "classes": split_list(r["stated_form_classes"]),
                       "words": split_list(r["stated_form_words"]),
                       "salt_conflict": r["salt_conflict"] == "True",
                       "pivotal": ph == PIVOTAL, "readout": ro})

    def exact(a):
        x = next(iter(a["moieties"]))
        return [p for p in by_moiety.get(x, ()) if p.moieties == a["moieties"]]

    def contains(a):
        x = next(iter(a["moieties"]))
        return [p for p in by_moiety.get(x, ()) if a["moieties"] <= p.moieties]

    def product_salts(a, p):
        """Salts on the parts naming the agent's moieties; None when any is unknown."""
        known = [p.part_salts.get(x) for x in a["moieties"]]
        return set().union(*known) if all(k is not None for k in known) else None

    def form_state(a, p):
        """The weakest state over every stated dimension (classes and salt)."""
        states = [class_state(c, p.form) for c in a["classes"]]
        if a["salts"]:
            states.append(salt_state(a["salts"], product_salts(a, p)))
        return min(states, key=_RANK.get) if states else CONFIRMED

    # ---------------------------------------------------------------------------------------
    _rule("2. STATED CLASSES of resolved agents vs their moiety's NDA/BLA products "
          "(exact moiety set)")
    print("  best state over the products: confirmed > possible > excluded; 'no product' = "
          "the exact moiety set has no NDA/BLA product")
    print(f"  resolved agents {len(agents)}; stating >= 1 class "
          f"{sum(1 for a in agents if a['classes'])}; stating >= 2 route classes "
          f"{sum(1 for a in agents if len([c for c in a['classes'] if c in ROUTE_CLASSES]) > 1)}")
    print(f"\n  {'class':22s} {'agents':>7s} {'pivotal':>7s} {'confirmed':>10s} "
          f"{'possible':>9s} {'excluded':>9s} {'no product':>11s}")
    examples = defaultdict(Counter)
    generic_used = defaultdict(Counter)
    for c in FORM_CLASSES:
        row = Counter()
        for a in agents:
            if c not in a["classes"]:
                continue
            row["agents"] += 1
            row["pivotal"] += a["pivotal"]
            ps = exact(a)
            if not ps:
                row["no product"] += 1
                continue
            best = max((class_state(c, p.form) for p in ps), key=_RANK.get)
            row[best] += 1
            if best == EXCLUDED:
                have = " | ".join(sorted({p.form.upper() for p in ps}))
                examples[c][(key(a["name"]), "|".join(a["words"]), have)] += 1
            elif best == POSSIBLE:
                for p in ps:
                    if class_state(c, p.form) == POSSIBLE:
                        generic_used[c][split_form(p.form)[1] or "(blank)"] += 1
        print(f"  {c:22s} {row['agents']:7d} {row['pivotal']:7d} {row[CONFIRMED]:10d} "
              f"{row[POSSIBLE]:9d} {row[EXCLUDED]:9d} {row['no product']:11d}")
    print("\n  'possible only' by the product route that allowed it (product rows, top 5):")
    for c in FORM_CLASSES:
        if generic_used[c]:
            top = sorted(generic_used[c].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            print(f"    {c:22s} " + "; ".join(f"{r} {n}" for r, n in top))
    print(f"\n  EXCLUDED BY EVERY PRODUCT, top {args.show} per class "
          "(agents: name | words stated | the moiety's Form strings):")
    for c in FORM_CLASSES:
        if not examples[c]:
            continue
        print(f"\n    {c}: {sum(examples[c].values())} agents")
        for (nm, w, have), n in sorted(examples[c].items(),
                                       key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"      {n:5d}  {nm[:40]:40s} [{w}]  has: {have[:150]}")

    # ---------------------------------------------------------------------------------------
    _rule("3. STATED SALTS vs the ingredient part that is the moiety (exact moiety set)")
    print(f"  agents stating a salt {sum(1 for a in agents if a['salts'])}; stating more than "
          f"one (salt_conflict) {sum(1 for a in agents if a['salt_conflict'])}")
    by_salt = defaultdict(Counter)
    salt_examples = Counter()
    for a in agents:
        if not a["salts"] or a["salt_conflict"]:
            continue
        label = "+".join(a["salts"])
        by_salt[label]["agents"] += 1
        ps = exact(a)
        if not ps:
            by_salt[label]["no product"] += 1
            continue
        best = max((salt_state(a["salts"], product_salts(a, p)) for p in ps),
                   key=_RANK.get)
        by_salt[label][best] += 1
        if best == EXCLUDED:
            have = sorted({"+".join(sorted(product_salts(a, p) or ())) or "(none)" for p in ps})
            salt_examples[(key(a["name"]), label, " | ".join(have))] += 1
    print(f"\n  {'stated salt':24s} {'agents':>7s} {'confirmed':>10s} {'unknown':>8s} "
          f"{'excluded':>9s} {'no product':>11s}")
    for label, row in sorted(by_salt.items(), key=lambda kv: (-kv[1]["agents"], kv[0])):
        print(f"  {label:24s} {row['agents']:7d} {row[CONFIRMED]:10d} {row[POSSIBLE]:8d} "
              f"{row[EXCLUDED]:9d} {row['no product']:11d}")
    print(f"\n  EXCLUDED BY EVERY PRODUCT, top {args.show * 2} "
          "(name | stated | salts the moiety's products carry):")
    for (nm, s, have), n in sorted(salt_examples.items(),
                                   key=lambda kv: (-kv[1], kv[0]))[:args.show * 2]:
        print(f"    {n:5d}  {nm[:44]:44s} [{s}]  has: {have[:100]}")

    # ---------------------------------------------------------------------------------------
    _rule("4. DATES: application original vs Orange Book product date (NDA products)")
    # key check 1: ob_product (appl_no, product_no) names the same product as Products
    products_by_key = {(norm_appl(r["ApplNo"]), norm_product(r["ProductNo"])): r
                       for r in products_raw}
    joined = agree_ing = agree_dosage = agree_route = 0
    disagree = Counter()
    for k, (r, _ds) in ob_by_key.items():
        if r.get("appl_type") != OB_INNOVATOR_TYPE:
            continue
        pr = products_by_key.get(k)
        if pr is None:
            continue
        joined += 1
        same_ing = key(r["ingredient"]) == key(pr["ActiveIngredient"])
        same_dosage = key(r["dose_form"]) == key(split_form(pr["Form"])[0])
        agree_ing += same_ing
        agree_dosage += same_dosage
        agree_route += key(r["route"]) == key(split_form(pr["Form"])[1])
        if not (same_ing and same_dosage):
            disagree[(key(r["ingredient"])[:30], key(pr["ActiveIngredient"])[:30],
                      key(r["dose_form"])[:24], key(split_form(pr["Form"])[0])[:24])] += 1
    n_ob_n = sum(1 for r, _ in ob_by_key.values() if r.get("appl_type") == OB_INNOVATOR_TYPE)
    ob_bad_dates = Counter(r["approval_date"] for r, _ in ob_by_key.values()
                           if (r.get("approval_date") or "").strip()
                           and parse_date(r["approval_date"]) is None)
    ob_floor = sum(1 for r, _ in ob_by_key.values()
                   if parse_date(r.get("approval_date")) is not None
                   and real_date(r["approval_date"]) is None)
    ob_blank = sum(1 for r, _ in ob_by_key.values() if not (r.get("approval_date") or "").strip())
    print(f"  ob_product approval_date: blank {ob_blank}, "
          f"unparseable {sum(ob_bad_dates.values())} {dict(ob_bad_dates.most_common(3))}, "
          f"before {EARLIEST_REAL_APPROVAL} {ob_floor}")
    repeated = {k: n for k, n in ob_key_rows.items() if n > 1}
    print(f"  KEY CHECK ob_product (appl_no, product_no) unique: rows {sum(ob_key_rows.values())}, "
          f"keys {len(ob_key_rows)}, keys on more than one row {len(repeated)} "
          f"(the last row wins here and in drug_dictionary) e.g. {sorted(repeated)[:3]}")
    print(f"  KEY CHECK ob_product (appl_no, product_no) -> Products: ob rows of type "
          f"'{OB_INNOVATOR_TYPE}' {n_ob_n}; found in Products {joined}; same ingredient "
          f"{agree_ing} ({_pct(agree_ing, joined)}); same dosage form {agree_dosage} "
          f"({_pct(agree_dosage, joined)}); same route {agree_route} "
          f"({_pct(agree_route, joined)})")
    for (i1, i2, f1, f2), n in sorted(disagree.items(),
                                      key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"      {n:4d}  ob '{i1}' / '{f1}'   vs   fda '{i2}' / '{f2}'")
    # key check 2: struct2obprod.prod_id -> ob_product.id, single-ingredient products
    ob_ids = {parse_struct_id(r["id"]) for r in dc["ob_product"]}
    s2o_ids = set(drugs_of_prod)
    single = same = 0
    for r, ds in ob_by_key.values():
        parts = ingredient_parts(r["ingredient"])
        if len(parts) != 1 or len(ds) != 1:
            continue
        got = part_moiety(parts[0])
        if got is None:
            continue
        single += 1
        same += got == ds
    print(f"  KEY CHECK struct2obprod.prod_id -> ob_product.id: prod ids {len(s2o_ids)}, of them "
          f"ob_product ids {len(s2o_ids & ob_ids)}; single-ingredient products whose name "
          f"resolves {single}, to the same structure as struct2obprod {same} "
          f"({_pct(same, single)})")

    rel = Counter()
    later_new_form, later_examples = Counter(), Counter()
    nda_by_app = defaultdict(list)
    for p in products:
        if p.appl_type == APPL_NDA:
            nda_by_app[p.appl].append(p)
    for a, ps in nda_by_app.items():
        orig_forms = {split_form(p.form)[:2] for p in ps
                      if p.ob_date is not None and p.app_date is not None
                      and p.ob_date == p.app_date}
        for p in ps:
            if (p.appl, p.prod) not in ob_by_key:
                rel["not in ob_product"] += 1
            elif p.app_date is None:
                rel["application undated"] += 1
            elif p.ob_date is None:
                rel["ob date null or before the floor"] += 1
            elif p.ob_date < p.app_date:
                rel["ob date EARLIER than the original"] += 1
            elif p.ob_date == p.app_date:
                rel["same day as the original"] += 1
            else:
                rel["later than the original"] += 1
                if orig_forms and split_form(p.form)[:2] not in orig_forms:
                    later_new_form["form not among the original's products"] += 1
                    later_examples[(p.appl, p.ingredient[:34], p.form.upper()[:40],
                                    str(p.app_date), str(p.ob_date))] += 1
                elif not orig_forms:
                    later_new_form["original's products undated: cannot tell"] += 1
                else:
                    later_new_form["same form as an original product"] += 1
    n_nda = sum(len(v) for v in nda_by_app.values())
    print(f"\n  NDA products {n_nda}, Orange Book product date against the application's "
          f"first approved original:")
    for k_, n in sorted(rel.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {k_:40s} {n:7d}  {_pct(n, n_nda)}")
    print("  products dated later than their original:")
    for k_, n in sorted(later_new_form.items()):
        print(f"    {k_:50s} {n:7d}")
    print(f"  ... form not among the original's, first {args.show} by application "
          "(appl | ingredient | form | original | product):")
    for (a_, ing, f, d1, d2), _n in sorted(later_examples.items())[:args.show]:
        print(f"    {a_} {ing:34s} {f:40s} {d1} {d2}")
    level2 = {r["ActionTypes_LookupID"]: r["SupplCategoryLevel2Code"] for r in actions}
    route_ids = {i for i, c in level2.items() if c == NEW_ROUTE_LEVEL2}
    approved_suppl = {(norm_appl(r["ApplNo"]), r["SubmissionNo"]) for r in submissions
                      if r["SubmissionType"] == SUPPLEMENT and r["SubmissionStatus"] == APPROVED}
    new_route = Counter()
    for r in joins:
        k_ = (norm_appl(r["ApplNo"]), r["SubmissionNo"])
        if r["SubmissionType"] == SUPPLEMENT and r["ActionTypes_LookupID"] in route_ids \
                and k_ in approved_suppl:
            new_route[appl_type.get(k_[0], "?")] += 1
    print(f"\n  approved supplements with action level-2 '{NEW_ROUTE_LEVEL2}' (new route of "
          f"administration), by ApplType: {dict(sorted(new_route.items())) or 'none'}; "
          f"action ids {sorted(route_ids)}")

    # ---------------------------------------------------------------------------------------
    _rule("5. SENSITIVITY: pivotal resolved agents with an ACTUAL readout")
    piv = [a for a in agents if a["pivotal"] and a["readout"] is not None]
    print(f"  agents {len(piv)} in {len({a['nct'] for a in piv})} trials; stating a form "
          f"{sum(1 for a in piv if a['classes'] or a['salts'])}; multi-moiety "
          f"{sum(1 for a in piv if len(a['moieties']) > 1)}")

    def app_dated(p):
        return p.app_date

    def ob_dated(p):
        return p.ob_date if p.ob_date is not None else p.app_date

    def summary(ps, r, dated):
        ds = [dated(p) for p in ps if dated(p) is not None]
        before = [x for x in ds if x < r]
        after = [x for x in ds if x >= r]
        if not ps:
            status = "no product"
        elif before:
            status = "approved before readout"
        elif not ds:
            status = "products, none dated"
        else:
            status = "not approved before readout"
        return status, (min(after) if after else None)

    rules = {}
    for a in piv:
        r = a["readout"]
        pa, pb = contains(a), exact(a)
        states = {id(p): form_state(a, p) for p in pb}
        pc = [p for p in pb if states[id(p)] != EXCLUDED]
        pd = [p for p in pb if states[id(p)] == CONFIRMED]
        rules[id(a)] = {"A": summary(pa, r, app_dated), "B": summary(pb, r, app_dated),
                        "C": summary(pc, r, app_dated), "D": summary(pd, r, app_dated),
                        "C'": summary(pc, r, ob_dated)}
    statuses = ("approved before readout", "not approved before readout",
                "products, none dated", "no product")
    print(f"\n  {'rule':6s}" + "".join(f"{s:>30s}" for s in statuses))
    for name in ("A", "B", "C", "D", "C'"):
        c = Counter(rules[id(a)][name][0] for a in piv)
        print(f"  {name:6s}" + "".join(f"{c[s]:30d}" for s in statuses))
    for x, y in (("A", "B"), ("B", "C"), ("C", "D"), ("C", "C'")):
        flips = Counter((rules[id(a)][x][0], rules[id(a)][y][0]) for a in piv
                        if rules[id(a)][x][0] != rules[id(a)][y][0])
        print(f"\n  {x} -> {y}: {sum(flips.values())} agents change status")
        for (s1, s2), n in sorted(flips.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"      {n:6d}  {s1}  ->  {s2}")
        shift = Counter()
        for a in piv:
            (s1, f1), (s2, f2) = rules[id(a)][x], rules[id(a)][y]
            if s1 == s2 == "not approved before readout":
                if f1 == f2:
                    shift["same first approval after readout"] += 1
                elif f1 is None or f2 is None:
                    shift["one rule has none after readout"] += 1
                elif abs((f2 - f1).days) <= SHIFT_REPORT_DAYS:
                    shift[f"differs by <= {SHIFT_REPORT_DAYS} days"] += 1
                else:
                    shift[f"differs by > {SHIFT_REPORT_DAYS} days"] += 1
        print(f"      among agents not approved before readout under both: "
              f"{dict(sorted(shift.items()))}")
    flipped = Counter()
    for a in piv:
        if rules[id(a)]["C"][0] != rules[id(a)]["D"][0]:
            flipped[(key(a["name"]), "|".join(a["words"] + a["salts"]),
                     " + ".join(sorted(name_of.get(x, str(x)) for x in a["moieties"])))] += 1
    print(f"\n  C -> D flips, top {args.show} (name | stated | moiety):")
    for (nm, w, mo), n in sorted(flipped.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {n:5d}  {nm[:40]:40s} [{w}]  {mo[:40]}")

    # ---------------------------------------------------------------------------------------
    _rule("6. BRANDS: pivotal agents matched through a brand name, stating no form (D-14)")
    brand_sources = {SRC_OB_TRADE, SRC_FDA_BRAND}
    # A brand key reaches a product by its Drugs@FDA name, that name without brackets
    # ('DOXIL (LIPOSOMAL)' -> 'doxil'), or its Orange Book trade name.
    by_brand = defaultdict(set)
    for p in products:
        by_brand[p.brand].add(p)
        by_brand[drop_parentheticals(p.brand)].add(p)
        ob = ob_by_key.get((p.appl, p.prod))
        if ob:
            by_brand[key(ob[0]["trade_name"])].add(p)
    bc, bex = Counter(), Counter()
    for a in piv:
        if a["classes"] or a["salts"]:
            continue
        if not set(a["source"].split(STEP_SEP)) & brand_sources:
            continue
        bc["agents"] += 1
        bp = [p for p in by_brand.get(key(a["matched_key"]), ()) if p.app_date]
        if not bp:
            bc["brand has no dated NDA/BLA product"] += 1
            continue
        moiety_first = min((p.app_date for p in exact(a) if p.app_date), default=None)
        brand_first = min(p.app_date for p in bp)
        r = a["readout"]
        if moiety_first is None or brand_first == moiety_first:
            bc["brand first approval = moiety first approval"] += 1
        elif brand_first > moiety_first:
            bc["brand first approval later"] += 1
            if moiety_first < r <= brand_first:
                bc["  ... and moiety-level says approved before readout, brand says not"] += 1
                bex[(key(a["name"]), str(moiety_first), str(brand_first))] += 1
        else:
            bc["brand first approval earlier (brand outside the exact moiety set)"] += 1
    order = ("agents", "brand has no dated NDA/BLA product",
             "brand first approval = moiety first approval", "brand first approval later",
             "  ... and moiety-level says approved before readout, brand says not",
             "brand first approval earlier (brand outside the exact moiety set)")
    assert set(bc) <= set(order), sorted(set(bc) - set(order))
    for k_ in order:
        print(f"  {k_:72s} {bc[k_]:6d}")
    for (nm, m1, b1), n in sorted(bex.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
        print(f"    {n:5d}  {nm[:40]:40s} moiety {m1}  brand {b1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())