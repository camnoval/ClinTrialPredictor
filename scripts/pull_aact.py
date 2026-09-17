#!/usr/bin/env python3
"""Pull DSAI's trial-level pipeline features from AACT (open ClinicalTrials.gov Postgres).

Step 2 of the DSAI reconstruction (see docs/DSAI_Reconstruction_Plan.md). Reconstructs the
open-data analogs of their Trialtrove-derived trial features: trial DESIGN flags, SPONSOR
class, TERMINATION-reason score, ACCRUAL/enrollment, phase and dates (phaseendyear). Keyed by
nct_id for the TOP + CTO trials we score.

Honesty: AACT's design/sponsor fields are coarser than Trialtrove's curated keywords, so
these are *approximations* of DSAI's originals, flagged as such (R11-style divergence). The
script is audit-first: it reports match rate and per-feature non-null coverage so we see how
much of each family AACT actually fills before wiring it into the model.

Access: free AACT account -> credentials. Set env AACT_USER / AACT_PASSWORD (or pass --user
--password). Host aact-db.ctti-clinicaltrials.org:5432, db 'aact'.
Deps: pip install psycopg2-binary pandas
Usage:
  setx AACT_USER you && setx AACT_PASSWORD pw          # (new shell after)
  python scripts/pull_aact.py --units data/chembl/units.csv --cto data/cto/cto_trials.csv
"""
from __future__ import annotations
import argparse
import os
import re
from pathlib import Path

# ---- pure DSAI-style derivations (offline-testable) ------------------------
def termination_score(why_stopped):
    """DSAI terminScore: 0 good -> 4/5 bad, + safety flag. NA (still running/none) -> mild 1."""
    if not why_stopped or str(why_stopped).strip() == "":
        return 1, 0
    w = str(why_stopped).lower()
    safety = 1 if "adverse" in w else 0
    if any(k in w for k in ("lack of eff", "negative outcome", "adverse", "priorit",
                            "efficacy", "did not meet", "futility")):
        s = 4
    elif any(k in w for k in ("business", "fund", "sponsor decision", "strateg")):
        s = 3
    elif any(k in w for k in ("terminated, other", "indetermin", "enroll", "accru",
                              "recruit", "covid")):
        s = 2
    elif "positive outcome" in w or "met " in w:
        s = 0
    else:
        s = 5
    return s, safety


def design_flags(allocation, masking, intervention_model, primary_purpose):
    """Open-data analog of DSAI design keywords, from AACT `designs`. Approximate."""
    a = (allocation or "").lower()
    m = (masking or "").lower()
    im = (intervention_model or "").lower()
    pp = (primary_purpose or "").lower()
    randomized = 1 if "randomized" in a and "non" not in a else 0
    blinded = 1 if any(k in m for k in ("double", "triple", "quadruple")) else 0
    controlled = 1 if ("parallel" in im or "crossover" in im or blinded) else 0
    efficacy_assessed = 1 if "treatment" in pp else 0
    pk_aspects = 1 if ("pharmacokinet" in pp or "basic science" in pp) else 0
    designscore = randomized + blinded + controlled + efficacy_assessed - pk_aspects \
        - (1 if "single" in im else 0)
    return {"randomized": randomized, "blinded": blinded, "controlled": controlled,
            "efficacy_assessed": efficacy_assessed, "pk_aspects": pk_aspects,
            "designscore": designscore}


def sponsor_dummies(agency_class, name):
    """Open-data analog of DSAI sponsor types, from AACT `sponsors.agency_class`. Coarser."""
    ac = (agency_class or "").lower()
    nm = (name or "").lower()
    return {
        "spons_industry": 1 if "industry" in ac else 0,
        "spons_gov": 1 if ("nih" in ac or "fed" in ac or "u.s." in ac) else 0,
        "spons_academic": 1 if ("other" in ac and any(k in nm for k in
                                ("univ", "college", "hospital", "institut", "center", "school"))) else 0,
        "spons_network": 1 if "network" in ac else 0,
        "spons_indiv": 1 if "indiv" in ac else 0,
    }


def phaseendyear(completion_date, primary_completion_date):
    """Year the Phase-2 readout landed (proxy for DSAI phaseendyear)."""
    for d in (completion_date, primary_completion_date):
        if d:
            m = re.search(r"(\d{4})", str(d))
            if m:
                return int(m.group(1))
    return None


def derive_row(r):
    """AACT joined row (dict) -> flat DSAI-style feature row. Pure."""
    out = {"nct_id": r["nct_id"], "phase": r.get("phase") or "",
           "overall_status": r.get("overall_status") or "",
           "enrollment": r.get("enrollment"), "enrollment_type": r.get("enrollment_type") or "",
           "number_of_arms": r.get("number_of_arms"),
           "start_date": r.get("start_date") or "", "completion_date": r.get("completion_date") or "",
           "phaseendyear": phaseendyear(r.get("completion_date"), r.get("primary_completion_date"))}
    ts, safety = termination_score(r.get("why_stopped"))
    out["termination_score"], out["safety_termination"] = ts, safety
    out.update(design_flags(r.get("allocation"), r.get("masking"),
                            r.get("intervention_model"), r.get("primary_purpose")))
    out.update(sponsor_dummies(r.get("agency_class"), r.get("sponsor_name")))
    # raw source fields kept so we can report TRUE coverage (vs defaulted-to-0 flags)
    out["raw_allocation"] = r.get("allocation") or ""
    out["raw_masking"] = r.get("masking") or ""
    out["raw_intervention_model"] = r.get("intervention_model") or ""
    out["raw_agency_class"] = r.get("agency_class") or ""
    return out


