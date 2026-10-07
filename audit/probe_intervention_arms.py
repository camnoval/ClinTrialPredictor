#!/usr/bin/env python3
"""Does trial-to-drug resolution need a new pull? READ-ONLY (read-only transaction). SCRATCH.

`trial_entities.csv` keeps, per trial, the DISTINCT names of every intervention pipe-joined,
so it cannot say which name belongs to which intervention or which arm. Its
`intervention_mesh_terms` is aggregated over every `mesh_type`. This probe reads the
intervention-level tables straight from the local restore and measures what a
tested-agent rule could recover from them. Nothing here is decided; the rule is labelled
CANDIDATE throughout.

  1. schema: the columns this probe needs, per table. Absent ones stop the run.
  2. population: interventional drug trials, derived with population.has_drug_intervention,
     cross-tabbed PAIRED against trial_labels.csv's is_drug_trial when --labels is given
  3. linkage integrity, keyed on the child's id: links to an intervention or arm outside
     the trial, exact duplicate links, other names pointing outside the trial
  4. arm vocabulary: raw group_type spellings, untyped and unrecognised arms
  5. role of every drug/biological intervention, from the types of the arms it is in
  6. trial outcome under the CANDIDATE rule, by phase class and by arm count, with the
     number of candidate agents per trial and how many candidates are placebo-named
  7. other names: coverage per drug intervention
  8. intervention MeSH: raw mesh_type spellings, and whether each indexed term can be
     attributed to a specific intervention by name
  9. optional, --entities: does trial_entities.csv equal what the restore holds, and is its
     MeSH column the indexed terms or every mesh_type pooled?

Sample NCT ids are drawn in salted-hash order (sampling.ordered_by_hash), so they are
spread across the registry and reproducible from the printed salt.

Usage (PowerShell), standalone window, server started with restore_aact_snapshot.py --start:
  python audit\\probe_intervention_arms.py --entities data\\aact\\trial_entities.csv --labels data\\aact\\trial_labels.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trial_pos.services.aact_aggregates import AGG_SEPARATOR  # noqa: E402
from trial_pos.services.aact_fields import GROUP_TYPES, MESH_LIST  # noqa: E402
from trial_pos.services.aact_snapshot import (  # noqa: E402
    LOCAL_DB, LOCAL_HOST, LOCAL_PASSWORD, LOCAL_PORT, LOCAL_USER,
)
from trial_pos.services.eligibility import phase_class  # noqa: E402
from trial_pos.services.population import (  # noqa: E402
    DRUG_INTERVENTION_TYPES, INTERVENTION_TYPE_SEP, has_drug_intervention,
    is_interventional, tribool,
)
from trial_pos.services.sampling import ordered_by_hash  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_SCHEMA = "ctgov"
DEFAULT_SALT = "probe-intervention-arms-v1"
DEFAULT_SAMPLES = 8
DEFAULT_MAX_K = 4                 # candidates-per-trial histogram: 1..max_k-1, then max_k+
DEFAULT_VOCAB_MAX = 40

# ---- arm roles ---------------------------------------------------------------------------
GROUP_EXPERIMENTAL = "EXPERIMENTAL"
GROUP_COMPARATOR = ("ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "SHAM_COMPARATOR")
GROUP_NEUTRAL = ("OTHER", "NO_INTERVENTION")
_ROLE_TYPES = (GROUP_EXPERIMENTAL,) + GROUP_COMPARATOR + GROUP_NEUTRAL
if set(_ROLE_TYPES) != set(GROUP_TYPES):
    raise SystemExit(f"!! arm-role sets {sorted(_ROLE_TYPES)} do not partition "
                     f"aact_fields.GROUP_TYPES {sorted(GROUP_TYPES)}; fix this probe first")

ROLE_UNLINKED = "unlinked_to_any_arm"
ROLE_EXPERIMENTAL_ONLY = "experimental_arms_only"
ROLE_EXPERIMENTAL_AND_COMPARATOR = "experimental_and_comparator_arms"
ROLE_COMPARATOR_ONLY = "comparator_arms_only"
ROLE_UNTYPED = "untyped_or_unrecognised_arms_only"
ROLE_NEUTRAL_ONLY = "other_or_no_intervention_arms_only"
ROLE_KINDS = (ROLE_UNLINKED, ROLE_EXPERIMENTAL_ONLY, ROLE_EXPERIMENTAL_AND_COMPARATOR,
              ROLE_COMPARATOR_ONLY, ROLE_UNTYPED, ROLE_NEUTRAL_ONLY)

# ---- trial outcome under the CANDIDATE rule, in precedence order --------------------------
OUT_NO_ARMS = "no_design_groups"
OUT_NO_DRUG_LINKED = "no_drug_intervention_linked_to_an_arm"
OUT_CANDIDATE_FOUND = "candidate_tested_agent_found"
OUT_ARMS_UNTYPED = "no_experimental_arm_and_some_arms_untyped"
OUT_NO_EXPERIMENTAL_ARM = "no_experimental_arm"
OUT_ALL_BACKGROUND = "every_experimental_drug_also_in_a_comparator_arm"
OUT_EXPERIMENTAL_NO_DRUG = "experimental_arms_carry_no_drug"
OUTCOMES = (OUT_NO_ARMS, OUT_NO_DRUG_LINKED, OUT_CANDIDATE_FOUND, OUT_ARMS_UNTYPED,
            OUT_NO_EXPERIMENTAL_ARM, OUT_ALL_BACKGROUND, OUT_EXPERIMENTAL_NO_DRUG)

# Names a candidate tested agent should never carry. Counted, never acted on here.
PLACEBO_NAME_PATTERN = re.compile(r"\b(placebo|sham|vehicle|dummy|saline)\b", re.IGNORECASE)

# MeSH attribution, in precedence order.
ATTR_CANDIDATE = "matches_a_candidate_tested_agent"
ATTR_DRUG = "matches_another_drug_intervention"
ATTR_ANY = "matches_a_non_drug_intervention"
ATTR_NONE = "matches_no_intervention_name"
ATTRIBUTIONS = (ATTR_CANDIDATE, ATTR_DRUG, ATTR_ANY, ATTR_NONE)

INTEGRITY_KINDS = ("link rows", "exact duplicate link rows",
                   "links to an intervention outside the trial",
                   "links to an arm outside the trial", "other-name rows",
                   "other names pointing outside the trial")

REQUIRED = {
    "studies": ("nct_id", "study_type", "phase"),
    "interventions": ("id", "nct_id", "intervention_type", "name"),
    "design_groups": ("id", "nct_id", "group_type", "title"),
    "design_group_interventions": ("nct_id", "design_group_id", "intervention_id"),
    "intervention_other_names": ("nct_id", "intervention_id", "name"),
    "browse_interventions": ("nct_id", "mesh_term", "downcase_mesh_term", "mesh_type"),
}
_C = 'COLLATE "C"'
SQL = {
    "studies": "SELECT nct_id, study_type, phase FROM {s}.studies ORDER BY nct_id",
    "interventions": ("SELECT id, nct_id, intervention_type, name FROM {s}.interventions "
                      "ORDER BY nct_id, id"),
    "design_groups": ("SELECT id, nct_id, group_type, title FROM {s}.design_groups "
                      "WHERE nct_id = ANY(%s) ORDER BY nct_id, id"),
    "design_group_interventions": (
        "SELECT nct_id, design_group_id, intervention_id FROM {s}.design_group_interventions "
        "WHERE nct_id = ANY(%s) ORDER BY nct_id, design_group_id, intervention_id"),
    "intervention_other_names": (
        "SELECT nct_id, intervention_id, name FROM {s}.intervention_other_names "
        f"WHERE nct_id = ANY(%s) ORDER BY nct_id, intervention_id, name {_C}"),
    "browse_interventions": (
        "SELECT nct_id, mesh_term, downcase_mesh_term, mesh_type FROM "
        f"{{s}}.browse_interventions WHERE nct_id = ANY(%s) "
        f"ORDER BY nct_id, mesh_type {_C}, mesh_term {_C}"),
}


def _rule(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def _pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def name_key(raw) -> str:
    """Casefold and collapse whitespace. Used only to ask whether two names are the same
    string; no dose stripping or salt handling, which would be a resolver decision."""
    return " ".join(str(raw or "").split()).casefold()


def normalize_group_type(raw):
    """Raw group_type -> uppercase underscore token, or None when blank."""
    s = str(raw or "").strip()
    return re.sub(r"[\s\-]+", "_", s.upper()) if s else None


# ---- pure analysis ------------------------------------------------------------------------
def drug_population(studies: dict, interventions: list) -> dict:
    """{nct: True/False/None} over interventional studies, exactly as the label pull
    decides is_drug_trial: has_drug_intervention on the pipe-joined type set."""
    types = defaultdict(set)
    for _iid, nct, itype, _name in interventions:
        if itype is not None and str(itype).strip():
            types[nct].add(str(itype))
    out = {}
    for nct, (study_type, _phase) in studies.items():
        if is_interventional(study_type) is not True:
            continue
        out[nct] = has_drug_intervention(INTERVENTION_TYPE_SEP.join(sorted(types.get(nct, ()))))
    return out


def is_drug_type(itype) -> bool:
    return str(itype or "").strip().lower() in DRUG_INTERVENTION_TYPES


def analyse(drug_ids: list, studies: dict, interventions: list, groups: list,
            links: list, other_names: list, mesh: list) -> dict:
    """Every count this probe prints, from in-memory rows. Pure."""
    drug = set(drug_ids)
    iv_by_trial = defaultdict(dict)                     # nct -> iid -> (type, name)
    for iid, nct, itype, name in interventions:
        if nct in drug:
            iv_by_trial[nct][iid] = (itype, name)
    grp_by_trial = defaultdict(dict)                    # nct -> gid -> normalised type
    raw_group_types = Counter()
    for gid, nct, gtype, _title in groups:
        grp_by_trial[nct][gid] = normalize_group_type(gtype)
        raw_group_types[gtype] += 1

    integrity = Counter({k: 0 for k in INTEGRITY_KINDS})
    seen_links = Counter((nct, gid, iid) for nct, gid, iid in links)
    integrity["link rows"] = len(links)
    integrity["exact duplicate link rows"] = sum(n - 1 for n in seen_links.values())
    arms_of_iv = defaultdict(set)                       # (nct, iid) -> {gid}
    for (nct, gid, iid) in seen_links:
        if iid not in iv_by_trial.get(nct, {}):
            integrity["links to an intervention outside the trial"] += 1
            continue
        if gid not in grp_by_trial.get(nct, {}):
            integrity["links to an arm outside the trial"] += 1
            continue
        arms_of_iv[(nct, iid)].add(gid)

    others = defaultdict(set)                           # (nct, iid) -> {name_key}
    integrity["other-name rows"] = len(other_names)
    for nct, iid, name in other_names:
        if iid not in iv_by_trial.get(nct, {}):
            integrity["other names pointing outside the trial"] += 1
            continue
        if name_key(name):
            others[(nct, iid)].add(name_key(name))

    unrecognised = Counter(t for g in grp_by_trial.values() for t in g.values()
                           if t is not None and t not in GROUP_TYPES)
    untyped_arms = sum(1 for g in grp_by_trial.values() for t in g.values() if t is None)

    roles = Counter()
    role_of = {}
    for nct in drug:
        for iid, (itype, _name) in iv_by_trial.get(nct, {}).items():
            if not is_drug_type(itype):
                continue
            kinds = {grp_by_trial[nct][g] for g in arms_of_iv.get((nct, iid), ())}
            if not kinds:
                role = ROLE_UNLINKED
            else:
                exp = GROUP_EXPERIMENTAL in kinds
                comp = bool(kinds & set(GROUP_COMPARATOR))
                odd = any(k is None or k not in GROUP_TYPES for k in kinds)
                if exp and comp:
                    role = ROLE_EXPERIMENTAL_AND_COMPARATOR
                elif exp:
                    role = ROLE_EXPERIMENTAL_ONLY
                elif comp:
                    role = ROLE_COMPARATOR_ONLY
                elif odd:
                    role = ROLE_UNTYPED
                else:
                    role = ROLE_NEUTRAL_ONLY
            roles[role] += 1
            role_of[(nct, iid)] = role

    outcome_of, candidates = {}, {}
    for nct in sorted(drug):
        arms = grp_by_trial.get(nct, {})
        drug_ivs = [iid for iid, (t, _n) in iv_by_trial.get(nct, {}).items() if is_drug_type(t)]
        linked = [iid for iid in drug_ivs if role_of.get((nct, iid)) != ROLE_UNLINKED]
        cand = [iid for iid in drug_ivs if role_of.get((nct, iid)) == ROLE_EXPERIMENTAL_ONLY]
        has_exp = GROUP_EXPERIMENTAL in arms.values()
        if not arms:
            out = OUT_NO_ARMS
        elif not linked:
            out = OUT_NO_DRUG_LINKED
        elif cand:
            out = OUT_CANDIDATE_FOUND
        elif not has_exp:
            out = OUT_ARMS_UNTYPED if any(t is None for t in arms.values()) \
                else OUT_NO_EXPERIMENTAL_ARM
        elif any(role_of.get((nct, i)) == ROLE_EXPERIMENTAL_AND_COMPARATOR for i in drug_ivs):
            out = OUT_ALL_BACKGROUND
        else:
            out = OUT_EXPERIMENTAL_NO_DRUG
        outcome_of[nct] = out
        candidates[nct] = cand

    by_phase = defaultdict(Counter)
    by_arms = defaultdict(Counter)
    for nct, out in outcome_of.items():
        by_phase[out][phase_class(studies[nct][1])] += 1
        by_arms[out]["1 arm" if len(grp_by_trial.get(nct, {})) == 1 else
                     ("0 arms" if not grp_by_trial.get(nct) else "2+ arms")] += 1

    k_hist = Counter(len(c) for nct, c in candidates.items()
                     if outcome_of[nct] == OUT_CANDIDATE_FOUND)
    placebo_named = [(nct, iid) for nct, c in candidates.items() for iid in c
                     if PLACEBO_NAME_PATTERN.search(str(iv_by_trial[nct][iid][1] or ""))]

    with_other = sum(1 for key, r in role_of.items() if others.get(key))
    cand_with_other = sum(1 for nct, c in candidates.items() for i in c if others.get((nct, i)))
    n_cand = sum(len(c) for c in candidates.values())

    raw_mesh_types = Counter(mt for _n, _t, _d, mt in mesh)
    mesh_list_spellings = Counter(mt for _n, _t, _d, mt in mesh
                                  if str(mt or "").strip().lower() == MESH_LIST)
    attribution = Counter()
    trials_with_list = set()
    trials_with_unattributed = set()
    name_sets: dict = {}

    def trial_name_sets(nct):
        """(candidate, drug, any) name_key sets for one trial, built once."""
        if nct not in name_sets:
            ivs = iv_by_trial.get(nct, {})
            per = {i: {name_key(n)} | others.get((nct, i), set()) for i, (_t, n) in ivs.items()}
            cand = set().union(*(per[i] for i in candidates.get(nct, ()))) if candidates.get(nct) \
                else set()
            drugs = [per[i] for i, (t, _n) in ivs.items() if is_drug_type(t)]
            name_sets[nct] = (cand, set().union(*drugs) if drugs else set(),
                              set().union(*per.values()) if per else set())
        return name_sets[nct]

    for nct, term, down, mt in mesh:
        if str(mt or "").strip().lower() != MESH_LIST:
            continue
        trials_with_list.add(nct)
        key = name_key(down if down else term)
        cand_names, drug_names, all_names = trial_name_sets(nct)
        if key in cand_names:
            a = ATTR_CANDIDATE
        elif key in drug_names:
            a = ATTR_DRUG
        elif key in all_names:
            a = ATTR_ANY
        else:
            a = ATTR_NONE
            trials_with_unattributed.add(nct)
        attribution[a] += 1

    samples_by_outcome = {o: [n for n, out in outcome_of.items() if out == o] for o in OUTCOMES}
    samples_by_role = defaultdict(list)
    for (nct, iid), role in role_of.items():
        samples_by_role[role].append(nct)
    return {
        "n_drug": len(drug), "integrity": integrity, "raw_group_types": raw_group_types,
        "unrecognised_group_types": unrecognised, "untyped_arms": untyped_arms,
        "n_arms": sum(len(g) for g in grp_by_trial.values()),
        "roles": roles, "outcomes": Counter(outcome_of.values()), "by_phase": by_phase,
        "by_arms": by_arms, "k_hist": k_hist, "placebo_named": placebo_named,
        "n_drug_ivs": len(role_of), "drug_ivs_with_other_names": with_other,
        "n_candidates": n_cand, "candidates_with_other_names": cand_with_other,
        "raw_mesh_types": raw_mesh_types, "mesh_list_spellings": mesh_list_spellings,
        "mesh_attribution": attribution, "trials_with_mesh_list": len(trials_with_list),
        "trials_with_unattributed_mesh": len(trials_with_unattributed),
        "samples_by_outcome": samples_by_outcome,
        "samples_by_role": {r: sorted(set(v)) for r, v in samples_by_role.items()},
        "iv_by_trial": iv_by_trial,
    }


def compare_entities(path: Path, drug: set, iv_by_trial: dict, mesh: list) -> dict:
    """trial_entities.csv against the restore, drug trials only, as sets per trial."""
    sql_names = {n: {v for (_t, v) in ivs.values() if v not in (None, "")}
                 for n, ivs in iv_by_trial.items()}
    sql_mesh_all, sql_mesh_list = defaultdict(set), defaultdict(set)
    for nct, term, _down, mt in mesh:
        if term in (None, ""):
            continue
        sql_mesh_all[nct].add(term)
        if str(mt or "").strip().lower() == MESH_LIST:
            sql_mesh_list[nct].add(term)
    out = Counter()
    differ = defaultdict(list)
    pipe_names = {n for n, s in sql_names.items() if any(AGG_SEPARATOR in v for v in s)}
    seen = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            nct = (row.get("nct_id") or "").strip().upper()
            if nct not in drug:
                continue
            seen.add(nct)
            f_names = {v for v in (row.get("intervention_names") or "").split(AGG_SEPARATOR)
                       if v != ""}
            f_mesh = {v for v in (row.get("intervention_mesh_terms") or "").split(AGG_SEPARATOR)
                      if v != ""}
            if f_names == sql_names.get(nct, set()):
                out["names equal"] += 1
            else:
                out["names differ"] += 1
                differ["names"].append(nct)
                if nct in pipe_names:
                    out["names differ, trial has a name containing the separator"] += 1
            all_m, list_m = sql_mesh_all.get(nct, set()), sql_mesh_list.get(nct, set())
            if f_mesh == all_m:
                out["mesh equals ALL mesh_types pooled"] += 1
            if f_mesh == list_m:
                out["mesh equals indexed (mesh-list) terms only"] += 1
            if f_mesh == all_m and all_m != list_m:
                out["mesh equals pooled AND pooled differs from indexed"] += 1
            if f_mesh != all_m and f_mesh != list_m:
                out["mesh equals neither"] += 1
                differ["mesh"].append(nct)
    out["drug trials absent from the file"] = len(drug - seen)
    return {"counts": out, "differ": differ}


def labels_crosstab(path: Path, derived: dict) -> Counter:
    """(file is_drug_trial, derived) paired over every interventional trial derived here."""
    table = Counter()
    seen = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            nct = (row.get("nct_id") or "").strip().upper()
            if nct not in derived:
                table[("in file only", tribool(row.get("is_drug_trial")))] += 1
                continue
            seen.add(nct)
            table[(tribool(row.get("is_drug_trial")), derived[nct])] += 1
    for nct in set(derived) - seen:
        table[("not in file", derived[nct])] += 1
    return table


# ---- printing -----------------------------------------------------------------------------
def _sample(ids, salt, k):
    return ordered_by_hash(ids, salt)[:k]


def report(res: dict, args) -> None:
    n = res["n_drug"]
    print(_rule("3. LINKAGE INTEGRITY (drug trials)"))
    for key, value in sorted(res["integrity"].items()):
        print(f"  {key:52s} {value:10d}")

    print(_rule("4. ARM VOCABULARY (drug trials)"))
    print(f"  arms {res['n_arms']}  untyped {res['untyped_arms']}")
    for raw, c in sorted(res["raw_group_types"].items(), key=lambda kv: (-kv[1], str(kv[0]))):
        print(f"    {raw!r:40s} {c:10d}  -> {normalize_group_type(raw)}")
    print(f"  normalised types outside aact_fields.GROUP_TYPES: "
          f"{dict(sorted(res['unrecognised_group_types'].items())) or 'none'}")

    print(_rule("5. ROLE OF EACH DRUG/BIOLOGICAL INTERVENTION"))
    total = res["n_drug_ivs"]
    print(f"  drug/biological intervention rows in drug trials: {total}")
    for role in ROLE_KINDS:
        c = res["roles"][role]
        print(f"  {role:44s} {c:10d}  {_pct(c, total):>6s}  sample "
              f"{_sample(res['samples_by_role'].get(role, []), args.salt, args.samples)}")

    print(_rule("6. TRIAL OUTCOME UNDER THE CANDIDATE RULE"))
    print("  CANDIDATE rule, not decided: the tested agent is a drug/biological intervention")
    print("  in >=1 EXPERIMENTAL arm and in no comparator arm. Outcomes in precedence order.")
    phases = sorted({p for c in res["by_phase"].values() for p in c})
    head = "".join(f"{p:>14s}" for p in phases)
    print(f"\n  {'outcome':52s} {'trials':>8s} {'share':>7s}{head}")
    for o in OUTCOMES:
        c = res["outcomes"][o]
        cells = "".join(f"{res['by_phase'][o][p]:14d}" for p in phases)
        print(f"  {o:52s} {c:8d} {_pct(c, n):>7s}{cells}")
    print(f"  {'(drug trials)':52s} {n:8d}")
    print(f"\n  {'outcome by arm count':52s} {'0 arms':>8s} {'1 arm':>8s} {'2+ arms':>8s}")
    for o in OUTCOMES:
        b = res["by_arms"][o]
        print(f"  {o:52s} {b['0 arms']:8d} {b['1 arm']:8d} {b['2+ arms']:8d}")
    print("\n  sample NCT ids per outcome, for reading on ClinicalTrials.gov:")
    for o in OUTCOMES:
        print(f"    {o:52s} {_sample(res['samples_by_outcome'][o], args.salt, args.samples)}")
    print(f"\n  candidates per trial (candidate_tested_agent_found only), max bucket "
          f"{args.max_k}+:")
    k = res["k_hist"]
    for i in range(1, args.max_k):
        print(f"    {i:>3d}      {k[i]:10d}")
    print(f"    {str(args.max_k) + '+':>4s}     {sum(v for j, v in k.items() if j >= args.max_k):10d}")
    pn = res["placebo_named"]
    print(f"\n  candidates whose name matches {PLACEBO_NAME_PATTERN.pattern!r}: {len(pn)} of "
          f"{res['n_candidates']}")
    for nct, iid in ordered_by_hash(pn, args.salt)[:args.samples]:
        print(f"    {nct}  {res['iv_by_trial'][nct][iid][1]!r}")

    print(_rule("7. OTHER NAMES"))
    print(f"  drug/biological interventions with >=1 other name: "
          f"{res['drug_ivs_with_other_names']} of {total} "
          f"({_pct(res['drug_ivs_with_other_names'], total)})")
    print(f"  candidate tested agents with >=1 other name: "
          f"{res['candidates_with_other_names']} of {res['n_candidates']} "
          f"({_pct(res['candidates_with_other_names'], res['n_candidates'])})")

    print(_rule("8. INTERVENTION MESH (browse_interventions, drug trials)"))
    print("  raw mesh_type spellings (rows):")
    for raw, c in sorted(res["raw_mesh_types"].items(), key=lambda kv: (-kv[1], str(kv[0]))):
        print(f"    {raw!r:30s} {c:10d}")
    print(f"  spellings that lowercase to {MESH_LIST!r}: "
          f"{dict(sorted(res['mesh_list_spellings'].items()))}")
    a = res["mesh_attribution"]
    tot = sum(a.values())
    print(f"\n  indexed terms ({MESH_LIST}) in {res['trials_with_mesh_list']} trials: {tot} rows. "
          f"Attribution by exact name_key equality with an intervention name or other name:")
    for k_ in ATTRIBUTIONS:
        print(f"    {k_:44s} {a[k_]:10d}  {_pct(a[k_], tot):>6s}")
    print(f"  trials with >=1 indexed term matching no intervention name: "
          f"{res['trials_with_unattributed_mesh']}")


def connect(args):
    try:
        import psycopg2
    except ImportError:
        raise SystemExit("!! psycopg2 is not installed: pip install -e .[pull]")
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db, user=args.user,
                            password=args.password)
    conn.set_session(readonly=True, autocommit=False)
    return conn


def missing_columns(conn, schema: str) -> dict:
    with conn.cursor() as c:
        c.execute("SELECT table_name, column_name FROM information_schema.columns "
                  "WHERE table_schema = %s ORDER BY table_name, column_name", (schema,))
        found = defaultdict(set)
        for t, col in c.fetchall():
            found[t].add(col)
    return {t: [col for col in cols if col not in found.get(t, set())]
            for t, cols in REQUIRED.items() if any(col not in found.get(t, set()) for col in cols)}


def fetch(conn, schema: str, table: str, ids=None) -> list:
    sql = SQL[table].format(s=schema)
    with conn.cursor() as c:
        c.execute(sql, (ids,) if ids is not None else None)
        return c.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=LOCAL_HOST)
    ap.add_argument("--port", type=int, default=LOCAL_PORT)
    ap.add_argument("--db", default=LOCAL_DB)
    ap.add_argument("--user", default=LOCAL_USER)
    ap.add_argument("--password", default=LOCAL_PASSWORD)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--entities", type=Path, default=None)
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--salt", default=DEFAULT_SALT)
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--max-k", type=int, default=DEFAULT_MAX_K)
    args = ap.parse_args()
    if not args.schema.isidentifier():
        raise SystemExit(f"!! bad schema name {args.schema!r}")
    print(f"host {args.host}:{args.port} db {args.db} schema {args.schema} | salt "
          f"{args.salt!r} | samples {args.samples} | max_k {args.max_k} | drug types "
          f"{sorted(DRUG_INTERVENTION_TYPES)} | mesh list token {MESH_LIST!r}")
    conn = connect(args)
    try:
        print(_rule("1. SCHEMA"))
        missing = missing_columns(conn, args.schema)
        for t, cols in REQUIRED.items():
            print(f"  {t:30s} needs {', '.join(cols)}  -> "
                  f"{'MISSING ' + str(missing[t]) if t in missing else 'ok'}")
        if missing:
            print("\n!! required columns absent; nothing measured.")
            return 2
        studies = {nct: (st, ph) for nct, st, ph in fetch(conn, args.schema, "studies")}
        interventions = fetch(conn, args.schema, "interventions")
        derived = drug_population(studies, interventions)
        drug_ids = sorted(n for n, v in derived.items() if v is True)
        print(_rule("2. POPULATION"))
        print(f"  studies {len(studies)}  interventional {len(derived)}  drug "
              f"{len(drug_ids)}  not drug {sum(1 for v in derived.values() if v is False)}  "
              f"no intervention rows {sum(1 for v in derived.values() if v is None)}")
        if args.labels is not None:
            table = labels_crosstab(args.labels, derived)
            print(f"  paired against {args.labels} is_drug_trial (file, derived):")
            for key, c in sorted(table.items(), key=lambda kv: str(kv[0])):
                print(f"    {str(key):40s} {c:10d}")
        groups = fetch(conn, args.schema, "design_groups", drug_ids)
        links = fetch(conn, args.schema, "design_group_interventions", drug_ids)
        others = fetch(conn, args.schema, "intervention_other_names", drug_ids)
        mesh = fetch(conn, args.schema, "browse_interventions", drug_ids)
        print(f"  rows read for drug trials: arms {len(groups)}, links {len(links)}, other "
              f"names {len(others)}, intervention MeSH {len(mesh)}")
    finally:
        conn.rollback()
        conn.close()
    res = analyse(drug_ids, studies, interventions, groups, links, others, mesh)
    report(res, args)
    if args.entities is not None:
        print(_rule(f"9. {args.entities} AGAINST THE RESTORE (drug trials)"))
        cmp_ = compare_entities(args.entities, set(drug_ids), res["iv_by_trial"], mesh)
        for key, c in sorted(cmp_["counts"].items()):
            print(f"  {key:60s} {c:10d}")
        for col, ids in sorted(cmp_["differ"].items()):
            print(f"  sample trials whose {col} differ: {_sample(ids, args.salt, args.samples)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
