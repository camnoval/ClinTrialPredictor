#!/usr/bin/env python3
"""Measure the binding constraint on the CTO forward test BEFORE writing cto_source.py.

The spine is ~90% Morgan-fingerprint (R8), so the number that governs the forward test is:
of the CTO 2020-2024 gold trials, how many have a drug arm that resolves to a
fingerprint-able small molecule -- and of those, how many also get a ChEMBL indication
label (label (i)). Biologics, coded compounds, radiotracers, and supplements will not
resolve; that is the honest ceiling (and exactly the product's hard case). This is the
CTO analogue of join_chembl.py / label_indication.py: measure before building.

Cohort (matches the TOP framing): interventional, >=1 DRUG/BIOLOGICAL arm, phase I-III.

Pipeline per trial:
  CT.gov v2 (reuse probe_ctgov.extract_trial)  ->  clean drug names (strip placebo/vehicle)
  -> resolve each name to ChEMBL (cached live lookup -> data/cto/name2chembl.csv)
  -> fingerprint-able if canonical_smiles parses in rdkit
  -> indication label if a resolved molecule's ChEMBL indication crosswalks (MONDO) to a
     trial condition, via data/ontology/mondo_xref.csv + data/chembl/drug_indication.csv
     (same crosswalk build_units.py uses; R11 parity).

Run the sanity check first (~50 trials, seconds), then the full pass:
  python scripts/audit_cto_coverage.py --limit 50
  python scripts/audit_cto_coverage.py                      # full 2020-24 gold set

Deps: pip install chembl_webresource_client rdkit pandas fsspec huggingface_hub
The trial id list defaults to the CTO file on HF (10 MB, read over the network, no bulk
download); override with --labels data/cto/human_labels_2020_2024.csv if you have it local.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # reuse sibling scripts
from probe_ctgov import extract_trial, fetch_one  # noqa: E402
from build_units import _load_mondo, _norm         # noqa: E402  (same crosswalk as build_units)

HF_LABELS = ("hf://datasets/chufangao/CTO/"
             "human_labels_2020_2024/human_labels_2020_2024.csv")
V2_LIST = "https://clinicaltrials.gov/api/v2/studies"
COHORT_PHASES = {"EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3"}
PLACEBO_RE = re.compile(
    r"\b(placebo|vehicle|sham|saline|sodium chloride|normal saline|dextrose|"
    r"water for injection|0\.9%)\b", re.I)
# formulations, routes, and salts to peel off so the parent name matches
FORM_RE = re.compile(
    r"\b(injectable product|injection|infusion|solution|suspension|tablets?|capsules?|"
    r"oral|patch|lozenge|gel|cream|spray|film|hydrochloride|hcl|sulfate|sulphate|sodium|"
    r"maleate|mesylate|besylate|citrate|acetate|fumarate|tartrate|dihydrate|for infusion|"
    r"for injection|low dose|high dose|bid|qd|tid|qid)\b", re.I)
UNIT = {"mg", "milligram", "milligrams", "mcg", "microgram", "micrograms", "g", "gram",
        "grams", "ml", "iu", "unit", "units", "%", "kg", "mg/kg", "dose", "doses",
        "\u00b5g", "\u03bcg", "ug"}
PHRASE_STOP = {"active", "control", "comparator", "standard of care", "best supportive care",
               "usual care", "observation", "no intervention", "vehicle", "saline",
               "normal saline", "standard therapy", "standard treatment", "active comparator"}
COMBO_RE = re.compile(
    r"\s*(?:,|/|\+| and | or | plus |in combination with|combined with| with |combination)\s*",
    re.I)
# doses whether spaced or glued: "40 mg", "20mg", "2.5 mg", "5mcg"
DOSE_GLUE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|milligrams?|microgram|g|ml|iu|units?|%)\b", re.I)
# arm/dose descriptor tokens that are never a drug name
TOKEN_STOP = {"high", "low", "dose", "doses", "arm", "group", "cohort", "part", "period",
              "stage", "active", "control", "comparator", "standard", "care", "of",
              "vehicle", "placebo", "daily", "weekly", "once", "twice", "per", "day",
              "days", "week", "weeks"}


def _split_combo(raw: str):
    """A regimen arm can name several drugs -> resolve/fingerprint each (as TOP pools)."""
    return [p for p in COMBO_RE.split(raw or "") if p.strip()]


def clean_drug_name(part: str):
    """Normalize ONE drug name; None if placebo/descriptor/dose-only junk."""
    if not part:
        return None
    if PLACEBO_RE.search(part):
        return None
    p = part.split("(")[0]                       # drop parenthetical formulation notes
    p = re.sub(r"\[[^\]]*\]", " ", p)            # drop [11C] radiolabels
    p = p.replace("™", "").replace("®", "").replace(")", " ").replace("(", " ")
    p = DOSE_GLUE_RE.sub(" ", p)                 # "5mg"/"40 mg"/"2.5 mg" -> gone
    p = FORM_RE.sub(" ", p)
    toks = []
    for t in re.split(r"\s+", p):
        tl = t.lower().strip(".,")
        if not re.search(r"[A-Za-z]", t):        # drop pure numbers / dose leftovers
            continue
        if tl in UNIT or tl in TOKEN_STOP:
            continue
        if re.fullmatch(r"[a-z]\d+", tl):        # arm labels like "e1", "a1"
            continue
        toks.append(t)
    p = " ".join(toks).strip(" -,.")
    if not p or len(p) <= 1 or p.lower() in PHRASE_STOP:
        return None
    return p


def drug_candidates(raw: str):
    """arm name -> list of cleaned drug names (combo-split, junk removed)."""
    return [c for part in _split_combo(raw) for c in [clean_drug_name(part)] if c]


def cohort_ok(trial: dict) -> bool:
    if (trial.get("study_type") or "").upper() != "INTERVENTIONAL":
        return False
    if not (set(trial.get("phases") or []) & COHORT_PHASES):
        return False
    names = [n for x in trial.get("drug_or_biological_names") or [] for n in drug_candidates(x)]
    return bool(names)


def trial_mondo(trial, name2, mesh2):
    """Crosswalk a trial's conditions (names + MeSH ids) to a MONDO id set."""
    s = set()
    for c in trial.get("conditions") or []:
        s |= name2.get(_norm(c), set())
    for mid, term in trial.get("condition_mesh") or []:
        if mid:
            s |= mesh2.get(str(mid).upper(), set())
        if term:
            s |= name2.get(_norm(term), set())
    return s


