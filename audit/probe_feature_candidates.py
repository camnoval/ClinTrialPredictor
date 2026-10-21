#!/usr/bin/env python3
"""Candidate-feature probe (D-60, D-62), before the feature builder. Reads only. Scratch.

Coverage and data quality only. It deliberately prints NO association with any label: the
features are chosen on coverage and reasoning, not by looking at outcomes first.

  1. ENTITIES (D-62's toggle): lead sponsor, collaborators, overall officials and their
     affiliations, sites (name + city + country), site investigators. Per kind: rows, trials
     covered, raw and normalised distinct names, the top names (so placeholders like
     'Medical Director' show), and per target and readout band the share of labelled trials
     whose entity had at least one trial registered BEFORE this one, with the median count.
     That is the registry-start truncation of D-57, measured for experience features.
  2. DISEASE CATEGORIES: each mesh-list condition term mapped through rows__mesh_terms tree
     numbers to its top-level C heading (C04 Neoplasms, ...). Coverage per target and the
     category mix of the endpoint-met population.
  3. ELIGIBILITY TEXT (trial_registration_text.csv): non-blank share, median length, median
     list items, and the share naming a patient-selection biomarker (BIOMARKER_WORDS).

Usage (PowerShell), one line:
  python audit\\probe_feature_candidates.py | Out-File -Encoding utf8 probe_feature_candidates_20261001.txt
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_posting_bias import read_csv  # noqa: E402
from build_targets import (  # noqa: E402
    D40_WINDOW as MARKET_HEADLINE_W, ERA_BAND_YEARS, MESH_LIST_TYPE, OUT_TARGETS,
    advancement_columns, market_columns,
)
from pull_aact_fields import OUT_FIELDS, OUT_TEXT  # noqa: E402
from trial_pos.services.aact_fields import output_name  # noqa: E402
from trial_pos.services.aact_rows import file_name  # noqa: E402
from trial_pos.services.advancement_label import DISEASE_EXACT  # noqa: E402
from trial_pos.services.eligibility import (  # noqa: E402
    DEFAULT_ADVANCEMENT_WINDOW_YEARS as ADV_W, REGISTRATION_SUBMITTED_FIELD,
)
from trial_pos.services.population import parse_date  # noqa: E402

csv.field_size_limit(10 ** 9)

TARGETS = (("endpoint_met", "endpoint_met_label"),
           (f"advancement_w{ADV_W}_{DISEASE_EXACT}", advancement_columns(ADV_W, DISEASE_EXACT)[2]),
           (f"market_w{MARKET_HEADLINE_W}", market_columns(MARKET_HEADLINE_W)[2]))
NO_BAND = "none"
SHOW_TOP = 15
LEAD = "lead"
# Trailing words that differ between spellings of one organisation ('Pfizer' / 'Pfizer Inc.').
# Removed from the END of a name only, repeatedly. A scratch normalisation: the probe's top
# lists show whether it is enough.
CORPORATE_SUFFIXES = frozenset({
    "inc", "incorporated", "ltd", "limited", "llc", "lp", "corp", "corporation", "co",
    "company", "gmbh", "ag", "sa", "nv", "bv", "plc", "spa", "srl", "kk", "ab", "as", "oy",
})
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Patient-selection biomarkers named in eligibility text (BIO 2016 found higher success with
# selection biomarkers). Whole words, case-insensitive; a crude first reading.
# CORRECTED 2026-10-19: the first run had no boundary after a term, so 'alk' matched
# 'alkaline phosphatase' and 'ret' 'retinopathy'; its biomarker shares (26.3%, 28.0%, 19.4%)
# are invalid. The builder's pattern (trial_features.BIOMARKER_TERMS) is the one to use.
from trial_pos.services.trial_features import BIOMARKER_TERMS as BIOMARKER_WORDS  # noqa: E402
from trial_pos.services.trial_features import _BIOMARKER  # noqa: E402
_LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022\u00b7]|\d{1,3}[.)])\s+")
MESH_C_BRANCH = "C"            # the Diseases branch of the MeSH tree
TOP_CODE_LENGTH = 3            # 'C04': a branch letter and two digits


def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def band_of(d) -> str:
    if d is None:
        return NO_BAND
    b = d.year - d.year % ERA_BAND_YEARS
    return f"{b}-{b + ERA_BAND_YEARS - 1}"


def entity_key(name) -> str:
    """Casefold, NFKC, punctuation to spaces, trailing corporate suffixes removed."""
    words = _NON_ALNUM.sub(" ", unicodedata.normalize("NFKC", str(name or "")).casefold()).split()
    while len(words) > 1 and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    args = ap.parse_args()
    a = args.aact_dir
    paths = {"targets": args.labels_dir / OUT_TARGETS, "fields": a / OUT_FIELDS,
             "text": a / OUT_TEXT, "conditions": a / file_name("browse_conditions"),
             "mesh_terms": a / file_name("mesh_terms"), "sponsors": a / file_name("sponsors"),
             "officials": a / file_name("overall_officials"),
             "facilities": a / file_name("facilities"),
             "investigators": a / file_name("facility_investigators")}
    for p in paths.values():
        if not p.exists():
            raise SystemExit(f"!! {p} not found. Nothing printed.")

    readout, label = {}, {t: set() for t, _c in TARGETS}
    for r in read_csv(paths["targets"]):
        readout[r["nct_id"]] = parse_date(r["readout_date"])
        for t, col in TARGETS:
            if r[col] != "":
                label[t].add(r["nct_id"])
    submitted_col = output_name("studies", REGISTRATION_SUBMITTED_FIELD)
    submitted = {r["nct_id"]: parse_date(r[submitted_col]) for r in read_csv(paths["fields"])}
    _rule("SETTINGS")
    print(f"  targets {[t for t, _c in TARGETS]} | bands {ERA_BAND_YEARS}y | top {SHOW_TOP} | "
          f"suffixes {len(CORPORATE_SUFFIXES)} | biomarker words {len(BIOMARKER_WORDS)}")
    print(f"  trials with a registration date {sum(d is not None for d in submitted.values())} "
          "of " + str(len(submitted)) + "; labelled: "
          + ", ".join(f"{t} {len(v)}" for t, v in label.items()))

    # ---- 1. entities ---------------------------------------------------------------------
    _rule("1. ENTITIES: coverage, spellings, and prior registered trials (D-62 toggle)")
    kinds = defaultdict(lambda: defaultdict(set))          # kind -> key -> ncts
    raw = defaultdict(lambda: defaultdict(set))            # kind -> key -> raw spellings
    rows_n = Counter()
    roles = Counter()

    def add(kind, nct, name):
        k = entity_key(name)
        rows_n[kind] += 1
        if k:
            kinds[kind][k].add(nct)
            raw[kind][k].add(str(name).strip())

    for r in read_csv(paths["sponsors"]):
        add("lead sponsor" if (r.get("lead_or_collaborator") or "").lower() == LEAD
            else "collaborator", r["nct_id"], r.get("name"))
    for r in read_csv(paths["officials"]):
        add("overall official", r["nct_id"], r.get("name"))
        add("official affiliation", r["nct_id"], r.get("affiliation"))
        roles[(r.get("role") or "").strip() or "(blank)"] += 1
    for r in read_csv(paths["facilities"]):
        add("site (name+city+country)", r["nct_id"],
            " | ".join((r.get("name") or "", r.get("city") or "", r.get("country") or ""))
            if (r.get("name") or "").strip() else "")
    for r in read_csv(paths["investigators"]):
        add("site investigator", r["nct_id"], r.get("name"))
    print(f"  overall official roles: {dict(sorted(roles.items(), key=lambda kv: -kv[1]))}")

    for kind in sorted(kinds):
        keys = kinds[kind]
        by_trial = defaultdict(set)
        for k, ncts in keys.items():
            for n in ncts:
                by_trial[n].add(k)
        dates = {k: sorted(d for d in (submitted.get(n) for n in ncts) if d is not None)
                 for k, ncts in keys.items()}
        n_raw = sum(len(v) for v in raw[kind].values())
        print(f"\n  {kind}: rows {rows_n[kind]}; trials {len(by_trial)}; raw spellings {n_raw}; "
              f"normalised keys {len(keys)}")
        top = sorted(keys, key=lambda k: (-len(keys[k]), k))[:SHOW_TOP]
        for k in top:
            print(f"    {len(keys[k]):7d} trials  {len(raw[kind][k]):4d} spellings  {k[:60]}")
        for t in label:
            present, by = 0, defaultdict(list)
            for n in sorted(label[t]):
                ks, s = by_trial.get(n), submitted.get(n)
                if not ks:
                    continue
                present += 1
                if s is None:
                    continue
                prior = max(bisect_left(dates[k], s) for k in ks)
                by[band_of(readout.get(n))].append(prior)
            print(f"    {t}: entity present {_pct(present, len(label[t]))}; by band, share "
                  "with a prior registered trial | median prior:")
            for b in sorted(by):
                v = by[b]
                print(f"      {b:10s} n {len(v):6d}  {_pct(sum(x > 0 for x in v), len(v)):>7s}"
                      f"  median {statistics.median(v):8.1f}")

    # ---- 2. disease categories ----------------------------------------------------------------
    _rule("2. DISEASE CATEGORIES: mesh-list condition terms -> top-level MeSH C headings")
    tops, top_name = defaultdict(set), {}
    for r in read_csv(paths["mesh_terms"]):
        tn = (r.get("tree_number") or "").strip()
        if not tn.startswith(MESH_C_BRANCH):
            continue
        code = tn[:TOP_CODE_LENGTH]
        tops[r["downcase_mesh_term"]].add(code)
        if tn == code:
            top_name[code] = r["mesh_term"]
    cats = defaultdict(set)
    for r in read_csv(paths["conditions"]):
        if r.get("mesh_type") == MESH_LIST_TYPE:
            cats[r["nct_id"]] |= tops.get(r["downcase_mesh_term"], set())
    print(f"  mesh terms with a C tree number {len(tops)}; top headings named {len(top_name)}")
    for t in label:
        n = len(label[t])
        has = [len(cats.get(x, ())) for x in label[t]]
        print(f"  {t}: with a category {_pct(sum(h > 0 for h in has), n)}; with 2 or more "
              f"{_pct(sum(h > 1 for h in has), n)}")
    mix = Counter(c for x in label[TARGETS[0][0]] for c in cats.get(x, ()))
    print(f"  {TARGETS[0][0]} category mix (trials; a trial can carry several):")
    for c, k in sorted(mix.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {c} {top_name.get(c, '?')[:44]:44s} {k:6d}  {_pct(k, len(label[TARGETS[0][0]]))}")

    # ---- 3. eligibility text -------------------------------------------------------------------
    _rule("3. ELIGIBILITY TEXT: length, list items, a named selection biomarker")
    crit_col = output_name("eligibilities", "criteria")
    stats = {t: {"n": 0, "chars": [], "items": [], "bio": 0} for t in label}
    for r in read_csv(paths["text"]):
        n = r["nct_id"]
        member = [t for t in label if n in label[t]]
        text = (r.get(crit_col) or "").strip()
        if not member or not text:
            continue
        items = sum(bool(_LIST_ITEM.match(line)) for line in text.splitlines())
        bio = bool(_BIOMARKER.search(text))
        for t in member:
            s = stats[t]
            s["n"] += 1
            s["chars"].append(len(text))
            s["items"].append(items)
            s["bio"] += bio
    for t, s in stats.items():
        if not s["n"]:
            print(f"  {t}: no criteria text")
            continue
        print(f"  {t}: non-blank {_pct(s['n'], len(label[t]))}; median chars "
              f"{statistics.median(s['chars']):.0f}; median list items "
              f"{statistics.median(s['items']):.0f}; zero items "
              f"{_pct(sum(i == 0 for i in s['items']), s['n'])}; names a biomarker "
              f"{_pct(s['bio'], s['n'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
