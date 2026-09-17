#!/usr/bin/env python3
"""Step 1 of the per-trial predictor: mine AACT for a real endpoint-met label.

This gates the whole project. Everything built so far predicts ChEMBL "drug approved",
which is not "this trial met its primary endpoint"; that mismatch is why the existing
artifact is a prior rather than a trial predictor. This script pulls the trial's own posted
primary-outcome analyses and derives the label from them.

All derivation logic lives in the pure, tested engine `trial_pos.services.endpoint_label`
(see tests/test_endpoint_label.py). This file is I/O and audit only -- deliberately, because
pull_aact.py put its derivations inline and they are consequently untested.

=============================================================================
THE FOUR TIERS -- printed on every run, and carried per row in the output
=============================================================================
  A  superiority analysis, p-value present            -> headline
  B  non-inferiority/equivalence, p-value present     -> headline
  C  no p-value, CI excludes null                     -> NOT headline, sensitivity only
  D  no decidable analysis (single-arm/descriptive)   -> endpoint_met UNKNOWN
`tier_min` is the weakest tier a trial's label rests on and is the correct headline filter.
Never report a pooled number without saying which tiers were pooled. Full definitions and
the direction-of-effect limitation are in the engine docstring and docs/EndpointLabelSpec.md.

=============================================================================
TWO LABEL VARIANTS
=============================================================================
  endpoint_met_strict  posted analyses only. Headline.
  endpoint_met_broad   adds terminated-for-futility as 0, stopped-for-efficacy as 1.
                       Sensitivity analysis. Its extra rows come from `why_stopped`, so
                       `why_stopped`/`overall_status`/`termination_score`/`safety_termination`
                       are LABEL INPUTS and must never be features -- for either variant, so
                       the two stay comparable. See endpoint_label.LABEL_DERIVED_FIELDS.

=============================================================================
WHAT THIS SCRIPT DOES *NOT* DO
=============================================================================
It reports posting coverage and a bias PREVIEW within the pulled cohort. It cannot do the
real Step-2 posting-bias audit, which requires comparing posters against non-posters across
the full AACT population rather than inside a cohort that is already selected. That is the
next script, and no modelling should start before it runs.

Access: free AACT account -> env AACT_USER / AACT_PASSWORD (or --user/--password).
        Host aact-db.ctti-clinicaltrials.org:5432, db 'aact', schema 'ctgov'.
        This is NOT the ClinicalTrials.gov API key; the v2 API has no results analyses.
Deps: psycopg2-binary, pandas.
Usage:
  python scripts\\pull_aact_results.py --probe-only          # schema check, no pull
  python scripts\\pull_aact_results.py
  python scripts\\pull_aact_results.py --ids NCT00000102 NCT01234567
  python scripts\\pull_aact_results.py --alpha 0.025 --broad-includes-safety
  python scripts\\pull_aact_results.py --from-raw data\\aact\\results_raw  # offline re-derive
"""
from __future__ import annotations

import argparse
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from trial_pos.services.endpoint_label import (
    DEFAULT_ALPHA, HEADLINE_TIERS, LABEL_DERIVED_FIELDS, TIER_DOC, TIER_ORDER, label_row,
)

# Columns we want. The probe intersects these with what the server actually has, so a
# schema change costs a warning line rather than a failed pull.
STUDIES_WANT = [
    "nct_id", "study_type", "phase", "overall_status", "why_stopped", "enrollment",
    "enrollment_type", "number_of_arms", "number_of_groups", "start_date",
    "primary_completion_date", "completion_date", "results_first_submitted_date",
    "results_first_posted_date", "last_update_posted_date",
]
STUDIES_NEED = ["nct_id", "overall_status", "why_stopped"]

OUTCOMES_WANT = ["id", "nct_id", "outcome_type", "title", "time_frame", "population"]
OUTCOMES_NEED = ["id", "nct_id", "outcome_type"]

ANALYSES_WANT = [
    "id", "nct_id", "outcome_id", "non_inferiority_type", "non_inferiority_description",
    "param_type", "param_value", "p_value", "p_value_modifier", "p_value_description",
    "ci_lower_limit", "ci_upper_limit", "ci_percent", "method", "groups_desc",
]
ANALYSES_NEED = ["outcome_id", "p_value"]