def summarize(rows):
    """rows: list of dicts with cohort/fp/ind flags -> printable counts. Pure."""
    n = len(rows)
    coh = [r for r in rows if r["cohort"]]
    fp = [r for r in coh if r["fp_ok"]]
    ind = [r for r in coh if r["ind_label"] is not None]
    both = [r for r in coh if r["fp_ok"] and r["ind_label"] is not None]
    return {
        "fetched": n,
        "cohort (intervent., drug arm, ph I-III)": len(coh),
        "  resolved to >=1 ChEMBL molecule": sum(r["resolved"] for r in coh),
        "  fingerprint-able (small molecule)": len(fp),
        "  ChEMBL indication label available": len(ind),
        "  BOTH fp + indication label": len(both),
    }


# ---- impure: fetch + resolve -----------------------------------------------
def fetch_batch(ids, sleep=0.2):
    """CT.gov v2 list endpoint with chunked filter.ids; per-id fallback.
    Fetches full studies (no fields filter) -- verified to parse; payload is modest."""
    import json
    out = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        url = f"{V2_LIST}?filter.ids={','.join(chunk)}&pageSize={len(chunk)}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "trial-pos-audit/0.1", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                page = json.loads(r.read().decode("utf-8"))
            got = page.get("studies", [])
            if not got:
                raise ValueError("empty page")
            out.extend(got)
        except Exception as e:
            print(f"  batch {i//100} fell back to per-id ({type(e).__name__}: {e})", flush=True)
            for nct in chunk:
                try:
                    out.append(fetch_one(nct))
                except Exception as ee:
                    print(f"    !! {nct}: {ee}")
        print(f"  fetched {min(i+100, len(ids))}/{len(ids)} trials...", flush=True)
        if sleep:
            time.sleep(sleep)
    return out


