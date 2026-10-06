#!/usr/bin/env python3
"""Drugs@FDA data-file probe. READ-ONLY, writes nothing.

SCRATCH. Decision (b), 2026-10-06: dated approval events come from Drugs@FDA, indication
codes from DrugCentral. Run before any join is designed.

Reads the unzipped drugsatfda.zip (12 tab-delimited tables, no quoting, per FDA's data
definitions) and answers, in order:

  1. which files exist, which encoding decoded them, rows, rows whose field count is not
     the header's, and declared columns that are absent
  2. key uniqueness for applications, products and submissions, and exact duplicate rows
  3. application types; submission type x status x class vocabulary
  4. approval dates: coverage, range, unparseable values, and the latest readout whose
     W-year window can close against THIS source
  5. whether a new-indication approval is separable: the full class vocabulary for
     supplements, how often their public notes are filled and what they say, and which
     document types are attached -- the files carry no indication field
  6. the name key: active-ingredient strings, multi-ingredient products, applications per
     ingredient
  7. optional, with --dc-dir: application-number and ingredient-name overlap with the
     extracted DrugCentral CSVs, counted as distinct values
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.eligibility import MARKET_WINDOW_YEARS_REPORTED  # noqa: E402

DEFAULT_DIR = Path("data/drugsatfda")
DELIMITER = "\t"
ENCODINGS = ("utf-8", "cp1252")          # tried in order; the one that decoded is printed
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y")
DEFAULT_VOCAB_MAX = 40
DEFAULT_SAMPLE_VALUES = 5
INGREDIENT_SEPARATOR = ";"

# From FDA's data definitions; the run confirms or contradicts each.
EXPECTED = {
    "Applications": ("ApplNo", "ApplType", "SponsorName"),
    "Products": ("ApplNo", "ProductNo", "DrugName", "ActiveIngredient"),
    "Submissions": ("ApplNo", "SubmissionClassCodeID", "SubmissionType", "SubmissionNo",
                    "SubmissionStatus", "SubmissionStatusDate", "SubmissionsPublicNotes",
                    "ReviewPriority"),
    "SubmissionClass_Lookup": ("SubmissionClassCodeID", "SubmissionClassCode",
                               "SubmissionClassCodeDescription"),
    "SubmissionPropertyType": ("ApplNo", "SubmissionType", "SubmissionNo",
                               "SubmissionPropertyTypeCode"),
    "ApplicationDocs": ("ApplNo", "SubmissionType", "SubmissionNo", "ApplicationDocsTypeID"),
    "ApplicationsDocsType_Lookup": ("ApplicationDocsType_Lookup_ID",
                                    "ApplicationDocsType_Lookup_Description"),
}
KEYS = {
    "Applications": ("ApplNo",),
    "Products": ("ApplNo", "ProductNo"),
    "Submissions": ("ApplNo", "SubmissionType", "SubmissionNo"),
    "SubmissionClass_Lookup": ("SubmissionClassCodeID",),
}
ACTION_JOIN = "Join_Submission_ActionTypes_Lookup"
ACTION_LOOKUP = "ActionTypes_Lookup"
EXPECTED[ACTION_JOIN] = ("ApplNo", "SubmissionType", "SubmissionNo", "ActionTypes_LookupID")
EXPECTED[ACTION_LOOKUP] = ("ActionTypes_LookupID", "ActionTypes_LookupDescription",
                           "SupplCategoryLevel1Code", "SupplCategoryLevel2Code")
EFFICACY_CLASS = "EFFICACY"
# Drugs@FDA covers products "approved since 1939" (FDA's description). An earlier date is a
# placeholder, counted apart and treated as undated.
EARLIEST_PLAUSIBLE_APPROVAL = date(1939, 1, 1)
NAME_SEPARATORS = (";", "||", ",")
ORIGINAL = "ORIG"
SUPPLEMENT = "SUPPL"
APPROVED = "AP"



def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def _norm_appl(value: str) -> str:
    """ApplNo is char(6); some sources drop leading zeros. Compare as digits, zero-padded."""
    v = (value or "").strip()
    return v.zfill(6) if v.isdigit() else v


def read_table(path: Path) -> dict:
    """-> {header, rows, encoding, malformed}. Tab-delimited, QUOTE_NONE: a field-count
    mismatch is counted and the row set aside, never silently realigned."""
    raw = path.read_bytes()
    text = encoding = None
    for enc in ENCODINGS:
        try:
            text, encoding = raw.decode(enc), enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SystemExit(f"!! {path} decodes as none of {ENCODINGS}")
    reader = csv.reader(text.splitlines(), delimiter=DELIMITER, quoting=csv.QUOTE_NONE)
    header = [h.strip().lstrip("\ufeff") for h in next(reader, [])]
    rows, malformed = [], 0
    for fields in reader:
        if not any(f.strip() for f in fields):
            continue
        if len(fields) != len(header):
            malformed += 1
            continue
        rows.append({h: f.strip() for h, f in zip(header, fields)})
    return {"header": header, "rows": rows, "encoding": encoding, "malformed": malformed}


def parse_date(value: str):
    v = (value or "").strip()
    if not v:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return "UNPARSEABLE"


# ---- 1. files ---------------------------------------------------------------
def section_files(folder: Path) -> tuple:
    print(_rule("1. FILES"))
    tables, problems = {}, []
    found = sorted(folder.glob("*.txt"))
    print(f"  folder {folder}: {len(found)} .txt files")
    for path in found:
        t = read_table(path)
        name = path.stem
        tables[name] = t
        modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        print(f"  {name:36s} rows {len(t['rows']):8d}  malformed {t['malformed']:5d}  "
              f"{t['encoding']:7s} modified {modified}")
        print(f"    {', '.join(t['header'])}")
        if t["malformed"]:
            problems.append(f"{name}: {t['malformed']} malformed rows")
    for name, cols in EXPECTED.items():
        if name not in tables:
            problems.append(f"{name}: file absent")
            print(f"  !! {name}.txt absent")
            continue
        missing = [c for c in cols if c not in tables[name]["header"]]
        if missing:
            problems.append(f"{name}: columns absent {missing}")
            print(f"  !! {name} lacks {missing}")
    return tables, problems


def _have(tables: dict, name: str) -> bool:
    return name in tables and all(c in tables[name]["header"] for c in EXPECTED[name])


# ---- 2. keys ----------------------------------------------------------------
def section_keys(tables: dict, problems: list) -> None:
    print(_rule("2. KEYS AND DUPLICATES"))
    for name, key in KEYS.items():
        if not _have(tables, name):
            continue
        rows = tables[name]["rows"]
        keys = Counter(tuple(r[k] for k in key) for r in rows)
        repeated = sum(1 for n in keys.values() if n > 1)
        exact = len(rows) - len({tuple(sorted(r.items())) for r in rows})
        flag = "  <-- NOT a key" if repeated else ""
        print(f"  {name:24s} key {key}: rows {len(rows)}  keys {len(keys)}  "
              f"repeated keys {repeated}  exact duplicate rows {exact}{flag}")
        if repeated:
            problems.append(f"{name}: {key} is not unique")


# ---- 3. vocabularies ----------------------------------------------------------
def section_vocab(tables: dict, vocab_max: int) -> dict:
    print(_rule("3. APPLICATION AND SUBMISSION VOCABULARY"))
    classes = {}
    if _have(tables, "SubmissionClass_Lookup"):
        for r in tables["SubmissionClass_Lookup"]["rows"]:
            classes[r["SubmissionClassCodeID"]] = (r["SubmissionClassCode"],
                                                   r["SubmissionClassCodeDescription"])
    if _have(tables, "Applications"):
        types = Counter(r["ApplType"] for r in tables["Applications"]["rows"])
        print(f"  ApplType: {dict(types.most_common())}")
    if not _have(tables, "Submissions"):
        return classes
    subs = tables["Submissions"]["rows"]
    print(f"\n  SubmissionType x SubmissionStatus: "
          f"{dict(Counter((r['SubmissionType'], r['SubmissionStatus']) for r in subs).most_common(vocab_max))}")
    orphan = sum(1 for r in subs if r["SubmissionClassCodeID"] and
                 r["SubmissionClassCodeID"] not in classes)
    blank = sum(1 for r in subs if not r["SubmissionClassCodeID"])
    print(f"  class id blank {blank}, absent from the lookup {orphan}")
    table = Counter((r["SubmissionType"], classes.get(r["SubmissionClassCodeID"],
                                                      ("(blank)" if not r["SubmissionClassCodeID"]
                                                       else "(orphan)", ""))) for r in subs
                    if r["SubmissionStatus"] == APPROVED)
    print(f"\n  APPROVED submissions by type and class:")
    print(f"    {'type':7s} {'class code':14s} {'description':50s} {'n':>8s}")
    for (stype, (code, desc)), n in sorted(table.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f"    {stype:7s} {code:14s} {desc[:50]:50s} {n:8d}")
    return classes


# ---- 4. dates ---------------------------------------------------------------
def section_dates(tables: dict) -> None:
    print(_rule("4. APPROVAL DATES"))
    if not _have(tables, "Submissions"):
        return
    subs = [r for r in tables["Submissions"]["rows"] if r["SubmissionStatus"] == APPROVED]
    latest = None
    for stype in (ORIGINAL, SUPPLEMENT):
        dated = Counter()
        values = []
        for r in subs:
            if r["SubmissionType"] != stype:
                continue
            d = parse_date(r["SubmissionStatusDate"])
            if isinstance(d, date) and d < EARLIEST_PLAUSIBLE_APPROVAL:
                dated[f"before {EARLIEST_PLAUSIBLE_APPROVAL}"] += 1
                continue
            dated["undated" if d is None else "unparseable" if d == "UNPARSEABLE" else "dated"] += 1
            if isinstance(d, date):
                values.append(d)
        first, last = (min(values), max(values)) if values else (None, None)
        print(f"  {stype:6s} approved: {sum(dated.values())}  {dict(dated)}  "
              f"earliest {first}  latest {last}")
        if last and (latest is None or last > latest):
            latest = last
    samples = [r["SubmissionStatusDate"] for r in subs[:DEFAULT_SAMPLE_VALUES]]
    print(f"  raw date sample: {samples}")
    if latest is None:
        print("  !! no parseable approval date")
        return
    print(f"\n  latest approval in this source: {latest}")
    print("  A readout's W-year window closes against THIS source only if readout + W")
    print(f"  falls on or before {latest}:")
    for window in sorted(MARKET_WINDOW_YEARS_REPORTED):
        try:
            bound = latest.replace(year=latest.year - window)
        except ValueError:
            bound = latest.replace(month=2, day=28, year=latest.year - window)
        print(f"    W={window}: latest measurable readout {bound}")
    if _have(tables, "Applications"):
        appl_type = {r["ApplNo"]: r["ApplType"] for r in tables["Applications"]["rows"]}
        orig_per_appl = Counter(r["ApplNo"] for r in subs if r["SubmissionType"] == ORIGINAL)
        multi = Counter(appl_type.get(a, "?") for a, n in orig_per_appl.items() if n > 1)
        no_orig = Counter(appl_type[a] for a in set(appl_type) - set(orig_per_appl))
        print(f"\n  applications with >1 approved ORIG, by ApplType: {dict(multi)}")
        print(f"  applications with no approved ORIG, by ApplType: {dict(no_orig)}")


# ---- 5. is a new indication separable? ------------------------------------------
def section_indication(tables: dict, classes: dict, vocab_max: int) -> None:
    print(_rule("5. IS A NEW-INDICATION APPROVAL SEPARABLE?"))
    print("  The files carry no indication field. A supplement can be tied to an")
    print("  indication only through its class, its public notes, or an attached document.")
    if not _have(tables, "Submissions"):
        return
    supp = [r for r in tables["Submissions"]["rows"]
            if r["SubmissionType"] == SUPPLEMENT and r["SubmissionStatus"] == APPROVED]
    by_class = Counter(classes.get(r["SubmissionClassCodeID"], ("?", "?"))[0] for r in supp)
    for code, n in by_class.most_common():
        rows = [r for r in supp if classes.get(r["SubmissionClassCodeID"], ("?",))[0] == code]
        notes = Counter(r["SubmissionsPublicNotes"] for r in rows if r["SubmissionsPublicNotes"])
        print(f"\n  class {code}: {n} approved supplements, public notes filled "
              f"{sum(notes.values())} ({_pct(sum(notes.values()), n)}), "
              f"{len(notes)} distinct notes")
        for note, k in notes.most_common(DEFAULT_SAMPLE_VALUES):
            print(f"    {k:6d}  {note[:100]!r}")
    if _have(tables, "SubmissionPropertyType"):
        props = Counter(r["SubmissionPropertyTypeCode"]
                        for r in tables["SubmissionPropertyType"]["rows"])
        print(f"\n  SubmissionPropertyTypeCode: {dict(props.most_common(vocab_max))}")
    if _have(tables, "ApplicationDocs"):
        lookup = {}
        if _have(tables, "ApplicationsDocsType_Lookup"):
            lookup = {r["ApplicationDocsType_Lookup_ID"]: r["ApplicationDocsType_Lookup_Description"]
                      for r in tables["ApplicationsDocsType_Lookup"]["rows"]}
        keyed = {(r["ApplNo"], r["SubmissionType"], r["SubmissionNo"]) for r in supp}
        docs = [r for r in tables["ApplicationDocs"]["rows"]
                if (r["ApplNo"], r["SubmissionType"], r["SubmissionNo"]) in keyed]
        with_doc = len({(r["ApplNo"], r["SubmissionType"], r["SubmissionNo"]) for r in docs})
        print(f"\n  approved supplements with any attached document: {with_doc} of "
              f"{len(supp)} ({_pct(with_doc, len(supp))})")
        print(f"  document types: "
              f"{dict(Counter(lookup.get(r['ApplicationDocsTypeID'], r['ApplicationDocsTypeID']) for r in docs).most_common(vocab_max))}")


# ---- 5b. supplement categories ---------------------------------------------------
def section_action_types(tables: dict, classes: dict, vocab_max: int) -> None:
    print(_rule("5b. SUPPLEMENT CATEGORIES (action types) -- the indication route"))
    if not (_have(tables, ACTION_JOIN) and _have(tables, ACTION_LOOKUP)
            and _have(tables, "Submissions")):
        print("  !! action-type tables absent -- skipped")
        return
    lookup = {r["ActionTypes_LookupID"]: (r["SupplCategoryLevel1Code"],
                                          r["SupplCategoryLevel2Code"],
                                          r["ActionTypes_LookupDescription"])
              for r in tables[ACTION_LOOKUP]["rows"]}
    print(f"  {ACTION_LOOKUP}: {len(lookup)} action types")
    print(f"    {'id':>4s} {'level 1':18s} {'level 2':28s} description")
    for aid, (l1, l2, desc) in sorted(lookup.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        print(f"    {aid:>4s} {l1[:18]:18s} {l2[:28]:28s} {desc[:60]}")
    per_sub: dict = {}
    orphan = 0
    for r in tables[ACTION_JOIN]["rows"]:
        aid = r["ActionTypes_LookupID"]
        if aid not in lookup:
            orphan += 1
        per_sub.setdefault((r["ApplNo"], r["SubmissionType"], r["SubmissionNo"]),
                           set()).add(aid)
    print(f"\n  join rows {len(tables[ACTION_JOIN]['rows'])}, submissions keyed "
          f"{len(per_sub)}, action ids absent from the lookup {orphan}")
    counts = Counter(len(v) for v in per_sub.values())
    print(f"  action types per submission: {dict(sorted(counts.items()))}")
    supp = [r for r in tables["Submissions"]["rows"]
            if r["SubmissionType"] == SUPPLEMENT and r["SubmissionStatus"] == APPROVED]
    keyed = [(r, per_sub.get((r["ApplNo"], r["SubmissionType"], r["SubmissionNo"]), set()))
             for r in supp]
    print(f"  approved supplements with >=1 action type: "
          f"{sum(1 for _, v in keyed if v)} of {len(supp)}")
    for scope, rows in (("ALL approved supplements", keyed),
                        (f"class {EFFICACY_CLASS} only",
                         [(r, v) for r, v in keyed
                          if classes.get(r["SubmissionClassCodeID"], ("",))[0]
                          == EFFICACY_CLASS])):
        tally = Counter()
        for _, v in rows:
            for aid in v or {"(none)"}:
                l1, l2, _ = lookup.get(aid, ("(none)" if aid == "(none)" else "(orphan)",
                                             "", ""))
                tally[(l1, l2)] += 1
        print(f"\n  {scope} ({len(rows)}), by category (a submission with two action "
              f"types counts twice):")
        for (l1, l2), n in tally.most_common(vocab_max):
            print(f"    {l1[:24]:24s} {l2[:34]:34s} {n:8d}")
    with_dates = [parse_date(r["SubmissionStatusDate"]) for r, v in keyed if v]
    with_dates = [d for d in with_dates if isinstance(d, date)]
    if with_dates:
        print(f"\n  supplements carrying an action type are dated "
              f"{min(with_dates)} .. {max(with_dates)}")
    print("  If a level-2 category names a new indication, that supplement is DATED but")
    print("  still does not say WHICH indication; the letter or label does.")


# ---- 6. name key --------------------------------------------------------------
def section_ingredients(tables: dict) -> set:
    print(_rule("6. ACTIVE-INGREDIENT KEY"))
    if not _have(tables, "Products"):
        return set()
    rows = tables["Products"]["rows"]
    strings = Counter(r["ActiveIngredient"].strip().lower() for r in rows if r["ActiveIngredient"])
    combos = sum(1 for s in strings if INGREDIENT_SEPARATOR in s)
    by_sep = {sep: sum(1 for s in strings if sep in s) for sep in NAME_SEPARATORS}
    print(f"  ActiveIngredient strings containing each separator: {by_sep}")
    single = {part.strip() for s in strings for part in s.split(INGREDIENT_SEPARATOR) if part.strip()}
    appls = {}
    for r in rows:
        for part in r["ActiveIngredient"].lower().split(INGREDIENT_SEPARATOR):
            if part.strip():
                appls.setdefault(part.strip(), set()).add(r["ApplNo"])
    per = sorted(len(v) for v in appls.values())
    print(f"  distinct ActiveIngredient strings {len(strings)}, of which multi-ingredient "
          f"('{INGREDIENT_SEPARATOR}') {combos}")
    print(f"  distinct single ingredients {len(single)}; applications per ingredient "
          f"p50 {per[len(per) // 2] if per else None} max {per[-1] if per else None}")
    print(f"  sample: {sorted(single)[:DEFAULT_SAMPLE_VALUES]}")
    print("  An ingredient carried by many applications is mostly generics (ANDA): the")
    print("  innovator's first approval is the one the market label needs.")
    return single


# ---- 7. DrugCentral overlap --------------------------------------------------------
def _dc_column(folder: Path, table: str, column: str) -> set:
    path = folder / f"dc_{table}.csv"
    if not path.exists():
        raise SystemExit(f"!! {path} not found. Run scripts\\extract_drugcentral.py.")
    with path.open(newline="", encoding="utf-8") as handle:
        return {row[column] for row in csv.DictReader(handle) if row[column]}


def section_drugcentral(tables: dict, ingredients: set, args) -> None:
    print(_rule("7. OVERLAP WITH DRUGCENTRAL (distinct values, no joined rows)"))
    if args.dc_dir is None:
        print("  (no --dc-dir; skipped)")
        return
    dc_appl = {_norm_appl(v) for v in _dc_column(args.dc_dir, "ob_product", "appl_no")}
    dc_names = _dc_column(args.dc_dir, "synonyms", "lname")
    rows = tables.get("Applications", {}).get("rows", [])
    fda_appl = {_norm_appl(r["ApplNo"]) for r in rows}
    by_type = Counter(r["ApplType"] for r in rows if _norm_appl(r["ApplNo"]) in dc_appl)
    both = fda_appl & dc_appl
    print(f"  ApplNo: Drugs@FDA {len(fda_appl)}, DrugCentral ob_product {len(dc_appl)}, "
          f"both {len(both)}, DrugCentral-only {len(dc_appl - fda_appl)}")
    print(f"  Drugs@FDA applications found in ob_product, by ApplType: {dict(by_type)}")
    matched = ingredients & dc_names
    print(f"  single ingredients matching a DrugCentral synonym exactly: {len(matched)} of "
          f"{len(ingredients)} ({_pct(len(matched), len(ingredients))})")
    print("  The Orange Book holds no BLAs, so biologics need the name route or another key.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                    help="folder holding the unzipped drugsatfda.zip. Default: %(default)s")
    ap.add_argument("--vocab-max", type=int, default=DEFAULT_VOCAB_MAX)
    ap.add_argument("--dc-dir", type=Path, default=None,
                    help="data/drugcentral, as written by scripts/extract_drugcentral.py")
    args = ap.parse_args()
    print(f"dir {args.dir} | encodings {ENCODINGS} | date formats {DATE_FORMATS} | "
          f"windows {sorted(MARKET_WINDOW_YEARS_REPORTED)} | DrugCentral "
          f"{args.dc_dir or 'not read'}")
    if not args.dir.is_dir():
        print(f"!! {args.dir} is not a folder. Download drugsatfda.zip and unzip it there.")
        return 2
    tables, problems = section_files(args.dir)
    section_keys(tables, problems)
    classes = section_vocab(tables, args.vocab_max)
    section_dates(tables)
    section_indication(tables, classes, args.vocab_max)
    section_action_types(tables, classes, args.vocab_max)
    ingredients = section_ingredients(tables)
    section_drugcentral(tables, ingredients, args)
    print(_rule("SUMMARY"))
    for p in problems:
        print(f"  !! {p}")
    print(f"  declared expectations contradicted: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
