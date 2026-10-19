#!/usr/bin/env python3
"""Form-level FDA approvals of every resolved tested agent (services/form_approval.py).
Reads only; writes its own two files into --out-dir:

  trial_drug_form_approvals.csv                one row per resolved (trial, tested agent)
  trial_drug_form_approvals.manifest.json      inputs (with hashes), settings, counts

Each row carries two product sets (strict: every stated requirement confirmed; loose: none
excluded) with their distinct approval dates, so the lag and the market label can ask any
dated question and get an answer only where both sets agree (D-27).

--set-rule defaults to contained (D-32) and --brand-rule to pin (D-33), both decided by
the owner on 2026-10-13. "Approved" means on the US market for ANY indication, not for the
trial's indication (D-32). The audit still prints how the other choice of each would change
the pivotal new-molecule status.

Needs the trial_drug_agents.csv written AFTER D-28 (hydrate words are not salts): an older
file stops the run, because its stated salts would exclude products wrongly.

Usage (PowerShell), one per block:
  python scripts\\match_form_approvals.py --overwrite |
      Out-File -Encoding utf8 match_form_approvals_20261001_v2.txt
  (one line in PowerShell)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from resolve_trial_drugs import (  # noqa: E402
    DC_MANIFEST, DC_NEED, FDA_APPLICATIONS, FDA_PRODUCTS, LABELS_FILE, OUT_AGENTS, PARTIAL,
    load_fda, read_rows, sha256_of,
)
from trial_pos.services.drug_dictionary import (  # noqa: E402
    OB_KEY_DRUGS_DIFFER, OB_KEY_REPEATED, SRC_FDA_BRAND, SRC_OB_TRADE, build_dictionary,
    parse_struct_id,
)
from trial_pos.services.drug_names import NON_DISTINGUISHING_SALT_WORDS, key  # noqa: E402
from trial_pos.services.drug_resolution import (  # noqa: E402
    AGENT_RESOLVED, DRUG_SEP, LIST_SEP, STEP_SEP,
)
from trial_pos.services.drugsatfda import (  # noqa: E402
    APPL_BLA, decode, norm_appl, parse_tab, require_columns,
)
from trial_pos.services.eligibility import phase_class, readout_date  # noqa: E402
from trial_pos.services.form_approval import (  # noqa: E402
    APPROVAL_COLUMNS, BEFORE_STATUSES as STATUSES, BRAND_MOIETY, BRAND_PIN, BRAND_RULES,
    CONFIRMED, EXCLUDED, POSSIBLE, SET_CONTAINED, SET_EXACT, SET_RULES, STATUS_BOUNDS,
    original_dates,
    STATUS_DC_EARLIER, STATUS_DC_UNDATED, STATUS_GAP, STATUS_NOT, STATUS_UNDATED,
    ProductIndex, Query, approval_row, approvals,
    before_status as status, build_products, fda_approval_dates, fda_approved_structures,
    value_states,
)
from trial_pos.services.drug_names import drop_parentheticals  # noqa: E402
from trial_pos.services.drug_resolution import resolve_one_name  # noqa: E402
from trial_pos.services.purplebook import read_purplebook  # noqa: E402
from trial_pos.services.population import tribool  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_SHOW = 12
PIVOTAL = "pivotal"
FDA_AGENCY = "FDA"                        # DrugCentral approval.type token
SUBMISSIONS = "Submissions.txt"
OUT_APPROVALS = "trial_drug_form_approvals.csv"
PURPLEBOOK_FILE = "Advanced_Search.csv"
_ALNUM = re.compile(r"[a-z0-9]+")
OUT_MANIFEST = "trial_drug_form_approvals.manifest.json"
AGENT_NEED = ("nct_id", "intervention_id", "agent_status", "drugs", "name_used", "source",
              "matched_key", "stated_salts", "stated_form_words")
OB_NEED = DC_NEED["ob_product"] + ("appl_type", "approval_date")
BRAND_SOURCES = frozenset({SRC_OB_TRADE, SRC_FDA_BRAND})
OTHER = {SET_EXACT: SET_CONTAINED, SET_CONTAINED: SET_EXACT,
         BRAND_PIN: BRAND_MOIETY, BRAND_MOIETY: BRAND_PIN}



def _rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _pct(n, d) -> str:
    return "undefined" if not d else f"{100.0 * n / d:.1f}%"


def split_list(raw) -> tuple:
    return tuple(x for x in (raw or "").split(LIST_SEP) if x)


def main() -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set-rule", default=SET_CONTAINED, choices=SET_RULES,
                    help="D-32 (owner, 2026-10-13): contained")
    ap.add_argument("--brand-rule", default=BRAND_PIN, choices=BRAND_RULES,
                    help="D-33 (owner, 2026-10-13): pin")
    ap.add_argument("--aact-dir", type=Path, default=Path("data") / "aact")
    ap.add_argument("--dc-dir", type=Path, default=Path("data") / "drugcentral")
    ap.add_argument("--fda-dir", type=Path, default=Path("data") / "drugsatfda")
    ap.add_argument("--labels-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--out-dir", type=Path, default=Path("data") / "labels")
    ap.add_argument("--purplebook", type=Path,
                    default=Path("data") / "purplebook" / PURPLEBOOK_FILE,
                    help="the Purple Book Advanced Search export (D-41)")
    ap.add_argument("--inspect", action="append", default=[],
                    help="a drug name whose products and evidence to print; repeatable")
    ap.add_argument("--show", type=int, default=DEFAULT_SHOW)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    outs = {k: args.out_dir / k for k in (OUT_APPROVALS, OUT_MANIFEST)}
    if any(p.exists() for p in outs.values()) and not args.overwrite:
        raise SystemExit("!! outputs exist; pass --overwrite: "
                         + ", ".join(str(p) for p in outs.values() if p.exists()))
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    _rule("SETTINGS")
    print(f"  set rule {args.set_rule} | brand rule {args.brand_rule} | DrugCentral agency "
          f"{FDA_AGENCY!r} | show {args.show}")

    _rule("INPUTS")
    m = json.loads((args.dc_dir / DC_MANIFEST).read_text(encoding="utf-8"))
    need = dict(DC_NEED, ob_product=OB_NEED, approval=("struct_id", "type", "approval"))
    for t in need:
        if t not in m["tables"]:
            raise SystemExit(f"!! DrugCentral manifest lacks {t}")
    dc = {t: list(read_rows(args.dc_dir / m["tables"][t]["file"], n)) for t, n in need.items()}
    for t, rows in dc.items():
        print(f"  dc {t:20s} rows {len(rows):8d}")
    products_raw = load_fda(args.fda_dir, FDA_PRODUCTS)
    applications = load_fda(args.fda_dir, FDA_APPLICATIONS)
    path = args.fda_dir / SUBMISSIONS
    if not path.exists():
        raise SystemExit(f"!! {path} not found")
    text, enc = decode(path.read_bytes())
    table = parse_tab(text)
    require_columns(table, ("ApplNo", "SubmissionType", "SubmissionStatus",
                            "SubmissionStatusDate"), SUBMISSIONS)
    submissions = table["rows"]
    print(f"  {SUBMISSIONS:20s} rows {len(submissions):8d}  malformed {table['malformed']}  {enc}")

    d = build_dictionary(dc["synonyms"], dc["structures"], dc["struct2parent"],
                         dc["ob_product"], dc["struct2obprod"], products_raw, applications)
    if not args.purplebook.exists():
        raise SystemExit(f"!! {args.purplebook} not found: the Purple Book Advanced Search "
                         "export (D-41). Nothing written.")
    with args.purplebook.open(encoding="utf-8", newline="") as f:
        pb_rows, pb_audit = read_purplebook(csv.DictReader(f))
    print(f"  Purple Book {args.purplebook.name:13s} rows {len(pb_rows):8d}  "
          f"{dict(sorted(pb_audit.items()))}")
    products, build_audit = build_products(d, products_raw, applications, submissions,
                                           dc["ob_product"], dc["struct2obprod"], pb_rows)
    fda_ok = fda_approved_structures(dc["approval"], FDA_AGENCY)
    fda_first, fda_undated = fda_approval_dates(dc["approval"], FDA_AGENCY)
    index = ProductIndex(products, fda_ok, fda_first, fda_undated)
    name_of = {parse_struct_id(r["id"]): r["name"] for r in dc["structures"]}

    def names(ids):
        return " + ".join(sorted(name_of.get(x, str(x)) for x in ids)) if ids else "-"

    _rule("1. BUILD: NDA/BLA products, identity, dates")
    by_type = dict(sorted(Counter(p.appl_type for p in products).items()))
    print(f"  NDA/BLA products {len(products)} (by type {by_type}); "
          f"DrugCentral FDA-approved structures {len(fda_ok)}")
    for k, v in sorted(build_audit.items()):
        print(f"  {k:72s} {v:7d}")
    for k in (OB_KEY_REPEATED, OB_KEY_DRUGS_DIFFER):     # zero printed, never omitted
        print(f"  dictionary: {k:60s} {d.audit[k]:7d}")
    alt = Counter()
    for p in products:
        if p.alternative is not None and p.alternative != p.link:
            alt[(key(" ; ".join(x.text for x in p.parts))[:44], names(p.link),
                 names(p.alternative))] += 1
    print(f"\n  alternative (salt-stripped) identity differs from the link, top {args.show * 2} "
          f"of {len(alt)} ingredient strings (products | ingredient | link | alternative):")
    for (ing, a1, a2), n in sorted(alt.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show * 2]:
        print(f"    {n:4d}  {ing:44s} {a1[:30]:30s} -> {a2[:30]}")

    # ---- the Purple Book against Drugs@FDA: the key check (rev 11 section 8) -----------
    _rule("1b. PURPLE BOOK: key check against Drugs@FDA, and what it could not link")
    fda_bla = {norm_appl(r["ApplNo"]) for r in applications if r.get("ApplType") == APPL_BLA}
    pb_cder = {norm_appl(r.bla) for r in pb_rows if r.center == "CDER"}
    print(f"  CDER BLA numbers in the Purple Book {len(pb_cder)}; also Drugs@FDA BLAs "
          f"{len(pb_cder & fda_bla)}; Purple Book only {len(pb_cder - fda_bla)} "
          f"e.g. {sorted(pb_cder - fda_bla)[:8]}")
    originals = original_dates(submissions)
    pb_first = {}
    for r in pb_rows:
        a = norm_appl(r.bla)
        if r.approved and (a not in pb_first or r.approved < pb_first[a]):
            pb_first[a] = r.approved
    cmp_dates, cmp_examples = Counter(), []
    for a in sorted(pb_first.keys() & originals.keys() & fda_bla):
        x, y = pb_first[a], originals[a]
        k = "same" if x == y else "Purple Book earlier" if x < y else "Purple Book later"
        cmp_dates[k] += 1
        if k != "same" and len(cmp_examples) < args.show:
            cmp_examples.append(f"{a} purple book {x} drugs@fda {y}")
    print(f"  first approval date, BLAs in both: {dict(sorted(cmp_dates.items()))}")
    for e in cmp_examples:
        print(f"    {e}")
    unlinked = Counter()
    for r in pb_rows:
        if resolve_one_name(d, r.proper).status != AGENT_RESOLVED:
            unlinked[(r.center, key(r.proper)[:60])] += 1
    for c in ("CDER", "CBER"):
        top = sorted(((k, n) for k, n in unlinked.items() if k[0] == c),
                     key=lambda kv: (-kv[1], kv[0]))
        print(f"  {c} proper names with no DrugCentral identity: {len(top)} names, "
              f"{sum(n for _k, n in top)} rows; top {args.show}:")
        for (_c, nm), n in top[:args.show]:
            print(f"    {n:4d}  {nm}")

    # Could unresolved trial agents name these products? Word-set match on the proper name
    # without brackets, or the brand: 'pneumococcal 13-valent conjugate vaccine (diphtheria
    # crm197 protein)' and '13-valent pneumococcal conjugate vaccine' share their words.
    pb_words = defaultdict(set)
    for r in pb_rows:
        if resolve_one_name(d, r.proper).status == AGENT_RESOLVED:
            continue
        for n in (drop_parentheticals(key(r.proper)), key(r.proprietary)):
            w = frozenset(_ALNUM.findall(n))
            if w:
                pb_words[w].add(r.center)
    hits = Counter()
    for r in read_rows(args.labels_dir / OUT_AGENTS, AGENT_NEED):
        if r["agent_status"] == AGENT_RESOLVED:
            continue
        w = frozenset(_ALNUM.findall(drop_parentheticals(key(r["name_used"]))))
        for c in sorted(pb_words.get(w, ())):
            hits[(c, key(r["name_used"])[:56])] += 1
    for c in ("CDER", "CBER"):
        top = sorted(((k, n) for k, n in hits.items() if k[0] == c),
                     key=lambda kv: (-kv[1], kv[0]))
        print(f"  not-resolved agents naming an unlinked {c} product: "
              f"{sum(n for _k, n in top)} agents, {len(top)} names; top {args.show}:")
        for (_c, nm), n in top[:args.show]:
            print(f"    {n:4d}  {nm}")

    for name in args.inspect:
        _rule(f"INSPECT {name!r}")
        res = resolve_one_name(d, name)
        print(f"  resolves: {res.status} {names(res.drugs)} (step {res.step}, source "
              f"{res.source})")
        for p in index.carrying(frozenset(res.drugs)):
            print(f"    {p.appl}/{p.prod} {p.appl_type} {p.form[:44]:44s} "
                  f"{key(' ; '.join(x.text for x in p.parts))[:40]:40s} link "
                  f"{names(p.link)[:30]} alt {names(p.alternative)[:30]} {p.approved} "
                  f"{p.date_source}")
        for x in sorted(res.drugs):
            print(f"  DrugCentral FDA: {name_of.get(x, x)} first {fda_first.get(x)} "
                  f"undated {x in fda_undated}")

    labels = {}
    for r in read_rows(args.aact_dir / LABELS_FILE, ("nct_id", "is_drug_trial", "phase",
                                                      "primary_completion_date",
                                                      "primary_completion_date_type")):
        if tribool(r["is_drug_trial"]) is True:
            labels[r["nct_id"]] = (phase_class(r["phase"]), readout_date(r))
    agents_path = args.labels_dir / OUT_AGENTS
    agents = []
    for r in read_rows(agents_path, AGENT_NEED):
        if r["agent_status"] != AGENT_RESOLVED:
            continue
        salts = split_list(r["stated_salts"])
        stale = set(salts) & NON_DISTINGUISHING_SALT_WORDS
        if stale:
            raise SystemExit(f"!! {agents_path} states {sorted(stale)} as salts: it predates "
                             "D-28. Rerun scripts\\resolve_trial_drugs.py first. Nothing written.")
        ms = frozenset(int(x) for x in r["drugs"].split(DRUG_SEP) if x)
        if not ms:
            raise SystemExit(f"!! resolved agent with no drug: {r['nct_id']} "
                             f"{r['intervention_id']}. Nothing written.")
        brand = (key(r["matched_key"])
                 if set(r["source"].split(STEP_SEP)) & BRAND_SOURCES else "")
        ph, ro = labels.get(r["nct_id"], (None, None))
        agents.append((r, Query(ms, salts, split_list(r["stated_form_words"]), brand), ph, ro))
    print(f"\n  resolved agents {len(agents)}; matched through a brand "
          f"{sum(1 for _r, q, _p, _o in agents if q.brand)}")

    results = [approvals(index, q, args.set_rule, args.brand_rule) for _r, q, _p, _o in agents]

    _rule("2. LINE-UP CHECK: each stated value against the agent's products (all phases)")
    print("  best state over the products that are the moiety set; 'no product' = none")
    table = defaultdict(Counter)
    excluded = defaultdict(Counter)
    for (r, q, _ph, _ro) in agents:
        if not (q.salts or q.words):
            continue
        for kind, value, best in value_states(index, q, args.set_rule, args.brand_rule):
            table[(kind, value)][best or "no product"] += 1
            if best == EXCLUDED:
                excluded[(kind, value)][(key(r["name_used"])[:40], names(q.moieties)[:30])] += 1
    print(f"\n  {'kind':9s} {'value':22s} {'agents':>7s} {CONFIRMED:>10s} {POSSIBLE:>9s} "
          f"{EXCLUDED:>9s} {'no product':>11s}")
    for (kind, value), c in sorted(table.items()):
        print(f"  {kind:9s} {value:22s} {sum(c.values()):7d} {c[CONFIRMED]:10d} "
              f"{c[POSSIBLE]:9d} {c[EXCLUDED]:9d} {c['no product']:11d}")
    print(f"\n  EXCLUDED BY EVERY PRODUCT, top {args.show} per value (agents | name | moiety):")
    for kv in sorted(excluded):
        print(f"\n    {kv[0]} {kv[1]}: {sum(excluded[kv].values())} agents")
        for (nm, mo), n in sorted(excluded[kv].items(),
                                  key=lambda x: (-x[1], x[0]))[:args.show]:
            print(f"      {n:5d}  {nm:40s} {mo}")

    _rule("3. PIVOTAL resolved agents with an ACTUAL readout: approved before the readout?")
    piv = [i for i, (_r, _q, ph, ro) in enumerate(agents) if ph == PIVOTAL and ro is not None]
    print(f"  agents {len(piv)} in {len({agents[i][0]['nct_id'] for i in piv})} trials")
    main_status = {i: status(results[i], agents[i][3]) for i in piv}
    by = defaultdict(Counter)
    for i in piv:
        q = agents[i][1]
        by["all"][main_status[i]] += 1
        by["form stated" if (q.salts or q.words) else "no form stated"][main_status[i]] += 1
        if q.brand:
            pin = results[i].brand_pinned
            by[f"brand, pinned={'' if pin is None else pin}"][main_status[i]] += 1
    width = max(len(s) for s in STATUSES)
    for group in sorted(by, key=lambda g: (g != "all", g)):
        n = sum(by[group].values())
        print(f"\n  {group}: {n}")
        for s in STATUSES:
            print(f"    {s:{width}s} {by[group][s]:7d}  {_pct(by[group][s], n)}")
    for s in (STATUS_BOUNDS, STATUS_UNDATED, STATUS_GAP, STATUS_DC_EARLIER, STATUS_DC_UNDATED,
              STATUS_NOT):
        top = Counter((key(agents[i][0]["name_used"])[:40], names(agents[i][1].moieties)[:30],
                       "|".join(agents[i][1].words + agents[i][1].salts))
                      for i in piv if main_status[i] == s)
        print(f"\n  {s}: top {args.show} (agents | name | moiety | stated)")
        for (nm, mo, st), n in sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"    {n:5d}  {nm:40s} {mo:30s} [{st}]")

    _rule("4. THE OPEN DECISIONS: pivotal status under the other choice of each")
    for label, rule_set, rule_brand in (
            (f"set rule {OTHER[args.set_rule]}", OTHER[args.set_rule], args.brand_rule),
            (f"brand rule {OTHER[args.brand_rule]}", args.set_rule, OTHER[args.brand_rule])):
        flips, examples = Counter(), Counter()
        for i in piv:
            r, q, _ph, ro = agents[i]
            other = status(approvals(index, q, rule_set, rule_brand), ro)
            if other != main_status[i]:
                flips[(main_status[i], other)] += 1
                examples[(key(r["name_used"])[:40], main_status[i][:24], other[:24])] += 1
        print(f"\n  {label}: {sum(flips.values())} of {len(piv)} agents change status")
        for (a, b), n in sorted(flips.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {n:6d}  {a}  ->  {b}")
        for (nm, a, b), n in sorted(examples.items(), key=lambda kv: (-kv[1], kv[0]))[:args.show]:
            print(f"      e.g. {n:4d}  {nm:40s} {a} -> {b}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    partial = {k: p.with_name(p.name + PARTIAL) for k, p in outs.items()}
    try:
        with partial[OUT_APPROVALS].open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=APPROVAL_COLUMNS)
            w.writeheader()
            for (r, q, _ph, _ro), a in zip(agents, results):
                w.writerow(approval_row(r["nct_id"], r["intervention_id"], r["drugs"], q, a,
                                        args.set_rule, args.brand_rule))
        inputs = {str(p): sha256_of(p) for p in
                  [agents_path, args.aact_dir / LABELS_FILE, args.dc_dir / DC_MANIFEST,
                   args.fda_dir / FDA_PRODUCTS, args.fda_dir / FDA_APPLICATIONS,
                   args.fda_dir / SUBMISSIONS] if p.exists()}
        manifest = {
            "script": "match_form_approvals.py", "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "settings": {"set_rule": args.set_rule, "brand_rule": args.brand_rule,
                         "fda_agency": FDA_AGENCY},
            "inputs_sha256": inputs, "agents": len(agents), "products": len(products),
            "pivotal_status": {s: by["all"][s] for s in STATUSES},
            "outputs": {OUT_APPROVALS: {"bytes": partial[OUT_APPROVALS].stat().st_size,
                                        "sha256": sha256_of(partial[OUT_APPROVALS])}},
        }
        partial[OUT_MANIFEST].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for k, p in outs.items():
            os.replace(partial[k], p)
    except BaseException:
        for p in partial.values():
            if p.exists():
                p.unlink()
        raise
    _rule("WRITTEN")
    for p in outs.values():
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