# ---- cross-verification (pure) ---------------------------------------------
def _skeleton(ik):
    """First InChIKey block = connectivity layer: same molecule ignoring salt/stereo."""
    return ik.split("-")[0] if ik else ""


def _skeleton_from_smiles(smi):
    """Largest organic fragment -> InChIKey first block. Robust to salts/counterions/hydrates
    (dasatinib vs dasatinib monohydrate agree). Falls back to '' if rdkit/parse unavailable."""
    if not smi:
        return ""
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import inchi
        RDLogger.DisableLog("rdApp.*")
        m = Chem.MolFromSmiles(smi)
        if m is None:
            return ""
        frags = Chem.GetMolFrags(m, asMols=True, sanitizeFrags=False)
        if frags:
            m = max(frags, key=lambda fr: fr.GetNumAtoms())
        return inchi.MolToInchiKey(m).split("-")[0]
    except Exception:
        return ""


def confidence_from_skeletons(sk_c, sk_p, has_chembl, has_pubchem):
    """Two independent IDs that AGREE -> high; one source -> medium; disagree -> conflict."""
    if sk_c and sk_p:
        return "high" if sk_c == sk_p else "conflict"
    if has_chembl or has_pubchem:
        return "medium"
    return "miss"


def decide_confidence(ik_chembl, ik_pubchem, has_chembl, has_pubchem):
    """InChIKey-string version (kept for callers/tests); prefer confidence_from_skeletons."""
    return confidence_from_skeletons(_skeleton(ik_chembl), _skeleton(ik_pubchem),
                                     has_chembl, has_pubchem)


_CONF_RANK = {"miss": 0, "conflict": 0, "medium": 1, "high": 2}


def conf_usable(conf, min_conf):
    """conflict is never usable; otherwise meet the confidence floor."""
    return conf != "conflict" and _CONF_RANK[conf] >= _CONF_RANK[min_conf]


def _get_json(url, sleep=0.15, timeout=30):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "trial-pos-audit/0.1", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        data = {}
    if sleep:
        time.sleep(sleep)
    return data


