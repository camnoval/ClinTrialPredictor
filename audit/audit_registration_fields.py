#!/usr/bin/env python3
"""Post-pull audit of trial_registration_fields.csv against trial_labels.csv. READ-ONLY.

SCRATCH. Verdicts come from services (aact_fields, population, sampling); this file counts.

  1. frame: one row per trial, and how much of the label file it covers
  2. RETROSPECTIVE REGISTRATION: trials first submitted after their primary completion,
     whose "registration" fields were written knowing the outcome. Overall, among drug
     trials, among headline labels and their negatives, by tier, phase and registry era
  3. sponsor class, PAIRED: the label file's restored lead class against a fresh pull of
     the same table (a check on the section 0.3 restore), and studies.source_class
     against the sponsors table (two AACT fields that should agree)
  4. facility counts: AACT's row-based has_single_facility and number_of_facilities
     against distinct sites
  5. duplicates: lead-sponsor rows not exactly one; interventions with copied rows
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_fields import (  # noqa: E402
    registered_after_primary_completion, registration_lag_days,
)
from trial_pos.services.endpoint_label import HEADLINE_TIERS  # noqa: E402
from trial_pos.services.population import (  # noqa: E402
    FDAAA_ENACTED, FINAL_RULE_EFFECTIVE, normalize_agency_class, parse_date,
)
from trial_pos.services.sampling import phase_group  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))

# ICMJE required registration before enrolment for trials starting on or after this date,
# which is when retrospective registration of ongoing trials peaked.
ICMJE_POLICY = date(2005, 7, 1)
ERA_BREAKS = ((ICMJE_POLICY, "before ICMJE 2005"), (FDAAA_ENACTED, "ICMJE to FDAAA"),
              (FINAL_RULE_EFFECTIVE, "FDAAA to Final Rule"))
ERA_LAST = "Final Rule onward"
ERA_UNKNOWN = "submitted date unknown"
LAG_QUANTILES = (0.25, 0.5, 0.75, 0.9)
DAYS_PER_YEAR = 365
TOP_DISAGREEMENTS = 12
DRUG_TRUE = "True"
STRICT_NEGATIVE = "0"

F = {  # fields-file columns this audit reads
    "submitted": "studies__study_first_submitted_date",
    "pcd": "studies__primary_completion_date",
    "pcd_type": "studies__primary_completion_date_type",
    "source_class": "studies__source_class",
    "lead_class": "sponsors__lead_agency_class",
    "lead_rows": "sponsors__n_lead_rows",
    "single": "calculated_values__has_single_facility",
    "n_fac_aact": "calculated_values__number_of_facilities",
    "fac_rows": "facilities__n_rows",
    "fac_distinct": "facilities__n_distinct",
    "iv_rows": "interventions__n_rows",
    "iv_distinct": "interventions__n_distinct",
}
L = ("nct_id", "is_drug_trial", "tier_min", "endpoint_met_strict", "phase",
     "lead_sponsor_class")


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def _int(raw):
    return int(raw) if raw not in ("", None) else None


def _read(path: Path, columns) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in columns if c not in reader.fieldnames]
        if missing:
            raise SystemExit(f"!! {path} lacks {missing}. Nothing computed.")
        out, dups = {}, 0
        for row in reader:
            nct = row["nct_id"].strip().upper()
            dups += nct in out
            out[nct] = {c: row[c] for c in columns}
    return out, dups


def era_of(submitted) -> str:
    d = parse_date(submitted)
    if d is None:
        return ERA_UNKNOWN
    for cut, name in ERA_BREAKS:
        if d < cut:
            return name
    return ERA_LAST


def _tri_row(label: str, values) -> None:
    c = Counter(values)
    n = sum(c.values())
    print(f"  {label:34s} n {n:7d}  after {c[True]:7d} ({_pct(c[True], n):>6s})  "
          f"before {c[False]:7d}  unknown {c[None]:6d}")


def _quantiles(values, qs) -> str:
    if not values:
        return "n/a"
    s = sorted(values)
    return "  ".join(f"p{int(q * 100)}={s[min(len(s) - 1, int(q * len(s)))]}" for q in qs)


def _paired(title: str, pairs) -> None:
    c = Counter(pairs)
    both = sum(n for (a, b), n in c.items() if a is not None and b is not None)
    agree = sum(n for (a, b), n in c.items() if a is not None and a == b)
    print(f"\n  {title}")
    print(f"    both present {both}   agree {agree} ({_pct(agree, both)})   "
          f"one side absent {sum(c.values()) - both}")
    worst = [(k, n) for k, n in c.most_common() if k[0] != k[1]][:TOP_DISAGREEMENTS]
    for (a, b), n in worst:
        print(f"    {str(a):14s} vs {str(b):14s} {n:7d}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fields", type=Path,
                    default=Path("data") / "aact" / "trial_registration_fields.csv")
    ap.add_argument("--labels", type=Path,
                    default=Path("data") / "aact" / "trial_labels.csv")
    args = ap.parse_args()
    print(f"fields {args.fields} | labels {args.labels} | headline tiers {HEADLINE_TIERS} "
          f"| ICMJE cut {ICMJE_POLICY}")
    fields, f_dups = _read(args.fields, ["nct_id"] + list(F.values()))
    labels, _ = _read(args.labels, L)
    headline = set(HEADLINE_TIERS)

    print(_rule("1. FRAME"))
    drug = [n for n, r in labels.items() if r["is_drug_trial"] == DRUG_TRUE]
    print(f"  fields rows {len(fields)}   duplicate nct_id (must be 0): {f_dups}")
    print(f"  label rows {len(labels)}   drug {len(drug)}   "
          f"drug found in fields {sum(n in fields for n in drug)}")

    def retro(nct):
        r = fields.get(nct)
        if r is None:
            return None
        return registered_after_primary_completion(r[F["submitted"]], r[F["pcd"]],
                                                   r[F["pcd_type"]])

    print(_rule("2. RETROSPECTIVE REGISTRATION (first submitted after primary completion)"))
    labelled = [n for n in drug if labels[n]["tier_min"] in headline and n in fields]
    negatives = [n for n in labelled if labels[n]["endpoint_met_strict"] == STRICT_NEGATIVE]
    _tri_row("all pulled", (retro(n) for n in fields))
    _tri_row("drug trials", (retro(n) for n in drug if n in fields))
    _tri_row("drug, headline-labelled", (retro(n) for n in labelled))
    _tri_row("  of which strict negatives", (retro(n) for n in negatives))
    print("\n  headline-labelled by tier")
    for tier in HEADLINE_TIERS:
        _tri_row(f"    {tier}", (retro(n) for n in labelled if labels[n]["tier_min"] == tier))
    print("\n  headline-labelled by phase group")
    groups = Counter(phase_group(labels[n]["phase"]) for n in labelled)
    for g, _n in groups.most_common():
        _tri_row(f"    {g}", (retro(n) for n in labelled
                               if phase_group(labels[n]["phase"]) == g))
    print("\n  headline-labelled by registry era of first submission")
    for era in [name for _c, name in ERA_BREAKS] + [ERA_LAST, ERA_UNKNOWN]:
        rows = [n for n in labelled if era_of(fields[n][F["submitted"]]) == era]
        if rows:
            _tri_row(f"    {era}", (retro(n) for n in rows))
    lags = [registration_lag_days(fields[n][F["submitted"]], fields[n][F["pcd"]])
            for n in labelled if retro(n) is True]
    print(f"\n  lag in days, headline-labelled retrospective: {_quantiles(lags, LAG_QUANTILES)}")
    print(f"  of those, more than a year late: "
          f"{sum(1 for d in lags if d > DAYS_PER_YEAR)} ({_pct(sum(1 for d in lags if d > DAYS_PER_YEAR), len(lags))})")

    print(_rule("3. SPONSOR CLASS, PAIRED"))

    def norm(raw):
        return normalize_agency_class(raw)
    _paired("label file lead_sponsor_class  vs  fresh sponsors lead class (drug trials)",
            ((norm(labels[n]["lead_sponsor_class"]), norm(fields[n][F["lead_class"]]))
             for n in drug if n in fields))
    _paired("studies.source_class  vs  sponsors lead class (all pulled)",
            ((norm(r[F["source_class"]]), norm(r[F["lead_class"]])) for r in fields.values()))

    print(_rule("4. FACILITY COUNTS: AACT row-based vs distinct sites"))
    single_pairs = Counter()
    n_mismatch = rows_vs_aact = 0
    with_sites = 0
    for r in fields.values():
        distinct = _int(r[F["fac_distinct"]])
        if distinct is None:
            continue
        with_sites += 1
        aact_single = {"True": True, "False": False}.get(r[F["single"]])
        single_pairs[(aact_single, distinct == 1)] += 1
        n_mismatch += _int(r[F["n_fac_aact"]]) != distinct
        rows_vs_aact += _int(r[F["n_fac_aact"]]) != _int(r[F["fac_rows"]])
    print(f"  trials with any facility row: {with_sites}")
    print("  AACT has_single_facility  x  distinct sites == 1")
    for (a, b), n in sorted(single_pairs.items(), key=str):
        print(f"    AACT {str(a):6s} distinct-single {str(b):6s} {n:8d}")
    print(f"  number_of_facilities != distinct sites : {n_mismatch} ({_pct(n_mismatch, with_sites)})")
    print(f"  number_of_facilities != raw rows       : {rows_vs_aact} "
          f"(0 means AACT counts rows, duplicates included)")

    print(_rule("5. DUPLICATES"))
    lead = Counter(r[F["lead_rows"]] for r in fields.values())
    print(f"  lead-sponsor rows per trial: {dict(lead.most_common())}")
    iv = [r for r in fields.values() if r[F["iv_rows"]] not in ("", None)]
    copied = sum(1 for r in iv if _int(r[F["iv_rows"]]) != _int(r[F["iv_distinct"]]))
    print(f"  interventions: trials with rows beyond distinct (type, name) {copied} of "
          f"{len(iv)} ({_pct(copied, len(iv))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())