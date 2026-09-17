#!/usr/bin/env python3
"""Validate reconstructed two-arm comparisons against sponsor verdicts. Decides step 3.

THE QUESTION
============
The gap audit found 887 multi-arm trials that posted primary outcomes and withheld the
analysis. Their comparisons are recomputable from the per-group values AACT does carry.
Before trusting that on the 887, measure it where the answer is already known: the ~1,326
trials that posted BOTH group measurements and a sponsor analysis.

So this script reconstructs verdicts on the overlap, compares them to the sponsor's own,
and reports agreement. If reconstruction disagrees with sponsors often, or disagrees
asymmetrically, the method cannot be used to manufacture labels and the 887 stay tier D.

WHAT WOULD MAKE IT USABLE
=========================
Read three things off the output, in this order:
  1. COVERAGE  -- what share of sponsor-verdict outcomes can be reconstructed at all.
                  Low coverage means the method cannot reach the 887 either.
  2. AGREEMENT -- raw agreement AND the 2x2 confusion matrix. Raw agreement is inflated
                  by the 69% positive base rate, so a coin weighted to "met" scores ~57%.
                  The matrix is the real evidence.
  3. SYMMETRY  -- whether disagreements run both ways. One-directional disagreement means
                  a systematic bias, which is disqualifying even at high raw agreement,
                  because it would shift the base rate of every label it produces.
Exactly one threshold exists -- the disagreement-skew ratio that triggers the warning in
section 3 -- and it is a CLI flag (--skew-ratio) printed with its value, not a constant
buried in the code. No pass/fail verdict is issued. The numbers get read and the decision
gets made explicitly, because "the script said 87%" is not a reason.

WHY DISAGREEMENT IS EXPECTED
============================
The reconstruction uses no pre-specified model, no covariate adjustment, no
stratification, no ITT/PP distinction, no multiplicity or interim adjustment. A protocol
ANCOVA on a stratified population is a different test from a Welch t on two posted
summaries. Some disagreement is correct behaviour, not error. The question is how much
and in which direction.

Subgroup rows are excluded: only measurements with blank category AND blank
classification are used, because those columns hold subgroup and timepoint strata and
mixing them would compare a subgroup of one arm against the whole of another.

Access: env AACT_USER / AACT_PASSWORD. Deps: pandas, psycopg2-binary.
Usage (PowerShell):
  python scripts\\validate_reconstruction.py --probe-only
  python scripts\\validate_reconstruction.py
  python scripts\\validate_reconstruction.py --include-gap    # also size the 887
  python scripts\\validate_reconstruction.py --from-raw data\\aact\\recon_raw
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Make src/ importable without depending on PYTHONPATH being set in the session.
# `pip install -e .` is the durable fix and makes this a no-op; this guard means the
# script still runs in a fresh shell where nobody remembered to export anything.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from trial_pos.services.endpoint_label import (
    DEFAULT_ALPHA, HEADLINE_TIERS, aggregate_outcome,
)
from trial_pos.services.recon_stats import measurement_kind, reconstruct

MEAS_WANT = ["nct_id", "outcome_id", "result_group_id", "category", "classification",
             "param_type", "param_value", "param_value_num", "dispersion_type",
             "dispersion_value", "dispersion_value_num", "units"]
MEAS_NEED = ["nct_id", "outcome_id", "result_group_id", "param_type"]
COUNT_WANT = ["nct_id", "outcome_id", "result_group_id", "scope", "units", "count"]
COUNT_NEED = ["outcome_id", "result_group_id", "count"]
GROUP_WANT = ["id", "nct_id", "ctgov_group_code", "result_type", "title"]
GROUP_NEED = ["id", "nct_id"]
# Sponsor analyses are pulled HERE, in the same session as the measurements, rather than
# read from pull_aact_results.py's dump.
#
# AACT REGENERATES ITS SURROGATE KEYS ON EVERY NIGHTLY REBUILD. The first live run
# compared outcomes.id from a dump taken days earlier (2626xxxxx) against today's
# outcome_measurements.outcome_id (2647xxxxx) and found zero overlap on 498 shared
# trials. Both were well-formed integers from different id spaces. Cross-snapshot joins
# on AACT ids are silently wrong; one session, one snapshot, one id space.
OUTCOMES_WANT = ["id", "nct_id", "outcome_type", "title"]
ANALYSES_WANT = ["id", "nct_id", "outcome_id", "non_inferiority_type", "param_type",
                 "p_value", "p_value_modifier", "ci_lower_limit", "ci_upper_limit"]


# Arm-title patterns that identify a comparator. Used only to choose WHICH pair to
# compare in a >2-arm outcome; it never affects whether a verdict is reached, so a miss
# costs coverage rather than correctness.
_CONTROL_TITLE_RX = tuple(re.compile(r, re.I) for r in (
    r"\bplacebo\b", r"\bcontrol\b", r"\bsham\b", r"\bvehicle\b",
    r"standard of care", r"\bsoc\b", r"usual care", r"\bcomparator\b",
    r"\bno treatment\b", r"\buntreated\b", r"\bactive control\b",
))


def is_control_arm(title) -> bool:
    return any(rx.search(str(title or "")) for rx in _CONTROL_TITLE_RX)


def _rule(t):
    return f"\n{'=' * 74}\n{t}\n{'=' * 74}"


def _pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def _scalar(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "na", "nat"):
        return None
    return v


def _idstr(v):
    """Canonical string for a database id.

    The two sides of the join arrive from different places: measurements come from
    Postgres as ints, sponsor verdicts come from a CSV that pandas may type as float.
    Without normalising, "12345" never matches "12345.0" and the overlap silently
    collapses to zero -- which is exactly what happened on the first live run.
    """
    v = _scalar(v)
    if v is None:
        return None
    s = str(v).strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


def _num(v):
    v = _scalar(v)
    if v is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(v))
    try:
        return float(m.group(0)) if m else None
    except ValueError:
        return None


# ---- pulls ---------------------------------------------------------------
def resolve(found: set[str], want: list[str], need: list[str], table: str):
    have = [c for c in want if c in found]
    absent = [c for c in want if c not in found]
    missing = [c for c in need if c not in found]
    print(f"  {table}: {len(have)}/{len(want)} columns present"
          + (f"; absent -> {', '.join(absent)}" if absent else ""))
    return have, missing


def fetch(conn, sql, ids, chunk, label):
    from psycopg2.extras import RealDictCursor
    rows = []
    with conn.cursor(cursor_factory=RealDictCursor) as c:
        for i in range(0, len(ids), chunk):
            c.execute(sql, {"ids": ids[i:i + chunk]})
            rows.extend(dict(r) for r in c.fetchall())
            print(f"    {label}: {min(i + chunk, len(ids))}/{len(ids)} ids, "
                  f"{len(rows)} rows", flush=True)
    return rows


# ---- assembly ------------------------------------------------------------
def build_groups(meas_rows, count_rows):
    """-> {(nct_id, outcome_id): {result_group_id: {value, dispersion, n, param_type}}}

    Only overall rows are kept: blank category and blank classification. Those columns
    carry subgroup and timepoint strata, and mixing strata across arms would compare a
    subgroup of one arm against the whole of another.
    """
    counts: dict[tuple, float] = {}
    for r in count_rows:
        oid, gid = _idstr(r.get("outcome_id")), _idstr(r.get("result_group_id"))
        n = _num(r.get("count"))
        if oid is None or gid is None or n is None:
            continue
        key = (oid, gid)
        counts[key] = max(counts.get(key, 0.0), n)   # largest posted N for that arm

    out: dict[tuple, dict] = defaultdict(dict)
    skipped_strata = 0
    for r in meas_rows:
        if _scalar(r.get("category")) is not None or \
                _scalar(r.get("classification")) is not None:
            skipped_strata += 1
            continue
        nct, oid = _scalar(r.get("nct_id")), _idstr(r.get("outcome_id"))
        gid = _idstr(r.get("result_group_id"))
        if not nct or oid is None or gid is None:
            continue
        val = _num(r.get("param_value_num"))
        if val is None:
            val = _num(r.get("param_value"))
        disp = _num(r.get("dispersion_value_num"))
        if disp is None:
            disp = _num(r.get("dispersion_value"))
        out[(str(nct).upper(), oid)][gid] = {
            "value": val,
            "dispersion_value": disp,
            "dispersion_type": _scalar(r.get("dispersion_type")),
            "n": counts.get((oid, gid)),
            "param_type": _scalar(r.get("param_type")),
        }
    return out, skipped_strata


def sponsor_verdicts(analysis_rows: list[dict], alpha: float):
    """-> {(nct_id, outcome_id): {'met', 'tier'}} from analyses pulled THIS session.

    Uses the same endpoint_label engine as the label build, so the comparison is against
    the project's real verdicts rather than a reimplementation.
    """
    by_outcome = defaultdict(list)
    for r in analysis_rows:
        nct, oid = _scalar(r.get("nct_id")), _idstr(r.get("outcome_id"))
        if not nct or oid is None:
            continue
        a = {k: _scalar(v) for k, v in r.items()}
        if a.get("p_value") is not None or a.get("ci_lower_limit") is not None:
            by_outcome[(str(nct).upper(), oid)].append(a)
    verdicts = {}
    for key, analyses in by_outcome.items():
        v = aggregate_outcome(analyses, alpha)
        if v["met"] is not None:
            verdicts[key] = {"met": v["met"], "tier": v["tier"]}
    return verdicts


def choose_pair(by_group: dict, titles: dict, multiarm: str):
    """-> (experimental, control, pairing_label, reason).

    Two arms: compare them directly, ordering experimental-first when a comparator can be
    identified so the recorded direction means something.

    More than two arms: with --multiarm strict, refuse -- there is no unambiguous
    pairwise comparison and picking one silently would be a choice hidden from the
    reader. With --multiarm control, pair the single comparator arm against the largest
    non-comparator arm, and tag the result so agreement can be reported separately for
    this subset rather than pooled with the direct pairs.
    """
    gids = list(by_group)
    ctrl = [g for g in gids if is_control_arm(titles.get(g))]
    if len(gids) == 2:
        if len(ctrl) == 1:
            c = ctrl[0]
            e = next(g for g in gids if g != c)
            return by_group[e], by_group[c], "direct_control_identified", ""
        return by_group[gids[0]], by_group[gids[1]], "direct_unordered", ""
    if multiarm != "control":
        return None, None, "", ("more than 2 arms -- refused "
                                "(--multiarm control pairs against the comparator)")
    if len(ctrl) != 1:
        return None, None, "", (f">2 arms and {len(ctrl)} comparator arms identified "
                                f"-- no unambiguous pair")
    c = ctrl[0]
    others = [g for g in gids if g != c]
    e = max(others, key=lambda g: by_group[g].get("n") or 0)
    return by_group[e], by_group[c], "multiarm_control_paired", ""


def run_reconstruction(groups, alpha, titles=None, multiarm="control"):
    """-> {(nct,oid): result}, plus a tally of why rows were unusable."""
    titles = titles or {}
    results, why = {}, Counter()
    for key, by_group in groups.items():
        if len(by_group) < 2:
            why["fewer than 2 arms posted (single-arm or one group only)"] += 1
            continue
        ga, gb, pairing, reason = choose_pair(by_group, titles, multiarm)
        if ga is None:
            why[reason] += 1
            continue
        pt = ga.get("param_type") or gb.get("param_type")
        if measurement_kind(pt) is None:
            why[f"param_type not testable: {pt}"] += 1
            continue
        res = reconstruct(ga, gb, pt, alpha)
        if res["met"] is None:
            why[res["reason"] or "inputs insufficient"] += 1
            continue
        res["param_type"] = pt
        res["pairing"] = pairing
        results[key] = res
    return results, why


# ---- audit ---------------------------------------------------------------
def audit(verdicts, recon, groups, skipped_strata, why, alpha,
          skew_ratio: float, min_bucket: int):
    print(_rule("1. COVERAGE -- can the method reach these outcomes at all?"))
    print(f"  primary outcomes with group measurements : {len(groups)}")
    print(f"  subgroup/timepoint rows excluded         : {skipped_strata}")
    print(f"  outcomes with a SPONSOR verdict          : {len(verdicts)}")
    print(f"  outcomes RECONSTRUCTED                   : {len(recon)}")
    overlap = set(verdicts) & set(recon)
    print(f"  overlap (both available)                 : {len(overlap)}")
    print(f"  reconstruction coverage of sponsor set   : "
          f"{_pct(len(overlap), len(verdicts))}")

    # Key diagnostic. A zero or near-zero overlap on trials that appear in both sets is
    # almost always a key-format mismatch, not a real finding, so make it self-evident
    # instead of leaving it to be inferred.
    ncts_v = {k[0] for k in verdicts}
    ncts_r = {k[0] for k in recon}
    print(f"  trials in sponsor set / recon set        : {len(ncts_v)} / {len(ncts_r)}")
    print(f"  trials in BOTH                           : {len(ncts_v & ncts_r)}")
    if ncts_v & ncts_r and len(overlap) < 0.05 * min(len(verdicts), len(recon)):
        print("  !! trials overlap but outcome keys do not -- this is a KEY MISMATCH,")
        print("     not a result. Sample keys from each side:")
        for label, keys in (("sponsor", verdicts), ("recon", recon)):
            for k in list(keys)[:3]:
                print(f"       {label:8s} nct={k[0]!r} outcome_id={k[1]!r}")
        print("     Compare the outcome_id forms above before reading anything below.")
    if why:
        print("\n  why outcomes were NOT reconstructed (top reasons):")
        for reason, n in why.most_common(12):
            print(f"    {n:6d}  {reason}")

    if not overlap:
        print("\n  !! no overlap -- nothing to validate. Stop here.")
        return

    print(_rule("2. AGREEMENT -- the 2x2 is the evidence, not the headline rate"))
    cm = Counter()
    for key in overlap:
        cm[(verdicts[key]["met"], recon[key]["met"])] += 1
    n = sum(cm.values())
    agree = cm[(1, 1)] + cm[(0, 0)]
    print(f"                        recon: not met    recon: met")
    print(f"    sponsor: not met      {cm[(0, 0)]:8d}      {cm[(0, 1)]:8d}")
    print(f"    sponsor: met          {cm[(1, 0)]:8d}      {cm[(1, 1)]:8d}")
    print(f"\n  raw agreement            : {agree}/{n} ({_pct(agree, n)})")
    spon_pos = (cm[(1, 0)] + cm[(1, 1)]) / n
    rec_pos = (cm[(0, 1)] + cm[(1, 1)]) / n
    chance = spon_pos * rec_pos + (1 - spon_pos) * (1 - rec_pos)
    kappa = (agree / n - chance) / (1 - chance) if chance < 1 else float("nan")
    print(f"  agreement expected by chance: {_pct(chance * n, n)}  "
          f"(sponsor met {_pct(spon_pos * n, n)}, recon met {_pct(rec_pos * n, n)})")
    print(f"  Cohen's kappa            : {kappa:.3f}")
    print("  kappa is the number to quote: raw agreement is inflated by the positive")
    print("  base rate, and a constant 'met' guess would already score highly.")
    if cm[(1, 0)] + cm[(1, 1)]:
        print(f"  sensitivity (recon met | sponsor met)     : "
              f"{_pct(cm[(1, 1)], cm[(1, 0)] + cm[(1, 1)])}")
    if cm[(0, 0)] + cm[(0, 1)]:
        print(f"  specificity (recon not met | sponsor not) : "
              f"{_pct(cm[(0, 0)], cm[(0, 0)] + cm[(0, 1)])}")

    print(_rule("3. SYMMETRY -- one-directional error is disqualifying"))
    fp, fn = cm[(0, 1)], cm[(1, 0)]
    print(f"  recon says met, sponsor says not : {fp}")
    print(f"  recon says not, sponsor says met : {fn}")
    if fp + fn:
        print(f"  skew toward 'met'                : {_pct(fp, fp + fn)} of disagreements")
        print(f"  warning triggers above           : {skew_ratio:g}:1 (--skew-ratio)")
        if max(fp, fn) > skew_ratio * max(min(fp, fn), 1):
            print("  !! strongly one-directional. Reconstructed labels would shift the")
            print("     base rate of every trial they cover. Do not proceed on this alone.")
        else:
            print("  disagreement runs both ways, which is what a noisy-but-unbiased")
            print("  approximation looks like.")

    print(_rule("BREAKDOWNS -- where the method works and where it does not"))
    for name, keyfn in (
        ("measurement kind", lambda k: recon[k]["kind"]),
        ("sponsor tier", lambda k: verdicts[k]["tier"]),
        ("small expected cell", lambda k: "flagged" if recon[k]["small_cell"] else "ok"),
        ("pairing", lambda k: recon[k].get("pairing", "?")),
    ):
        buckets = defaultdict(lambda: [0, 0])
        for k in overlap:
            b = buckets[keyfn(k)]
            b[0] += 1
            b[1] += int(verdicts[k]["met"] == recon[k]["met"])
        print(f"\n  by {name}:")
        for b, (tot, ag) in sorted(buckets.items(), key=lambda kv: -kv[1][0]):
            print(f"    {str(b)[:34]:34s} n={tot:6d}  agreement={_pct(ag, tot)}")

    sizes = sorted((recon[k]["n_total"] or 0) for k in overlap)
    if sizes:
        cuts = [sizes[len(sizes) // 3], sizes[2 * len(sizes) // 3]]
        buckets = defaultdict(lambda: [0, 0])
        for k in overlap:
            nt = recon[k]["n_total"] or 0
            lab = f"<={cuts[0]:.0f}" if nt <= cuts[0] else (
                f"{cuts[0]:.0f}-{cuts[1]:.0f}" if nt <= cuts[1] else f">{cuts[1]:.0f}")
            b = buckets[lab]
            b[0] += 1
            b[1] += int(verdicts[k]["met"] == recon[k]["met"])
        print("\n  by total N (tertiles):")
        for b, (tot, ag) in buckets.items():
            print(f"    {b:34s} n={tot:6d}  agreement={_pct(ag, tot)}")

    print(f"\n  worst-agreeing param_types (n>={min_bucket}):")
    pts = defaultdict(lambda: [0, 0])
    for k in overlap:
        b = pts[str(recon[k]["param_type"])]
        b[0] += 1
        b[1] += int(verdicts[k]["met"] == recon[k]["met"])
    ranked = [(pt, t, a) for pt, (t, a) in pts.items() if t >= min_bucket]
    for pt, t, a in sorted(ranked, key=lambda x: x[2] / x[1])[:8]:
        print(f"    {pt[:34]:34s} n={t:6d}  agreement={_pct(a, t)}")


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=Path("data/aact/trial_labels.csv"), type=Path)
    ap.add_argument("--raw-prefix", default=Path("data/aact/recon_raw"), type=Path)
    ap.add_argument("--from-raw", default=None, type=Path)
    ap.add_argument("--include-gap", action="store_true",
                    help="also size how many of the no-analysis trials are reachable")
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    ap.add_argument("--multiarm", choices=["strict", "control"], default="control",
                    help="how to handle outcomes with >2 arms: refuse, or pair the "
                         "comparator arm against the largest other arm (reported "
                         "separately in the breakdowns)")
    ap.add_argument("--skew-ratio", type=float, default=3.0,
                    help="warn when disagreements run this many times more in one "
                         "direction than the other (section 3). Not a pass/fail test.")
    ap.add_argument("--min-bucket", type=int, default=5,
                    help="minimum n for a param_type to appear in the breakdown")
    ap.add_argument("--host", default="aact-db.ctti-clinicaltrials.org")
    ap.add_argument("--db", default="aact")
    ap.add_argument("--schema", default="ctgov")
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--chunk", type=int, default=1500)
    ap.add_argument("--probe-only", action="store_true")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print(f"!! unsafe schema name {args.schema!r}")
        return 2

    print(_rule("RECONSTRUCTION VALIDATION (overlap where sponsor verdicts exist)"))
    if not args.labels.exists():
        print(f"!! need {args.labels} -- run pull_aact_results.py first")
        return 2
    lab = pd.read_csv(args.labels)
    have_verdict = lab[lab["endpoint_met_strict"].notna()]
    gap = lab[(lab["endpoint_met_strict"].isna()) & (lab["n_primary_outcomes"] > 0)]
    val_ids = sorted({str(x).upper() for x in have_verdict["nct_id"].dropna()})
    gap_ids = sorted({str(x).upper() for x in gap["nct_id"].dropna()})
    print(f"  validation set (sponsor verdict exists) : {len(val_ids)} trials")
    print(f"  gap set (outcomes posted, no verdict)   : {len(gap_ids)} trials")
    ids = val_ids + (gap_ids if args.include_gap else [])

    if args.from_raw:
        meas = pd.read_csv(f"{args.from_raw}_measurements.csv", low_memory=False)
        cnts = pd.read_csv(f"{args.from_raw}_counts.csv", low_memory=False)
        gpath = Path(f"{args.from_raw}_groups.csv")
        grps = pd.read_csv(gpath, low_memory=False) if gpath.exists() else None
        meas_rows, count_rows = meas.to_dict("records"), cnts.to_dict("records")
        group_rows = grps.to_dict("records") if grps is not None else []
        apath = Path(f"{args.from_raw}_analyses.csv")
        if not apath.exists():
            print(f"\n!! {apath} is absent. Earlier dumps predate the snapshot fix and")
            print("   their ids are NOT joinable with the measurements. Re-run without")
            print("   --from-raw so all four tables come from one snapshot.")
            return 2
        analysis_rows = pd.read_csv(apath, low_memory=False).to_dict("records")
        print(f"\n  offline: {len(meas_rows)} measurement rows, {len(count_rows)} "
              f"counts, {len(group_rows)} groups")
    else:
        if not args.user or not args.password:
            print("\n!! set AACT_USER / AACT_PASSWORD "
                  "(scripts\\check_aact_connection.py diagnoses failures)")
            return 2
        import psycopg2
        conn = psycopg2.connect(host=args.host, port=5432, dbname=args.db,
                                user=args.user, password=args.password)
        try:
            print(_rule(f"SCHEMA PROBE (schema '{args.schema}')"))
            tables = ["outcome_measurements", "outcome_counts", "result_groups",
                      "outcomes"]
            found = {t: set() for t in tables}
            with conn.cursor() as c:
                c.execute("SELECT table_name, column_name FROM information_schema.columns"
                          " WHERE table_schema = %s AND table_name = ANY(%s)",
                          (args.schema, tables))
                for t, col in c.fetchall():
                    found[t].add(col)
            m_cols, m_missing = resolve(found["outcome_measurements"], MEAS_WANT,
                                       MEAS_NEED, "outcome_measurements")
            c_cols, c_missing = resolve(found["outcome_counts"], COUNT_WANT,
                                        COUNT_NEED, "outcome_counts")
            resolve(found["result_groups"], GROUP_WANT, GROUP_NEED, "result_groups")
            if m_missing or c_missing:
                print(f"\n!! required columns absent: {m_missing + c_missing}")
                return 3
            print("  probe OK.")
            if args.probe_only:
                return 0
            sch = args.schema
            meas_sql = (
                f"SELECT {', '.join('om.' + c for c in m_cols)} "
                f"FROM {sch}.outcome_measurements om "
                f"JOIN {sch}.outcomes o ON o.id = om.outcome_id "
                f"WHERE om.nct_id = ANY(%(ids)s) "
                f"AND lower(o.outcome_type) = 'primary';")
            ana_sql = (
                f"SELECT {', '.join('oa.' + c for c in ANALYSES_WANT if c in found['outcome_analyses'])} "
                f"FROM {sch}.outcome_analyses oa "
                f"JOIN {sch}.outcomes o ON o.id = oa.outcome_id "
                f"WHERE oa.nct_id = ANY(%(ids)s) "
                f"AND lower(o.outcome_type) = 'primary';")
            grp_sql = (
                f"SELECT {', '.join('rg.' + c for c in GROUP_WANT if c in found['result_groups'])} "
                f"FROM {sch}.result_groups rg "
                f"WHERE rg.nct_id = ANY(%(ids)s);")
            cnt_sql = (
                f"SELECT {', '.join('oc.' + c for c in c_cols)} "
                f"FROM {sch}.outcome_counts oc "
                f"JOIN {sch}.outcomes o ON o.id = oc.outcome_id "
                f"WHERE oc.nct_id = ANY(%(ids)s) "
                f"AND lower(o.outcome_type) = 'primary';")
            print(_rule("PULLING"))
            meas_rows = fetch(conn, meas_sql, ids, args.chunk, "measurements")
            count_rows = fetch(conn, cnt_sql, ids, args.chunk, "counts")
            group_rows = fetch(conn, grp_sql, ids, args.chunk, "result groups")
            analysis_rows = fetch(conn, ana_sql, ids, args.chunk, "sponsor analyses")
        finally:
            conn.close()
        args.raw_prefix.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(meas_rows).to_csv(f"{args.raw_prefix}_measurements.csv", index=False)
        pd.DataFrame(count_rows).to_csv(f"{args.raw_prefix}_counts.csv", index=False)
        pd.DataFrame(group_rows).to_csv(f"{args.raw_prefix}_groups.csv", index=False)
        pd.DataFrame(analysis_rows).to_csv(f"{args.raw_prefix}_analyses.csv", index=False)
        print(f"  raw dumps -> {args.raw_prefix}_measurements.csv, _counts.csv, "
              f"_groups.csv, _analyses.csv")
        print("  all four come from ONE snapshot, so their ids are mutually joinable")

    titles = {}
    for r in group_rows:
        gid = _idstr(r.get("id"))
        if gid is not None:
            titles[gid] = _scalar(r.get("title")) or ""
    groups, skipped = build_groups(meas_rows, count_rows)
    verdicts = sponsor_verdicts(analysis_rows, args.alpha)
    recon, why = run_reconstruction(groups, args.alpha, titles, args.multiarm)
    print(f"\n  arm titles available for {len(titles)} result groups; "
          f"multiarm strategy = {args.multiarm}")
    audit(verdicts, recon, groups, skipped, why, args.alpha,
          args.skew_ratio, args.min_bucket)

    if args.include_gap:
        gap_set = set(gap_ids)
        reachable = {k for k in recon if k[0] in gap_set}
        trials = {k[0] for k in reachable}
        print(_rule("POTENTIAL GAIN on the no-verdict trials"))
        print(f"  gap trials queried        : {len(gap_ids)}")
        print(f"  outcomes reconstructed    : {len(reachable)}")
        print(f"  TRIALS newly labelable    : {len(trials)} "
              f"({_pct(len(trials), len(gap_ids))} of the gap)")
        print("  Read this ONLY after the agreement numbers above justify it. A large")
        print("  reachable count is not an argument for a method that disagrees with")
        print("  sponsors, and these labels would be a reconstructed tier either way,")
        print(f"  excluded from headline tiers {HEADLINE_TIERS}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())