FEATURE_COLS = ["nct_id", "phase", "overall_status", "enrollment", "enrollment_type",
                "number_of_arms", "start_date", "completion_date", "phaseendyear",
                "termination_score", "safety_termination", "randomized", "blinded",
                "controlled", "efficacy_assessed", "pk_aspects", "designscore",
                "spons_industry", "spons_gov", "spons_academic", "spons_network", "spons_indiv",
                "raw_allocation", "raw_masking", "raw_intervention_model", "raw_agency_class"]

QUERY = """
SELECT s.nct_id, s.phase, s.overall_status, s.why_stopped, s.enrollment, s.enrollment_type,
       s.number_of_arms, s.start_date, s.completion_date, s.primary_completion_date,
       d.allocation, d.intervention_model, d.primary_purpose, d.masking,
       sp.agency_class, sp.name AS sponsor_name
FROM studies s
LEFT JOIN designs d ON d.nct_id = s.nct_id
LEFT JOIN sponsors sp ON sp.nct_id = s.nct_id AND sp.lead_or_collaborator = 'lead'
WHERE s.nct_id = ANY(%(ids)s);
"""


def _collect_nctids(units_path, cto_path):
    import pandas as pd
    ids = set()
    if units_path and Path(units_path).exists():
        ids |= {str(x) for x in pd.read_csv(units_path)["nctid"].dropna()}
    if cto_path and Path(cto_path).exists():
        ids |= {str(x) for x in pd.read_csv(cto_path)["nctid"].dropna()}
    return sorted(ids)


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=Path("data/chembl/units.csv"), type=Path)
    ap.add_argument("--cto", default=Path("data/cto/cto_trials.csv"), type=Path)
    ap.add_argument("--out", default=Path("data/aact/aact_features.csv"), type=Path)
    ap.add_argument("--host", default="aact-db.ctti-clinicaltrials.org")
    ap.add_argument("--db", default="aact")
    ap.add_argument("--user", default=os.environ.get("AACT_USER"))
    ap.add_argument("--password", default=os.environ.get("AACT_PASSWORD"))
    ap.add_argument("--chunk", type=int, default=2000)
    args = ap.parse_args()
    if not args.user or not args.password:
        print("!! set AACT_USER / AACT_PASSWORD (free account at aact.ctti-clinicaltrials.org)")
        return 2

    import psycopg2
    from psycopg2.extras import RealDictCursor
    ids = _collect_nctids(args.units, args.cto)
    print(f"{'='*66}\nAACT FEATURE PULL\n{'='*66}\nnct_ids to pull: {len(ids)}", flush=True)

    conn = psycopg2.connect(host=args.host, port=5432, dbname=args.db,
                            user=args.user, password=args.password)
    # fail fast on a tiny query so a schema mismatch surfaces before the big pull
    with conn.cursor() as c:
        c.execute("SELECT nct_id FROM studies LIMIT 1;")
        c.fetchone()

    rows = []
    with conn.cursor(cursor_factory=RealDictCursor) as c:
        for i in range(0, len(ids), args.chunk):
            chunk = ids[i:i + args.chunk]
            c.execute(QUERY, {"ids": chunk})
            rows.extend(derive_row(dict(r)) for r in c.fetchall())
            print(f"  pulled {min(i+args.chunk, len(ids))}/{len(ids)}", flush=True)
    conn.close()

    df = pd.DataFrame(rows).reindex(columns=FEATURE_COLS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    matched = len(df)
    print(f"\nWROTE {matched} rows -> {args.out}  (match rate {100*matched/max(1,len(ids)):.1f}%)")
    print(f"\n{'='*66}\nFEATURE COVERAGE (non-null / non-zero rate)\n{'='*66}")
    for col in FEATURE_COLS[1:]:
        if col.startswith("raw_"):
            continue
        s = df[col]
        if s.dtype.kind in "if":
            filled = s.notna().mean()
        else:
            filled = (s.astype(str).str.len() > 0).mean()
        print(f"  {col:22s}: {100*filled:5.1f}%")

    print(f"\n{'='*66}\nTRUE AACT SOURCE PRESENCE (design/sponsor flags default missing->0)\n{'='*66}")
    for col in ("raw_allocation", "raw_masking", "raw_intervention_model", "raw_agency_class"):
        present = (df[col].astype(str).str.len() > 0).mean()
        print(f"  {col:24s}: {100*present:5.1f}%  <- real coverage of this family")
    print("  if these are low, the design/sponsor flags are mostly 'unknown coded as 0' --")
    print("  we'd add an explicit missing-indicator rather than let 0 mean 'no'.")
    print("\n  phaseendyear feeds the censoring correction + time-respecting MoA-prior LOO.")
    print("  Next: join to units, re-run MoA at full 3-granularity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())