# Start-year buckets for the posting-rate preview. The boundaries are regulatory, not
# arbitrary: FDAAA 801 results-reporting took effect in 2008, and the Final Rule plus NIH
# policy tightened enforcement from January 2017. 2100 is an open upper bound.
FDAAA_YEAR_BUCKETS = ((1990, 2007), (2008, 2013), (2014, 2017), (2018, 2100))

OUT_COLS = [
    "nct_id", "phase", "study_type", "overall_status", "why_stopped_class",
    "results_posted", "results_first_posted_date", "start_date", "completion_date",
    "primary_completion_date", "enrollment", "number_of_arms",
    "n_primary_outcomes", "n_primary_analyzed", "n_primary_met", "frac_primary_met",
    "any_primary_met", "all_primary_met", "n_analyses_total",
    "tier_min", "tier_max", "tier_mix",
    "endpoint_met_strict", "endpoint_met_broad",
    "label_source_strict", "label_source_broad", "label_rule", "alpha_used",
]


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def _scalar(v):
    """None for anything blank-ish. pandas turns empty CSV cells into float NaN, which is
    TRUTHY -- so a naive `or` chain reads a missing posting date as a posted result."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:            # NaN
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "na", "nat"):
        return None
    return v


def _rule(title):
    return f"\n{'=' * 74}\n{title}\n{'=' * 74}"


def print_tier_legend():
    print(_rule("TIER LEGEND (the label is only as good as its tier)"))
    for t in TIER_ORDER:
        headline = "HEADLINE" if t in HEADLINE_TIERS else "not headline"
        print(f"  {t:22s} [{headline:12s}] {TIER_DOC[t]}")
    print("  tier_min = weakest tier the trial's label rests on -> filter headline runs on it.")


# ---- id collection --------------------------------------------------------
def collect_nctids(units_path: Path, cto_path: Path, explicit: list[str] | None) -> list[str]:
    import pandas as pd
    if explicit:
        return sorted({s.strip().upper() for s in explicit if s.strip()})
    ids: set[str] = set()
    for path, col in ((units_path, "nctid"), (cto_path, "nctid")):
        if path and path.exists():
            df = pd.read_csv(path)
            if col in df.columns:
                ids |= {str(x).strip().upper() for x in df[col].dropna()}
                print(f"  ids from {path}: {df[col].nunique()} distinct")
            else:
                print(f"  !! {path} has no '{col}' column (found: {list(df.columns)[:8]}...)")
        else:
            print(f"  (absent, skipped) {path}")
    return sorted(ids)


# ---- schema probe ---------------------------------------------------------
def probe_schema(conn, schema: str) -> dict[str, set[str]]:
    """{table: set(columns)} for the tables we touch. Empty set = table not visible."""
    tables = ["studies", "outcomes", "outcome_analyses", "result_groups"]
    found: dict[str, set[str]] = {t: set() for t in tables}
    with conn.cursor() as c:
        c.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %(schema)s AND table_name = ANY(%(tabs)s)",
            {"schema": schema, "tabs": tables},
        )
        for table, col in c.fetchall():
            found[table].add(col)
    return found


def resolve_columns(found: set[str], want: list[str], need: list[str],
                    table: str) -> tuple[list[str], list[str]]:
    """(usable columns, missing required) -- reported, never silently dropped."""
    have = [c for c in want if c in found]
    absent = [c for c in want if c not in found]
    missing_required = [c for c in need if c not in found]
    if absent:
        print(f"  {table}: {len(have)}/{len(want)} wanted columns present; "
              f"absent -> {', '.join(absent)}")
    else:
        print(f"  {table}: all {len(want)} wanted columns present")
    return have, missing_required


# ---- pulls ----------------------------------------------------------------
def fetch_rows(conn, sql: str, ids: list[str], chunk: int, label: str) -> list[dict]:
    from psycopg2.extras import RealDictCursor
    rows: list[dict] = []
    with conn.cursor(cursor_factory=RealDictCursor) as c:
        for i in range(0, len(ids), chunk):
            c.execute(sql, {"ids": ids[i:i + chunk]})
            rows.extend(dict(r) for r in c.fetchall())
            print(f"    {label}: {min(i + chunk, len(ids))}/{len(ids)} ids, "
                  f"{len(rows)} rows", flush=True)
    return rows


def build_sql(schema: str, studies_cols, outcomes_cols, analyses_cols):
    s_sel = ", ".join(f"s.{c}" for c in studies_cols)
    o_sel = ", ".join(f"o.{c} AS outcome_{c}" for c in outcomes_cols)
    a_sel = ", ".join(f"oa.{c} AS analysis_{c}" for c in analyses_cols)
    studies_sql = (f"SELECT {s_sel} FROM {schema}.studies s "
                   f"WHERE s.nct_id = ANY(%(ids)s);")
    # LEFT JOIN so a primary outcome with zero analyses still produces a row -- that is
    # exactly the tier-D population and it must be counted, not silently dropped.
    outcomes_sql = (
        f"SELECT {o_sel}, {a_sel} "
        f"FROM {schema}.outcomes o "
        f"LEFT JOIN {schema}.outcome_analyses oa ON oa.outcome_id = o.id "
        f"WHERE o.nct_id = ANY(%(ids)s) AND lower(o.outcome_type) = 'primary';"
    )
    return studies_sql, outcomes_sql


# ---- derivation -----------------------------------------------------------
def derive(studies: list[dict], outcome_rows: list[dict], alpha: float,
           broad_safety: bool) -> list[dict]:
    """studies rows + flattened outcome/analysis rows -> one label record per trial."""
    by_trial: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in outcome_rows:
        nct = str(r.get("outcome_nct_id") or "").upper()
        oid = str(r.get("outcome_id") or r.get("analysis_outcome_id") or "")
        if not nct or not oid:
            continue
        # strip the analysis_ prefix so the engine sees plain AACT field names
        analysis = {k[len("analysis_"):]: _scalar(v) for k, v in r.items()
                    if k.startswith("analysis_")}
        if analysis.get("id") is None and analysis.get("p_value") is None \
                and analysis.get("ci_lower_limit") is None:
            by_trial[nct].setdefault(oid, [])          # outcome exists, no analysis
        else:
            by_trial[nct][oid].append(analysis)
    out = []
    for s in studies:
        nct = str(s.get("nct_id") or "").upper()
        s = {k: _scalar(v) for k, v in s.items()}
        rec = label_row(s, dict(by_trial.get(nct, {})), alpha, broad_safety)
        posted = (_scalar(s.get("results_first_posted_date"))
                  or _scalar(s.get("results_first_submitted_date")))
        rec.update({
            "phase": _scalar(s.get("phase")) or "",
            "study_type": _scalar(s.get("study_type")) or "",
            "overall_status": _scalar(s.get("overall_status")) or "",
            "results_posted": int(posted is not None),
            "results_first_posted_date": str(posted) if posted is not None else "",
            "start_date": str(_scalar(s.get("start_date")) or ""),
            "completion_date": str(_scalar(s.get("completion_date")) or ""),
            "primary_completion_date": str(_scalar(s.get("primary_completion_date")) or ""),
            "enrollment": _scalar(s.get("enrollment")),
            "number_of_arms": _scalar(s.get("number_of_arms")),
        })
        out.append(rec)
    return out


# ---- audit ----------------------------------------------------------------
def audit(records: list[dict], alpha: float) -> None:
    import pandas as pd
    d = pd.DataFrame(records)
    n = len(d)
    print(_rule("LABEL COVERAGE (the number that decides whether this project is real)"))
    posted = int(d["results_posted"].sum())
    with_outcomes = int((d["n_primary_outcomes"] > 0).sum())
    with_analyses = int((d["n_analyses_total"] > 0).sum())
    print(f"  trials pulled                        : {n}")
    print(f"  results posted (any)                 : {posted} ({_pct(posted, n)})")
    print(f"  >=1 primary outcome row              : {with_outcomes} ({_pct(with_outcomes, n)})")
    print(f"  >=1 primary ANALYSIS row             : {with_analyses} ({_pct(with_analyses, n)})")
    print("  the gap between the last two lines is the real coverage cliff: sponsors post")
    print("  outcome measurements far more often than statistical analyses.")

    for variant in ("strict", "broad"):
        col = f"endpoint_met_{variant}"
        lab = d[d[col].notna()]
        pos = int((lab[col] == 1).sum())
        print(f"\n  {variant:6s}: labelled {len(lab)}/{n} ({_pct(len(lab), n)})  "
              f"positives {pos} ({_pct(pos, len(lab))} of labelled)")
        head = lab[lab["tier_min"].isin(HEADLINE_TIERS)]
        pos_h = int((head[col] == 1).sum())
        print(f"          headline tiers only (A/B): {len(head)} "
              f"({_pct(len(head), n)})  positives {pos_h} ({_pct(pos_h, len(head))})")
        if variant == "broad":
            src = Counter(lab["label_source_broad"])
            print(f"          sources: {dict(src)}")

    print(_rule("TIER DISTRIBUTION (trial level, by tier_min)"))
    for t in TIER_ORDER:
        c = int((d["tier_min"] == t).sum())
        print(f"  {t:22s}: {c:6d} ({_pct(c, n)})  {TIER_DOC[t]}")

    print(_rule("MULTI-ENDPOINT STRUCTURE (all four readings are carried, none chosen yet)"))
    hist = Counter(int(x) for x in d["n_primary_outcomes"].fillna(0))
    print("  primary outcomes per trial: "
          + "  ".join(f"{k}:{hist[k]}" for k in sorted(hist)[:10]))
    dec = d[d["n_primary_analyzed"] > 0]
    if len(dec):
        agree = int((dec["any_primary_met"] == dec["all_primary_met"]).sum())
        print(f"  trials where any_met == all_met      : {agree}/{len(dec)} "
              f"({_pct(agree, len(dec))})")
        print(f"  trials where the two readings DIFFER : {len(dec) - agree} "
              f"<- the multi-endpoint ambiguity, quantified")
        print(f"  mean frac_primary_met                : "
              f"{dec['frac_primary_met'].mean():.3f}")

    print(_rule("why_stopped CLASSIFICATION (broad-label input; never a feature)"))
    for k, v in Counter(d["why_stopped_class"]).most_common():
        print(f"  {k:18s}: {v:6d} ({_pct(v, n)})")
    print("  conservative by design: unmatched text -> 'other', never 'futility'.")

    print(_rule("POSTING-BIAS PREVIEW (within-cohort only -- NOT the Step-2 audit)"))
    print("  This cohort is already selected (TOP x ChEMBL-matched). A real bias audit must")
    print("  compare posters against non-posters across the FULL AACT population. Treat")
    print("  everything below as a smell test, not a result.")
    for col in ("phase", "overall_status"):
        if col not in d.columns:
            continue
        print(f"\n  posting rate by {col}:")
        grp = d.groupby(d[col].replace("", "(blank)"))["results_posted"]
        for key, sub in sorted(grp, key=lambda kv: -len(kv[1]))[:8]:
            print(f"    {str(key)[:34]:34s} n={len(sub):6d}  posted={_pct(int(sub.sum()), len(sub))}")
    yr = d["start_date"].astype(str).str.extract(r"(\d{4})")[0]
    if yr.notna().any():
        d2 = d.assign(_yr=yr.astype(float))
        buckets = FDAAA_YEAR_BUCKETS
        print("\n  posting rate by start-year bucket (FDAAA took effect 2008, "
              "enforcement tightened 2017):")
        for lo, hi in buckets:
            sub = d2[(d2["_yr"] >= lo) & (d2["_yr"] <= hi)]
            if len(sub):
                open_end = hi >= FDAAA_YEAR_BUCKETS[-1][1]
                span = f"{lo}-{'now' if open_end else hi}"
                rate = _pct(int(sub["results_posted"].sum()), len(sub))
                print(f"    {span:12s} n={len(sub):6d}  posted={rate}")

    print(_rule("R7 REMINDER"))
    print("  These fields are LABEL INPUTS and must be excluded from every feature matrix:")
    print("    " + ", ".join(LABEL_DERIVED_FIELDS))
    print(f"  alpha used for the p-value rule: {alpha:g} (a parameter, not a constant --")
    print("  group-sequential designs and alpha-splitting across primaries use others).")


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--cto", default=Path("data/cto/cto_trials.csv"), type=Path)
    ap.add_argument("--ids", nargs="*", default=None, help="explicit nct_ids (overrides files)")
    ap.add_argument("--out", default=Path("data/aact/trial_labels.csv"), type=Path)
    ap.add_argument("--raw-prefix", default=Path("data/aact/results_raw"), type=Path,
                    help="write raw pulled rows here so labels can be re-derived offline")
    ap.add_argument("--from-raw", default=None, type=Path,
                    help="re-derive from a previous --raw-prefix dump, no DB needed")
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--broad-includes-safety", action="store_true",
                    help="fold safety terminations into the broad label as 0 (off by default)")
    ap.add_argument("--host", default="aact-db.ctti-clinicaltrials.org")
    ap.add_argument("--db", default="aact")
    ap.add_argument("--schema", default="ctgov")
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--chunk", type=int, default=2000)
    ap.add_argument("--probe-only", action="store_true", help="schema check, then stop")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print(f"!! refusing an unsafe schema name: {args.schema!r}")
        return 2

    print(_rule("AACT RESULTS PULL -> PER-TRIAL ENDPOINT-MET LABEL"))
    print_tier_legend()

    # ---- offline path: re-derive from a saved dump -------------------------
    if args.from_raw:
        s_path = Path(f"{args.from_raw}_studies.csv")
        o_path = Path(f"{args.from_raw}_outcomes.csv")
        if not (s_path.exists() and o_path.exists()):
            print(f"!! need both {s_path} and {o_path}")
            return 2
        studies = pd.read_csv(s_path).where(lambda x: x.notna(), None).to_dict("records")
        orows = pd.read_csv(o_path).where(lambda x: x.notna(), None).to_dict("records")
        print(f"\n  offline re-derive: {len(studies)} studies, {len(orows)} outcome/analysis rows")
        records = derive(studies, orows, args.alpha, args.broad_includes_safety)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(records).reindex(columns=OUT_COLS).to_csv(args.out, index=False)
        audit(records, args.alpha)
        print(f"\n  wrote -> {args.out}")
        return 0

    if not args.user or not args.password:
        print("\n!! set AACT_USER / AACT_PASSWORD (free account at aact.ctti-clinicaltrials.org).")
        print("   Note: this is the Postgres mirror, NOT a ClinicalTrials.gov API key --")
        print("   the v2 API does not expose outcome_analyses.")
        return 2

    print("\n  collecting nct_ids:")
    ids = collect_nctids(args.units, args.cto, args.ids)
    print(f"  total distinct nct_ids: {len(ids)}")
    if not ids and not args.probe_only:
        print("!! no ids to pull. Pass --ids or check the input paths.")
        return 2

    import psycopg2
    conn = psycopg2.connect(host=args.host, port=5432, dbname=args.db,
                            user=args.user, password=args.password)
    try:
        with conn.cursor() as c:
            # schema name validated above, so direct interpolation is safe here
            c.execute(f"SET search_path TO {args.schema}, public;")

        print(_rule(f"SCHEMA PROBE (schema '{args.schema}')"))
        found = probe_schema(conn, args.schema)
        for t, cols in found.items():
            if not cols:
                print(f"  !! table not visible: {t}")
        s_cols, s_missing = resolve_columns(found["studies"], STUDIES_WANT, STUDIES_NEED, "studies")
        o_cols, o_missing = resolve_columns(found["outcomes"], OUTCOMES_WANT, OUTCOMES_NEED, "outcomes")
        a_cols, a_missing = resolve_columns(found["outcome_analyses"], ANALYSES_WANT,
                                           ANALYSES_NEED, "outcome_analyses")
        blocking = s_missing + o_missing + a_missing
        if blocking:
            print(f"\n!! required columns absent: {', '.join(blocking)}")
            print("   The AACT schema has moved. Paste this probe output and the query will")
            print("   be adjusted rather than guessed at.")
            return 3
        print("  probe OK: every required column is present.")
        if args.probe_only:
            print("\n  --probe-only: stopping before the pull.")
            return 0

        studies_sql, outcomes_sql = build_sql(args.schema, s_cols, o_cols, a_cols)
        print(_rule("PULLING"))
        studies = fetch_rows(conn, studies_sql, ids, args.chunk, "studies")
        orows = fetch_rows(conn, outcomes_sql, ids, args.chunk, "primary outcomes+analyses")
    finally:
        conn.close()

    print(f"\n  studies matched: {len(studies)}/{len(ids)} "
          f"({_pct(len(studies), len(ids))})")
    print(f"  primary outcome/analysis rows: {len(orows)}")

    args.raw_prefix.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(studies).to_csv(f"{args.raw_prefix}_studies.csv", index=False)
    pd.DataFrame(orows).to_csv(f"{args.raw_prefix}_outcomes.csv", index=False)
    print(f"  raw dumps -> {args.raw_prefix}_studies.csv, {args.raw_prefix}_outcomes.csv")
    print("  (re-derive labels offline with --from-raw, no second query needed)")

    records = derive(studies, orows, args.alpha, args.broad_includes_safety)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).reindex(columns=OUT_COLS).to_csv(args.out, index=False)
    audit(records, args.alpha)
    print(f"\n  wrote -> {args.out}")
    print("  NEXT: the Step-2 posting-bias audit against the full AACT population.")
    print("  No modelling until that is on the table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())