class MultiSourceResolver:
    """name -> molecule via THREE sources that check each other, cached to CSV.

      ChEMBL  : exact pref_name/synonym, gated to molecule_type == 'Small molecule'
                (structurally excludes the dose-string / cell-therapy false positives),
                gives chembl_id (needed for the ChEMBL indication label) + structure.
      RxNorm  : NLM RxNav normalizes messy clinical strings ('seladelpar 10 mg tablet')
                down to an ingredient -- fixes recall the regex can't.
      PubChem : independent structure for the RxNorm-normalized (or raw) name -> a second
                InChIKey to verify ChEMBL against.
    confidence = decide_confidence(ChEMBL InChIKey, PubChem InChIKey). high = both agree.
    """
    FIELDS = ["name", "chembl_id", "smiles", "inchikey_chembl", "rxcui", "ingredient",
              "inchikey_pubchem", "smiles_pubchem", "confidence", "resolver_version"]
    VERSION = "3"  # bump when resolution logic changes -> old cache auto-rebuilds

    def __init__(self, cache_path: Path, use_rxnorm=True, sleep=0.15):
        self.cache_path = cache_path
        self.use_rxnorm = use_rxnorm
        self.sleep = sleep
        self.cache: dict = {}
        self._client = None
        self._new = 0
        if cache_path.exists():
            with cache_path.open(encoding="utf-8") as f:
                rdr = csv.DictReader(f)
                rows = list(rdr) if set(rdr.fieldnames or []) == set(self.FIELDS) else None
            if rows is not None and all(r.get("resolver_version") == self.VERSION
                                        for r in rows):
                for r in rows:
                    self.cache[r["name"]] = r
            else:
                print(f"  (resolver/cache version changed -> rebuilding {cache_path.name})")

    def _mol(self):
        if self._client is None:
            from chembl_webresource_client.new_client import new_client
            self._client = new_client.molecule
        return self._client

    def _chembl(self, name):
        only = ["molecule_chembl_id", "molecule_structures", "molecule_type", "pref_name"]
        queries = [
            lambda: self._mol().filter(pref_name__iexact=name.upper()).only(only),
            lambda: self._mol().filter(
                molecule_synonyms__molecule_synonym__iexact=name).only(only),
        ]
        for q in queries:
            try:
                for i, m in enumerate(q()):
                    if i >= 5:
                        break
                    if m.get("molecule_type") != "Small molecule":   # the precision gate
                        continue
                    st = m.get("molecule_structures") or {}
                    if st.get("canonical_smiles"):
                        return (m.get("molecule_chembl_id"), st["canonical_smiles"],
                                st.get("standard_inchi_key"))
            except Exception:
                continue
        return None, None, None

    def _rxnorm(self, name):
        """messy name -> (ingredient_rxcui, ingredient_name) via RxNav EXACT/normalized
        lookup. approximateTerm was fabricating matches on codes (MS-553->morphine), so a
        code that is not a real drug now correctly returns nothing."""
        js = _get_json("https://rxnav.nlm.nih.gov/REST/rxcui.json"
                       f"?name={quote(name)}&search=1", self.sleep)   # search=1 = normalized
        ids = (js.get("idGroup") or {}).get("rxnormId") or []
        if not ids:
            return None, None
        rxcui = ids[0]
        js2 = _get_json(f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/related.json?tty=IN",
                        self.sleep)
        for grp in (js2.get("relatedGroup") or {}).get("conceptGroup") or []:
            if grp.get("tty") == "IN":
                props = grp.get("conceptProperties") or []
                if props:
                    return props[0].get("rxcui", rxcui), props[0].get("name")
        return rxcui, None                      # rxcui found but no ingredient roll-up

    def _pubchem(self, name):
        js = _get_json("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                       f"{quote(name)}/property/InChIKey,CanonicalSMILES/JSON", self.sleep)
        props = (js.get("PropertyTable") or {}).get("Properties") or []
        if props:
            return props[0].get("InChIKey"), props[0].get("CanonicalSMILES")
        return None, None

    def resolve(self, name):
        if name in self.cache:
            return self.cache[name]
        cid, smi_c, ik_c = self._chembl(name)
        rxcui, ing = self._rxnorm(name) if self.use_rxnorm else (None, None)
        # if the raw name missed in ChEMBL, retry with RxNorm's normalized ingredient --
        # recovers the chembl_id (needed for label (i)) and lets PubChem cross-check it,
        # upgrading e.g. "5Fluorouracil" -> fluorouracil -> CHEMBL185 (high).
        if not cid and ing:
            cid2, smi_c2, ik_c2 = self._chembl(ing)
            if cid2:
                cid, smi_c, ik_c = cid2, smi_c2, ik_c2
        ik_p, smi_p = self._pubchem(ing or name)
        # compare on the largest organic fragment so a parent and its salt/hydrate agree
        sk_c = _skeleton_from_smiles(smi_c) or _skeleton(ik_c)
        sk_p = _skeleton_from_smiles(smi_p) or _skeleton(ik_p)
        conf = confidence_from_skeletons(sk_c, sk_p, bool(cid), bool(ik_p))
        rec = {"name": name, "chembl_id": cid or "",
               "smiles": smi_c or smi_p or "", "inchikey_chembl": ik_c or "",
               "rxcui": rxcui or "", "ingredient": ing or "",
               "inchikey_pubchem": ik_p or "", "smiles_pubchem": smi_p or "",
               "confidence": conf, "resolver_version": self.VERSION}
        self.cache[name] = rec
        self._new += 1
        if self._new % 10 == 0:
            self.flush()
        return rec

    def flush(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.FIELDS)
            w.writeheader()
            for r in self.cache.values():
                w.writerow(r)


def _fp_ok(smiles, _cache={}):
    if not smiles:
        return False
    if smiles in _cache:
        return _cache[smiles]
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")
    ok = Chem.MolFromSmiles(smiles) is not None
    _cache[smiles] = ok
    return ok


def _build_di_index(di_path, name2, mesh2, efo2, keep_ids):
    """molecule_chembl_id -> [(max_phase, mondo_set, name_set)] for resolved ids only."""
    import pandas as pd
    di = pd.read_csv(di_path).fillna("")
    idx: dict = {}
    for _, r in di.iterrows():
        cid = r.get("molecule_chembl_id")
        if cid not in keep_ids:
            continue
        try:
            ph = float(r.get("max_phase_for_ind"))
        except (TypeError, ValueError):
            continue
        mondo = set()
        if r.get("mesh_id"):
            mondo |= mesh2.get(str(r["mesh_id"]).upper(), set())
        efo = str(r.get("efo_id") or "").upper()
        if efo.startswith("MONDO:"):
            mondo.add(efo)
        mondo |= efo2.get(efo, set())
        names = {_norm(r.get("mesh_heading")), _norm(r.get("efo_term"))} - {""}
        for nm in names:
            mondo |= name2.get(nm, set())
        idx.setdefault(cid, []).append((ph, mondo, names))
    return idx


def indication_label(chembl_ids, di_index, tmondo, tnames):
    """Best ChEMBL indication label for this trial: 1 if approved, 0 if failed, None."""
    best = None
    for cid in chembl_ids:
        for ph, mondo, names in di_index.get(cid, ()):
            if (mondo & tmondo) or (names & tnames):
                if best is None or ph > best:
                    best = ph
    if best == 4:
        return 1
    if best in (1.0, 2.0):
        return 0
    return None


def score_trials(trials, records, di_index, name2, mesh2, min_conf):
    """Per-trial coverage flags, counting only names resolved at >= min_conf. Pure."""
    rows = []
    for t in trials:
        coh = cohort_ok(t)
        cids, smis = [], []
        if coh:
            for raw in t.get("drug_or_biological_names") or []:
                for nm in drug_candidates(raw):
                    r = records.get(nm)
                    if r and conf_usable(r["confidence"], min_conf):
                        if r["chembl_id"]:
                            cids.append(r["chembl_id"])
                        if r["smiles"]:
                            smis.append(r["smiles"])
        tmondo = trial_mondo(t, name2, mesh2)
        tnames = {_norm(c) for c in (t.get("conditions") or [])} - {""}
        rows.append({
            "cohort": coh, "resolved": bool(cids),
            "fp_ok": any(_fp_ok(s) for s in smis),
            "ind_label": indication_label(cids, di_index, tmondo, tnames) if coh else None,
        })
    return rows


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=HF_LABELS, help="human_labels_2020_2024 (local or hf://)")
    ap.add_argument("--xref", default=Path("data/ontology/mondo_xref.csv"), type=Path)
    ap.add_argument("--chembl-dir", default=Path("data/chembl"), type=Path)
    ap.add_argument("--cache", default=Path("data/cto/name2chembl.csv"), type=Path)
    ap.add_argument("--limit", type=int, default=0, help="sample N trial ids (sanity check)")
    ap.add_argument("--no-rxnorm", action="store_true", help="ChEMBL+PubChem only, skip RxNorm")
    args = ap.parse_args()

    print(f"{'='*70}\nCTO MOLECULE + INDICATION COVERAGE AUDIT\n{'='*70}")
    print(f"reading trial ids from {args.labels} ...", flush=True)
    lab = pd.read_csv(args.labels, usecols=["nct_id", "labels"])
    if args.limit:
        lab = lab.head(args.limit)
    ids = [str(x) for x in lab["nct_id"].tolist()]
    gold = dict(zip(lab["nct_id"].astype(str), lab["labels"]))
    print(f"gold trials to audit: {len(ids)}")

    print("fetching trials from ClinicalTrials.gov v2 ...", flush=True)
    studies = fetch_batch(ids)
    trials = [extract_trial(s) for s in studies]
    print(f"fetched + parsed: {len(trials)}", flush=True)

    name2, mesh2, efo2, _icd2 = _load_mondo(args.xref)
    resolver = MultiSourceResolver(args.cache, use_rxnorm=not args.no_rxnorm)

    cohort = [t for t in trials if cohort_ok(t)]
    names = sorted({n for t in cohort
                    for x in (t.get("drug_or_biological_names") or [])
                    for n in drug_candidates(x)})
    n_cached = sum(1 for nm in names if nm in resolver.cache)
    print(f"cohort trials: {len(cohort)}   unique drug names: {len(names)} "
          f"({n_cached} already cached)", flush=True)
    src = "ChEMBL + PubChem" if args.no_rxnorm else "ChEMBL + RxNorm + PubChem"
    print(f"resolving via {src}, cross-checked by InChIKey (first run is slow) ...", flush=True)
    records = {}
    for i, nm in enumerate(names, 1):
        r = resolver.resolve(nm)
        records[nm] = r
        tag = r["chembl_id"] or (f"rx:{r['ingredient']}" if r["ingredient"] else "miss")
        print(f"  [resolve {i}/{len(names)}] {nm[:40]:40s} -> {tag:24s} ({r['confidence']})",
              flush=True)
    resolver.flush()

    keep_ids = {r["chembl_id"] for r in records.values() if r["chembl_id"]}
    print(f"scoring coverage ({len(keep_ids)} distinct ChEMBL molecules) ...", flush=True)
    di_index = _build_di_index(args.chembl_dir / "drug_indication.csv",
                               name2, mesh2, efo2, keep_ids)

    tiers = Counter(r["confidence"] for r in records.values())
    print(f"\n{'='*70}\nRESOLVER CONFIDENCE (per unique name, n={len(records)})\n{'='*70}")
    print(f"  high (ChEMBL+PubChem agree) : {tiers['high']}")
    print(f"  medium (single source)      : {tiers['medium']}")
    print(f"  conflict (sources disagree) : {tiers['conflict']}   <- review, treated unusable")
    print(f"  miss (biologic/coded/none)  : {tiers['miss']}")

    for band in ("high", "medium"):
        rows = score_trials(trials, records, di_index, name2, mesh2, band)
        s = summarize(rows)
        denom = s["cohort (intervent., drug arm, ph I-III)"]
        label = "HIGH-confidence only" if band == "high" else "HIGH + MEDIUM"
        print(f"\n{'='*70}\nCOVERAGE ceiling -- {label}\n{'='*70}")
        for k, v in s.items():
            pct = f"  ({100*v/denom:.1f}% of cohort)" if denom and k.startswith("  ") else ""
            print(f"  {k:42s}: {v}{pct}")

    conflicts = [n for n, r in records.items() if r["confidence"] == "conflict"]
    misses = [n for n, r in records.items() if r["confidence"] == "miss"]
    if conflicts:
        print(f"\n  CONFLICTS to review ({len(conflicts)}): {conflicts[:12]}")
    print(f"  example misses: {misses[:12]}")
    print(f"  cache -> {args.cache}")
    print("\n  band = the resolver-uncertainty range on the forward test. label (ii) = CTO")
    print("  gold covers ~all trials; label (i) = ChEMBL-derived is bounded by 'BOTH' above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())