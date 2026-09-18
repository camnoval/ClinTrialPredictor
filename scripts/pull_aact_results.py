#!/usr/bin/env python3
"""Step 1/6.2: mine AACT for the endpoint-met label across the FULL trial population.

WHAT CHANGED FROM THE COHORT VERSION
====================================
This script used to take its id list from `data/chembl/units.csv` -- the TOP x ChEMBL
cohort, 5,128 trials. That restriction was load-bearing for the old ChEMBL-approval
project and pure cost for this one: the endpoint-met label reads only AACT's own
`outcomes` / `outcome_analyses` tables and needs neither TOP nor ChEMBL. The cohort was
also the stated reason the headline negative class was capped at 369 trials, which is the
number the model-class decision rests on.

So the population is now defined BY QUERY, not by a file: every trial AACT knows about,
filtered to interventional through the tested pure predicate in
`trial_pos.services.population`. The id list is gone. `--ids` survives for spot checks.

=============================================================================
FDAAA: RAW FIELDS ARE CARRIED, APPLICABILITY IS NOT DECIDED HERE
=============================================================================
FDAAA applicability is not an AACT field -- it is a derivation whose inputs are partly
sponsor-self-reported on a form that postdates the 2017 Final Rule, so their coverage is
unknown until measured. Deciding applicability inside a pull would bury that judgment
where nobody could audit it.

This script therefore carries the component fields per row, tri-state, and reports their
coverage. It writes no applicability verdict. The rule gets written once, in the Step-6.3
audit, with its inputs' coverage on the table. `population.py` has a test that fails if an
applicability function ever appears in the pull path.

=============================================================================
THE FOUR TIERS -- printed on every run, and carried per row in the output
=============================================================================
  A  superiority analysis, p-value present            -> headline
  B  non-inferiority/equivalence, p-value present     -> headline
  C  no p-value, CI excludes null                     -> NOT headline, sensitivity only
  D  no decidable analysis (single-arm/descriptive)    -> endpoint_met UNKNOWN
`tier_min` is the weakest tier a trial's label rests on and is the correct headline filter.
Never report a pooled number without saying which tiers were pooled.

=============================================================================
TWO LABEL VARIANTS
=============================================================================
  endpoint_met_strict  posted analyses only. Headline.
  endpoint_met_broad   adds terminated-for-futility as 0, stopped-for-efficacy as 1.
                       Sensitivity only. Its extra rows come from `why_stopped`, so
                       `why_stopped`/`overall_status` and friends are LABEL INPUTS and
                       must never be features -- for BOTH variants, so the two stay
                       comparable. See endpoint_label.LABEL_DERIVED_FIELDS.

=============================================================================
WHAT THIS SCRIPT STILL DOES *NOT* DO
=============================================================================
It reports coverage and component availability. It does not do the Step-6.3 posting-bias
audit, and it does not decide the applicability domain or the reweighting. Widening the
population makes the DENOMINATOR honest; it does not fix the fact that the label exists
only where a sponsor posted an analysis. That asymmetry is the audit's subject.

Memory: derivation runs per chunk and results stream to CSV, because the full population
is two orders of magnitude larger than the old cohort and accumulating every joined
outcome row would not fit comfortably on a laptop.

Access: free AACT account -> env AACT_USER / AACT_PASSWORD (or --user/--password).
        Host aact-db.ctti-clinicaltrials.org:5432, db 'aact', schema 'ctgov'.
        This is NOT a ClinicalTrials.gov API key; the v2 API has no outcome_analyses.
Deps: psycopg2-binary, pandas.

Usage (run in this order the first time):
  python scripts\\pull_aact_results.py --probe-only
  python scripts\\pull_aact_results.py --max-trials 2000      # smoke run
  python scripts\\pull_aact_results.py                        # the full pull
  python scripts\\pull_aact_results.py --from-raw data\\aact\\results_raw   # offline
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Make src/ importable without depending on PYTHONPATH being set in the session.
# `pip install -e .` is the durable fix and makes this a no-op; this guard means the
# script still runs in a fresh shell, or in a conda env where the package was never
# installed. run_checks.py sets PYTHONPATH itself, which is why the gate can be green
# while a script run by hand fails -- that asymmetry is what this block removes.
# Matches validate_reconstruction.py, which has had it since the same lesson.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from trial_pos.services.endpoint_label import (
    DEFAULT_ALPHA, HEADLINE_TIERS, LABEL_DERIVED_FIELDS, TIER_DOC, TIER_ORDER, label_row,
)
from trial_pos.services.resume import (
    build_manifest, describe_conflicts, header_problems, ids_remaining,
    manifest_conflicts,
)
from trial_pos.services.population import (
    ERA_DATE_SOURCE_DOC, ERA_DATE_SOURCES, ERA_DOC, ERAS, FDAAA_COMPONENT_DOC,
    FDAAA_COMPONENTS, POSTING_OUTCOME_FIELDS, UNKNOWN, component_coverage,
    components_for_row, era_coverage_by_source, era_for_row, gap_summary,
    is_interventional, normalize_study_type, tribool,
)

# ---- columns wanted. The probe intersects these with what the server has, so a schema
# change costs a warning line rather than a failed pull.
STUDIES_WANT = [
    "nct_id", "study_type", "phase", "overall_status", "why_stopped", "enrollment",
    "enrollment_type", "number_of_arms", "number_of_groups", "start_date",
    "primary_completion_date", "completion_date", "results_first_submitted_date",
    "results_first_posted_date", "last_update_posted_date",
    # FDAAA component inputs living on `studies`. NOT required: several postdate the 2017
    # form, and measuring how often they are absent is the point of this step.
    "is_fda_regulated_drug", "is_fda_regulated_device", "is_us_export",
    "has_expanded_access",
]
STUDIES_NEED = ["nct_id", "overall_status", "why_stopped"]

# AACT's own derived table. `has_us_facility` is the jurisdictional hook that exists for
# OLD trials (it comes from facility records, not a sponsor declaration), which makes it
# the most useful component for the pre-2017 era. `were_results_reported` is AACT's
# results-posted flag: pulled as the audit's OUTCOME variable, and registered in
# LABEL_DERIVED_FIELDS so it can never become a feature.
CALC_WANT = ["nct_id", "has_us_facility", "were_results_reported",
             "registered_in_calendar_year", "number_of_facilities"]
CALC_NEED: list[str] = []          # entirely optional; absence is a finding, not an error

OUTCOMES_WANT = ["id", "nct_id", "outcome_type", "title", "time_frame", "population"]
OUTCOMES_NEED = ["id", "nct_id", "outcome_type"]

ANALYSES_WANT = [
    "id", "nct_id", "outcome_id", "non_inferiority_type", "non_inferiority_description",
    "param_type", "param_value", "p_value", "p_value_modifier", "p_value_description",
    "ci_lower_limit", "ci_upper_limit", "ci_percent", "method", "groups_desc",
]
ANALYSES_NEED = ["outcome_id", "p_value"]


def _dedup(seq):
    """Order-preserving de-duplication, so OUT_COLS cannot grow a repeated column when a
    field is both a label output and an FDAAA component (`phase` and
    `primary_completion_date` are both)."""
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# Built from FDAAA_COMPONENTS rather than retyped, so adding a component to the spec
# automatically widens the output instead of being silently dropped at write time.
_COMPONENT_COLS = [name for name, _t, _c, _k, _w in FDAAA_COMPONENTS]

OUT_COLS = _dedup([
    "nct_id", "phase", "study_type", "study_type_norm", "overall_status",
    "why_stopped_class",
    "results_posted", "results_first_posted_date", "start_date", "completion_date",
    "primary_completion_date", "enrollment", "number_of_arms",
    *_COMPONENT_COLS, "fdaaa_era", "era_date_source", *POSTING_OUTCOME_FIELDS,
    "n_primary_outcomes", "n_primary_analyzed", "n_primary_met", "frac_primary_met",
    "any_primary_met", "all_primary_met", "n_analyses_total",
    "tier_min", "tier_max", "tier_mix",
    "endpoint_met_strict", "endpoint_met_broad",
    "label_source_strict", "label_source_broad", "label_rule", "alpha_used",
])

# The events-per-variable floor the additive-model decision was argued from. Named here
# because the audit prints it as the threshold the recount should be read against, and a
# threshold nobody can see is a threshold nobody can check. Feature count is approximate
# (the matrix does not exist yet), so this is a reading aid, not a test.
EPV_FLOOR = 10
PLANNED_FEATURE_COUNT = 40


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


def print_configuration(args):
    """Every value that changes the output, printed. A threshold nobody can see is a
    threshold nobody can check."""
    print(_rule("CONFIGURATION (every value that changes the output)"))
    print(f"  population scope         : {args.population}")
    print(f"  explicit --ids           : {len(args.ids) if args.ids else 0}")
    print(f"  alpha (p-value rule)     : {args.alpha:g}")
    print(f"  broad includes safety    : {args.broad_includes_safety}")
    print(f"  era date fallback        : {args.era_fallback} "
          f"(primary_completion_date -> completion_date)")
    print(f"  max trials (0 = no cap)  : {args.max_trials}")
    print(f"  chunk size (ids/query)   : {args.chunk}")
    print(f"  resume                   : {args.resume}{' (FORCED)' if args.force_resume else ''}")
    print(f"  schema                   : {args.schema}")
    print(f"  output                   : {args.out}")
    print(f"  raw dumps                : {args.raw_prefix}_studies.csv, "
          f"{args.raw_prefix}_outcomes.csv")


# ---- schema probe ---------------------------------------------------------
def probe_schema(conn, schema: str) -> dict[str, set[str]]:
    """{table: set(columns)} for the tables we touch. Empty set = table not visible."""
    tables = ["studies", "outcomes", "outcome_analyses", "calculated_values"]
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


def report_component_availability(s_cols, c_cols):
    """Which FDAAA components this server can actually supply, and why each is wanted.

    Printed BEFORE the pull, so a missing component is known in advance rather than
    discovered as a column of nulls afterwards.
    """
    print(_rule("FDAAA COMPONENT AVAILABILITY (no applicability verdict is computed)"))
    available = set(s_cols) | set(c_cols)
    for name, table, column, kind, why in FDAAA_COMPONENTS:
        mark = "OK  " if column in available else "MISS"
        print(f"  [{mark}] {name:26s} {table}.{column} ({kind})")
        print(f"         {why}")
    for field in POSTING_OUTCOME_FIELDS:
        mark = "OK  " if field in available else "MISS"
        print(f"  [{mark}] {field:26s} calculated_values.{field} -- audit OUTCOME, "
              f"never a feature")
    print("  A MISS is a finding, not an error: it sizes how much of the population the")
    print("  eventual applicability rule cannot reach. That is why this step defers the")
    print("  rule instead of writing one now.")


# ---- pulls ----------------------------------------------------------------
def fetch_population(conn, schema: str, population: str, max_trials: int):
    """-> (ids, study_type_tally). The population is decided by TESTED PURE CODE.

    Every (nct_id, study_type) pair is pulled and filtered through
    `population.is_interventional`, rather than encoding "interventional" as a SQL LIKE.
    Two reasons: the definition then lives in exactly one tested place, and the tri-state
    result is preserved, so trials whose type is UNKNOWN are counted as unknown instead of
    being silently excluded by a predicate that reads them as non-matching.

    A server-side cursor streams the id list so a half-million-row result never lands in
    memory at once.
    """
    sql = f"SELECT nct_id, study_type FROM {schema}.studies ORDER BY nct_id;"
    ids: list[str] = []
    tally: Counter = Counter()
    with conn.cursor(name="trial_pos_population") as c:
        c.itersize = 20000
        c.execute(sql)
        for nct_id, study_type in c:
            norm = normalize_study_type(study_type)
            tally[norm if norm is not None else UNKNOWN] += 1
            keep = True if population == "all" else (is_interventional(study_type) is True)
            if keep and nct_id:
                ids.append(str(nct_id).upper())
    print(_rule("POPULATION (defined by query, not by a cohort file)"))
    total = sum(tally.values())
    print(f"  trials in AACT.studies       : {total}")
    for key, n in tally.most_common():
        print(f"    {str(key)[:40]:40s} {n:8d} ({_pct(n, total)})")
    print(f"  selected (scope={population:14s}): {len(ids)} ({_pct(len(ids), total)})")
    print("  'unknown' above is study_type absent or 'N/A' -- counted separately from")
    print("  'observational', because absent is not the same claim as non-interventional.")
    if max_trials and len(ids) > max_trials:
        ids = ids[:max_trials]
        print(f"\n  !! --max-trials {max_trials} applied: pulling only the FIRST "
              f"{len(ids)} ids")
        print("     in nct_id order. This is a SMOKE RUN. nct_id order is roughly")
        print("     registration order and therefore correlated with era, so the coverage")
        print("     and negative-class numbers below are NOT the population's.")
    return ids, tally


def fetch_rows(conn, sql: str, ids: list[str]) -> list[dict]:
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as c:
        c.execute(sql, {"ids": ids})
        return [dict(r) for r in c.fetchall()]


def build_sql(schema: str, studies_cols, calc_cols, outcomes_cols, analyses_cols):
    s_sel = ", ".join(f"s.{c}" for c in studies_cols)
    # calculated_values joins on nct_id -- a NATURAL key. AACT regenerates surrogate keys
    # on every nightly rebuild, so nct_id is the only id safe to join on at all, let alone
    # across snapshots.
    c_sel = ", ".join(f"cv.{c} AS {c}" for c in calc_cols if c != "nct_id")
    join_calc = (f" LEFT JOIN {schema}.calculated_values cv ON cv.nct_id = s.nct_id"
                 if c_sel else "")
    studies_sql = (f"SELECT {s_sel}{', ' + c_sel if c_sel else ''} "
                   f"FROM {schema}.studies s{join_calc} "
                   f"WHERE s.nct_id = ANY(%(ids)s);")
    o_sel = ", ".join(f"o.{c} AS outcome_{c}" for c in outcomes_cols)
    a_sel = ", ".join(f"oa.{c} AS analysis_{c}" for c in analyses_cols)
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
           broad_safety: bool, era_fallback: bool = True) -> list[dict]:
    """studies rows + flattened outcome/analysis rows -> one label record per trial.

    Correct per chunk as well as in aggregate: rows are filtered by nct_id, so a chunk
    holds every outcome for every trial it contains and no cross-chunk state is needed.
    That is what makes the streaming write safe.
    """
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
    for raw in studies:
        nct = str(raw.get("nct_id") or "").upper()
        s = {k: _scalar(v) for k, v in raw.items()}
        rec = label_row(s, dict(by_trial.get(nct, {})), alpha, broad_safety)
        posted = (_scalar(s.get("results_first_posted_date"))
                  or _scalar(s.get("results_first_submitted_date")))
        # FDAAA components read from the RAW row, not the _scalar-cleaned one: tribool is
        # the tested parser for both the Postgres-native bool and the CSV 't'/'f' form,
        # and routing through _scalar first would add a second, untested coercion.
        components = components_for_row(raw)
        rec.update({
            "phase": _scalar(s.get("phase")) or "",
            "study_type": _scalar(s.get("study_type")) or "",
            "study_type_norm": normalize_study_type(raw.get("study_type")) or "",
            "overall_status": _scalar(s.get("overall_status")) or "",
            "results_posted": int(posted is not None),
            "results_first_posted_date": str(posted) if posted is not None else "",
            "start_date": str(_scalar(s.get("start_date")) or ""),
            "completion_date": str(_scalar(s.get("completion_date")) or ""),
            "enrollment": _scalar(s.get("enrollment")),
            "number_of_arms": _scalar(s.get("number_of_arms")),
        })
        rec.update(components)
        # Era is binned on primary_completion_date -- the date a posting deadline runs
        # from -- falling back to completion_date only when the flag allows it. The source
        # travels with the value, because an era read without knowing which date produced
        # it cannot be audited.
        era, era_source = era_for_row(raw, era_fallback)
        rec["fdaaa_era"] = era or ""
        rec["era_date_source"] = era_source
        for field in POSTING_OUTCOME_FIELDS:
            rec[field] = tribool(raw.get(field))
        out.append(rec)
    return out


# ---- streaming output -----------------------------------------------------
class StreamWriter:
    """Append rows to a CSV with a fixed header. Bounds memory on the full population.

    Opened lazily so a run that dies during the probe does not leave a zero-row file that
    looks like a completed pull which found nothing.

    In append mode the existing header is validated for EXACT ORDER before a single row
    is written. Same columns in a different order is silent corruption -- values land
    under the wrong headers and nothing downstream can detect it -- so a mismatch raises
    rather than warning.
    """

    def __init__(self, path: Path, fieldnames: list[str], append: bool = False):
        self.path = path
        self.fieldnames = fieldnames
        self.append = append and path.exists()
        self._fh = None
        self._writer = None
        self.n_written = 0
        if self.append:
            problems = header_problems(read_header(path), fieldnames)
            if problems:
                raise ValueError(
                    f"refusing to append to {path}:\n    "
                    + "\n    ".join(problems))

    def write(self, rows: list[dict]):
        if not rows:
            return
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if self.append else "w"
            self._fh = self.path.open(mode, newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._fh, fieldnames=self.fieldnames,
                                          extrasaction="ignore")
            if not self.append:
                self._writer.writeheader()
        for r in rows:
            self._writer.writerow({k: r.get(k) for k in self.fieldnames})
        self.n_written += len(rows)
        self._fh.flush()

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def read_header(path: Path):
    """First row of a CSV as a list, or None if the file is absent/empty."""
    if not path.exists():
        return None
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            return row
    return None


def read_done_ids(path: Path, column: str = "nct_id") -> list:
    """nct_ids already present in an output CSV. Streamed, never loaded whole."""
    if not path.exists():
        return []
    out = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            value = (row.get(column) or "").strip()
            if value:
                out.append(value)
    return out


MANIFEST_SUFFIX = ".manifest.json"


def manifest_path(out_path: Path) -> Path:
    return Path(str(out_path) + MANIFEST_SUFFIX)


def load_manifest(out_path: Path):
    p = manifest_path(out_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt manifest must read as "unknown", which manifest_conflicts treats as
        # conflicting on every field -- never as agreement.
        return None


def save_manifest(out_path: Path, manifest: dict) -> None:
    p = manifest_path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


# ---- audit ----------------------------------------------------------------
def audit(records: list[dict], alpha: float, population: str, capped: bool,
          era_fallback: bool) -> None:
    import pandas as pd
    d = pd.DataFrame(records)
    n = len(d)
    if not n:
        print("\n!! no records derived -- nothing to audit.")
        return

    print(_rule("LABEL COVERAGE (the number that decides whether this project is real)"))
    posted = int(d["results_posted"].sum())
    with_outcomes = int((d["n_primary_outcomes"] > 0).sum())
    with_analyses = int((d["n_analyses_total"] > 0).sum())
    print(f"  trials pulled                        : {n}")
    print(f"  results posted (any)                 : {posted} ({_pct(posted, n)})")
    print(f"  >=1 primary outcome row              : {with_outcomes} ({_pct(with_outcomes, n)})")
    print(f"  >=1 primary ANALYSIS row             : {with_analyses} ({_pct(with_analyses, n)})")
    print("  the gap between the last two lines is the coverage cliff: sponsors post")
    print("  outcome measurements far more often than statistical analyses.")
    print("  NOTE: widening the population makes this DENOMINATOR honest. It does not")
    print("  make the label less selective -- that is the Step-6.3 audit's subject.")

    print(_rule("NEGATIVE-CLASS RECOUNT (the model-class decision depends on this)"))
    print("  The additive-model-only decision rested on ~369 headline negatives against")
    print(f"  ~{PLANNED_FEATURE_COUNT} features, i.e. ~9 events per variable, below the "
          f"{EPV_FLOOR}-20 rule of thumb.")
    print("  That count came from the 5,128-trial cohort. Here it is again, unrestricted:")
    for variant in ("strict", "broad"):
        col = f"endpoint_met_{variant}"
        lab = d[d[col].notna()]
        head = lab[lab["tier_min"].isin(HEADLINE_TIERS)]
        neg = int((head[col] == 0).sum())
        pos = int((head[col] == 1).sum())
        print(f"    {variant:6s} headline: {len(head):7d} labelled | "
              f"positives {pos:7d} | NEGATIVES {neg:7d}")
    threshold = EPV_FLOOR * PLANNED_FEATURE_COUNT
    if capped:
        print("  !! --max-trials was applied, so this is NOT the population count.")
        print("     Do not carry these numbers into the model-class decision.")
    else:
        print(f"  Read the strict-headline NEGATIVES figure against ~"
              f"{PLANNED_FEATURE_COUNT} features. At {EPV_FLOOR} events")
        print(f"  per variable the floor is {threshold}. Above that, the events-per-variable")
        print("  argument that ruled out boosting no longer holds on its own terms and")
        print("  must be re-made on other grounds or dropped. Below it, the argument")
        print("  stands and the additive model remains forced rather than chosen.")

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
            print(f"          sources: {dict(Counter(lab['label_source_broad']))}")

    print(_rule("TIER DISTRIBUTION (trial level, by tier_min)"))
    for t in TIER_ORDER:
        c = int((d["tier_min"] == t).sum())
        print(f"  {t:22s}: {c:7d} ({_pct(c, n)})  {TIER_DOC[t]}")

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
        print(f"  {k:18s}: {v:7d} ({_pct(v, n)})")
    print("  conservative by design: unmatched text -> 'other', never 'futility'.")

    # ---- what the eventual FDAAA rule will have to work with ----------------
    print(_rule("FDAAA COMPONENT COVERAGE (inputs only -- NO applicability verdict)"))
    print("  Tri-state on purpose. 'false' means the sponsor said no; 'unknown' means the")
    print("  question was never asked -- most often because the field postdates the trial.")
    print("  Collapsing the two would manufacture a finding.")
    cov = component_coverage(records)
    for name, buckets in cov.items():
        parts = "  ".join(f"{k}={v} ({_pct(v, n)})" for k, v in buckets.items())
        print(f"\n  {name}")
        print(f"    {parts}")
        print(f"    why carried: {FDAAA_COMPONENT_DOC[name]}")

    print(_rule("REGULATORY ERA (descriptive bins, derived from the statutory dates)"))
    print(f"  era date fallback: {'ON' if era_fallback else 'OFF'} (--era-fallback / "
          f"--no-era-fallback)")
    print("  Binned on primary_completion_date -- the date a posting deadline runs from --")
    print("  falling back to completion_date where the flag allows and primary is absent.")
    print("  Reported BOTH ways below so the fallback's effect is visible, not assumed.")

    by_source = era_coverage_by_source(records, era_fallback)
    primary_only = era_coverage_by_source(records, allow_fallback=False)

    def _era_line(label, buckets, denom):
        parts = "  ".join(f"{name.split('_')[0] if name != UNKNOWN else name}="
                          f"{buckets[name]} ({_pct(buckets[name], denom)})"
                          for name in [e[0] for e in ERAS] + [UNKNOWN])
        print(f"    {label:24s} {parts}")

    print("\n  A. primary_completion_date only (no fallback):")
    pooled_primary = {k: 0 for k in [e[0] for e in ERAS] + [UNKNOWN]}
    for buckets in primary_only.values():
        for k, v in buckets.items():
            pooled_primary[k] += v
    _era_line("all rows", pooled_primary, n)

    if era_fallback:
        print("\n  B. with fallback, split by which date was used:")
        for src in ERA_DATE_SOURCES:
            buckets = by_source[src]
            subtotal = sum(buckets.values())
            if not subtotal:
                continue
            _era_line(f"{src} (n={subtotal})", buckets, subtotal)
            print(f"      {ERA_DATE_SOURCE_DOC[src]}")
        pooled = {k: 0 for k in [e[0] for e in ERAS] + [UNKNOWN]}
        for buckets in by_source.values():
            for k, v in buckets.items():
                pooled[k] += v
        print()
        _era_line("pooled", pooled, n)
        recovered = pooled_primary[UNKNOWN] - pooled[UNKNOWN]
        print(f"\n  era recovered by the fallback: {recovered} trials "
              f"({_pct(recovered, n)} of the pull)")
        print("  COMPARE A against the pooled line of B. If the era distribution shifts")
        print("  materially, the fallback is doing work that needs justifying: overall")
        print("  completion can postdate the primary readout, so a fallback row's era can")
        print("  be too LATE, which would overstate how many trials faced the tighter rule.")

    print("\n  primary-to-overall completion gap, where both dates exist:")
    gaps = gap_summary(records)
    if gaps["n_comparable"]:
        print(f"    comparable rows {gaps['n_comparable']}  "
              f"median {gaps['q50']}d  p90 {gaps['q90']}d  "
              f"min {gaps['min']}d  max {gaps['max']}d")
        print(f"    negative gaps (overall BEFORE primary -- registry errors): "
              f"{gaps['n_negative']}")
        print("    A median of days means the fallback is nearly harmless. A median of")
        print("    years means it moves trials across era boundaries and the")
        print("    completion_date-sourced rows must be read separately, never pooled.")
    else:
        print("    no rows have both dates; the fallback's risk cannot be sized here.")

    for name, _s, _e in ERAS:
        print(f"\n  {name}: {ERA_DOC[name]}")
    print("\n  These bins are DESCRIPTIVE. They do not assert that any trial was required")
    print("  to post; that determination is Step 6.3's, with the coverage above in hand.")

    print(_rule("POSTING OUTCOME (the audit's dependent variable, never a feature)"))
    for field in POSTING_OUTCOME_FIELDS:
        if field not in d.columns:
            print(f"  {field}: absent from this pull")
            continue
        vc = Counter(tribool(v) for v in d[field])
        print(f"  {field}: true={vc[True]}  false={vc[False]}  unknown={vc[None]}")
        known_mask = [tribool(v) is not None for v in d[field]]
        known = d[known_mask]
        if len(known):
            ours = known["results_posted"].astype(bool)
            theirs = [bool(tribool(v)) for v in known[field]]
            match = sum(int(a == b) for a, b in zip(ours, theirs))
            print(f"  agreement with our own results_posted flag: {match}/{len(known)} "
                  f"({_pct(match, len(known))})")
            print("  Disagreement here is worth reading BEFORE the audit: the two are")
            print("  derived differently (posting DATES vs AACT's own flag), so a gap is a")
            print("  definition difference to resolve, not noise to average over.")

    print(_rule("R7 REMINDER"))
    print("  These fields are LABEL INPUTS and must be excluded from every feature matrix:")
    print("    " + ", ".join(LABEL_DERIVED_FIELDS))
    print("  The FDAAA components above are NOT on that list and may become features.")
    print("  This is still only a printed reminder -- there is no feature builder yet to")
    print("  assert against the constant. Build the assertion as code in Step 6.5.")
    print(f"  alpha used for the p-value rule: {alpha:g} (a parameter, not a constant --")
    print("  group-sequential designs and alpha-splitting across primaries use others).")
    print(f"  population scope: {population}")


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--population", choices=["interventional", "all"],
                    default="interventional",
                    help="which trials form the population. 'interventional' uses the "
                         "tested predicate in services/population.py; 'all' keeps "
                         "observational and expanded-access trials too (they have no "
                         "primary-endpoint analysis to label, so this is a diagnostic).")
    ap.add_argument("--ids", nargs="*", default=None,
                    help="explicit nct_ids for a spot check; overrides --population")
    ap.add_argument("--max-trials", type=int, default=0,
                    help="cap the pull for a smoke run (0 = no cap). Takes the first N "
                         "ids in nct_id order, which correlates with era -- so a capped "
                         "run's rates are not the population's.")
    ap.add_argument("--out", default=Path("data/aact/trial_labels.csv"), type=Path)
    ap.add_argument("--raw-prefix", default=Path("data/aact/results_raw"), type=Path,
                    help="write raw pulled rows here so labels can be re-derived offline")
    ap.add_argument("--from-raw", default=None, type=Path,
                    help="re-derive from a previous --raw-prefix dump, no DB needed")
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--broad-includes-safety", action="store_true",
                    help="fold safety terminations into the broad label as 0 (off by default)")
    ap.add_argument("--era-fallback", dest="era_fallback", action="store_true",
                    default=True,
                    help="when primary_completion_date is absent, bin the regulatory era "
                         "on completion_date instead, recording era_date_source per row "
                         "(default: on)")
    ap.add_argument("--no-era-fallback", dest="era_fallback", action="store_false",
                    help="leave the era unknown when primary_completion_date is absent")
    ap.add_argument("--host", default="aact-db.ctti-clinicaltrials.org")
    ap.add_argument("--db", default="aact")
    ap.add_argument("--schema", default="ctgov")
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--chunk", type=int, default=2000)
    ap.add_argument("--probe-only", action="store_true", help="schema check, then stop")
    ap.add_argument("--resume", action="store_true",
                    help="append to an existing --out instead of overwriting, skipping "
                         "nct_ids already present. Refuses unless the saved manifest's "
                         "settings match this run exactly, because appending rows "
                         "labelled under different settings yields one file holding two "
                         "definitions and nothing downstream could detect it.")
    ap.add_argument("--force-resume", action="store_true",
                    help="resume even when the settings conflict. The resulting file "
                         "will contain rows labelled under DIFFERENT settings. Only use "
                         "this if mixing them is your stated intent.")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print(f"!! refusing an unsafe schema name: {args.schema!r}")
        return 2
    if args.chunk < 1:
        print(f"!! --chunk must be >= 1, got {args.chunk}")
        return 2

    print(_rule("AACT RESULTS PULL -> PER-TRIAL ENDPOINT-MET LABEL (full population)"))
    print_tier_legend()
    print_configuration(args)

    # ---- offline path: re-derive from a saved dump -------------------------
    if args.from_raw:
        s_path = Path(f"{args.from_raw}_studies.csv")
        o_path = Path(f"{args.from_raw}_outcomes.csv")
        if not (s_path.exists() and o_path.exists()):
            print(f"!! need both {s_path} and {o_path}")
            return 2
        # keep_default_na=False is NOT optional here. pandas' default na_values list
        # contains the literal string "NA", so it silently converts AACT's explicit
        # not-applicable phase into NaN -- destroying the not_applicable-vs-absent
        # distinction BEFORE any tested code sees the value, and making this offline
        # re-derive disagree with the live pull (psycopg2 returns a real "NA" string).
        # With it off, blanks arrive as "" and all blank-handling stays in _scalar/_text,
        # which are tested. This is the same family of bug as pandas-NaN-being-truthy.
        studies = pd.read_csv(s_path, low_memory=False,
                              keep_default_na=False).to_dict("records")
        orows = pd.read_csv(o_path, low_memory=False,
                            keep_default_na=False).to_dict("records")
        print(f"\n  offline re-derive: {len(studies)} studies, "
              f"{len(orows)} outcome/analysis rows")
        records = derive(studies, orows, args.alpha, args.broad_includes_safety,
                         args.era_fallback)
        writer = StreamWriter(args.out, OUT_COLS)
        writer.write(records)
        writer.close()
        audit(records, args.alpha, "from-raw", capped=False,
              era_fallback=args.era_fallback)
        print(f"\n  wrote {writer.n_written} rows -> {args.out}")
        return 0

    if not args.user or not args.password:
        print("\n!! set AACT_USER / AACT_PASSWORD (free account at aact.ctti-clinicaltrials.org).")
        print("   Note: this is the Postgres mirror, NOT a ClinicalTrials.gov API key --")
        print("   the v2 API does not expose outcome_analyses.")
        print("   scripts\\check_aact_connection.py diagnoses failures layer by layer.")
        return 2

    import psycopg2
    conn = psycopg2.connect(host=args.host, port=5432, dbname=args.db,
                            user=args.user, password=args.password)
    label_writer = None
    raw_studies_writer = None
    raw_outcomes_writer = None
    records: list[dict] = []
    try:
        with conn.cursor() as c:
            # schema name validated above, so direct interpolation is safe here
            c.execute(f"SET search_path TO {args.schema}, public;")

        print(_rule(f"SCHEMA PROBE (schema '{args.schema}')"))
        found = probe_schema(conn, args.schema)
        for t, cols in found.items():
            if not cols:
                print(f"  !! table not visible: {t}")
        s_cols, s_missing = resolve_columns(found["studies"], STUDIES_WANT,
                                            STUDIES_NEED, "studies")
        c_cols, _c_missing = resolve_columns(found["calculated_values"], CALC_WANT,
                                            CALC_NEED, "calculated_values")
        o_cols, o_missing = resolve_columns(found["outcomes"], OUTCOMES_WANT,
                                           OUTCOMES_NEED, "outcomes")
        a_cols, a_missing = resolve_columns(found["outcome_analyses"], ANALYSES_WANT,
                                           ANALYSES_NEED, "outcome_analyses")
        blocking = s_missing + o_missing + a_missing
        if blocking:
            print(f"\n!! required columns absent: {', '.join(blocking)}")
            print("   The AACT schema has moved. Paste this probe output and the query")
            print("   will be adjusted rather than guessed at.")
            return 3
        print("  probe OK: every required column is present.")
        report_component_availability(s_cols, c_cols)
        if args.probe_only:
            print("\n  --probe-only: stopping before the pull.")
            return 0

        if args.ids:
            ids = sorted({s.strip().upper() for s in args.ids if s.strip()})
            print(_rule("POPULATION (explicit --ids; --population ignored)"))
            print(f"  ids given: {len(ids)}")
        else:
            ids, _tally = fetch_population(conn, args.schema, args.population,
                                           args.max_trials)
        if not ids:
            print("!! no ids selected. Nothing to pull.")
            return 2

        # ---- resume gate ---------------------------------------------------
        n_prior = 0          # rows already in --out from an earlier run
        current_manifest = build_manifest({
            "population": args.population,
            "schema": args.schema,
            "alpha": args.alpha,
            "broad_includes_safety": args.broad_includes_safety,
            "era_fallback": args.era_fallback,
        })
        if args.resume:
            print(_rule("RESUME"))
            saved = load_manifest(args.out)
            conflicts = manifest_conflicts(saved, current_manifest)
            if conflicts and not args.force_resume:
                print(f"  !! refusing to resume {args.out}: settings do not match the")
                print("     run that produced it. Appending would leave one file holding")
                print("     rows labelled under two different definitions, which nothing")
                print("     downstream could detect.")
                for line in describe_conflicts(conflicts):
                    print(f"       {line}")
                if saved is None:
                    print("     No manifest was found. Either this file came from a run")
                    print("     predating manifests, or it was written elsewhere. Re-run")
                    print("     without --resume to rebuild it cleanly.")
                print("     Override with --force-resume only if mixing settings is your")
                print("     stated intent.")
                return 4
            if conflicts:
                print("  !! --force-resume: proceeding DESPITE these conflicts. The output")
                print("     will contain rows labelled under different settings:")
                for line in describe_conflicts(conflicts):
                    print(f"       {line}")
            else:
                print("  settings match the existing run's manifest.")
            done = read_done_ids(args.out)
            n_prior = len(done)
            before = len(ids)
            ids = ids_remaining(ids, done)
            print(f"  already in {args.out}: {len(done)}")
            print(f"  ids remaining to pull    : {len(ids)} of {before}")
            if not ids:
                print("  nothing left to pull. Re-deriving the audit from the raw dumps")
                print("  is the way to re-read the numbers: --from-raw")
                return 0

        studies_sql, outcomes_sql = build_sql(args.schema, s_cols, c_cols, o_cols, a_cols)
        # Append only when resuming; otherwise every writer truncates, so a fresh run can
        # never silently inherit rows from a previous one.
        label_writer = StreamWriter(args.out, OUT_COLS, append=args.resume)
        raw_studies_writer = StreamWriter(Path(f"{args.raw_prefix}_studies.csv"),
                                          s_cols + [c for c in c_cols if c != "nct_id"],
                                          append=args.resume)
        raw_outcomes_writer = StreamWriter(
            Path(f"{args.raw_prefix}_outcomes.csv"),
            [f"outcome_{c}" for c in o_cols] + [f"analysis_{c}" for c in a_cols],
            append=args.resume)
        save_manifest(args.out, current_manifest)

        print(_rule("PULLING (derived and written per chunk to bound memory)"))
        n_chunks = (len(ids) + args.chunk - 1) // args.chunk
        for i in range(0, len(ids), args.chunk):
            block = ids[i:i + args.chunk]
            studies = fetch_rows(conn, studies_sql, block)
            orows = fetch_rows(conn, outcomes_sql, block)
            chunk_records = derive(studies, orows, args.alpha,
                                   args.broad_includes_safety, args.era_fallback)
            raw_studies_writer.write(studies)
            raw_outcomes_writer.write(orows)
            label_writer.write(chunk_records)
            records.extend(chunk_records)
            # cumulative, so a resumed run's counter lines up with the original's
            print(f"    chunk {i // args.chunk + 1}/{n_chunks}: "
                  f"{len(studies)} studies, {len(orows)} outcome rows -> "
                  f"{n_prior + label_writer.n_written} labelled so far", flush=True)
    finally:
        conn.close()
        if label_writer is not None:
            label_writer.close()
        if raw_studies_writer is not None:
            raw_studies_writer.close()
        if raw_outcomes_writer is not None:
            raw_outcomes_writer.close()

    print(f"\n  studies matched: {len(records)}/{len(ids)} "
          f"({_pct(len(records), len(ids))})")
    print(f"  raw dumps -> {args.raw_prefix}_studies.csv, {args.raw_prefix}_outcomes.csv")
    print("  (re-derive labels offline with --from-raw, no second query needed)")

    audit(records, args.alpha, args.population, capped=bool(args.max_trials),
          era_fallback=args.era_fallback)
    n_new = label_writer.n_written if label_writer is not None else 0
    print(f"\n  wrote {n_new} rows this run -> {args.out}")
    if args.resume:
        print("  (appended; the file also holds the earlier run's rows)")
    print("  NEXT: Step 6.3, the posting-bias audit, reading this file. It decides the")
    print("  applicability domain and the reweighting. No modelling before that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())