#!/usr/bin/env python3
"""Audit the CTO (Gao et al., 2024) HuggingFace dataset BEFORE building a source.

Project rhythm: measure before you build. The CTO CSVs carry outcome labels + dates +
phase but NOT the fields our spine runs on -- SMILES, InChIKey, conditions, ICD. Those
have to come from CTTI.zip (the AACT/CTTI dump) plus the drug-name->structure and
disease->ontology maps we already built for TOP. This script tells us exactly what is
present and where, so the join plan for cto_source.py is grounded in fact, not guesses.

Three modes (pick one):
  (default)      audit chufangao/CTO on HuggingFace over the network
  --local-dir D  audit a local mirror directory (walks the tree)
  --zip FILE     peek INSIDE a .zip (e.g. CTTI.zip) without extracting it to disk

It does NOT commit to any modeling or label choice. Pure audit.

Deps:  pip install huggingface_hub pyarrow pandas fsspec
Usage: python scripts/inspect_cto.py
       python scripts/inspect_cto.py --repo-id chufangao/CTO
       python scripts/inspect_cto.py --local-dir data/cto
       python scripts/inspect_cto.py --zip data/cto/CTTI.zip
"""
from __future__ import annotations
import argparse
import io
import zipfile
from pathlib import Path

# Fields the spine needs, and the substrings that would identify each in a column name.
NEEDS = {
    "nct_id":            ["nct", "nct_id", "nctid"],
    "smiles":            ["smiles", "smiless", "canonical_smiles"],
    "inchikey":          ["inchikey", "inchi_key", "standard_inchi_key", "inchi"],
    "drug/intervention": ["intervention", "compound", "molecule", "agent", "drug_name"],
    "condition/disease": ["condition", "disease", "indication", "mesh", "diagnosis",
                          "downcase_mesh", "browse"],
    "icd":               ["icd", "icdcode", "icd10", "icd_10"],
    "eligibility":       ["eligibilit", "inclusion_criteria", "exclusion_criteria",
                          "criteria_text"],
    "phase":             ["phase"],
    "date":              ["start_date", "completion_date", "primary_completion",
                          "study_first"],
    "label/outcome":     ["label", "outcome", "success"],
}
TABULAR = (".parquet", ".csv", ".tsv", ".txt")


def _fmt_mb(n):
    return "?" if n is None else f"{n / 1e6:,.1f} MB"


# ---- readers ---------------------------------------------------------------
def _schema_parquet_stream(fileobj, n=3):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(fileobj)
    cols = [(f.name, str(f.type)) for f in pf.schema_arrow]
    nrows = pf.metadata.num_rows
    head = None
    if pf.num_row_groups:
        head = pf.read_row_group(0).slice(0, n).to_pandas()
    return cols, nrows, head


def _sniff_sep(sample: bytes, is_txt: bool):
    """Pick the delimiter by counting candidates on the header line. pandas' own
    sniffer is unreliable on 3 rows; AACT .txt dumps are pipe-delimited."""
    text = sample.decode("utf-8", "ignore")
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {d: line.count(d) for d in ("|", "\t", ",", ";")}
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        return "|" if is_txt else ","
    return best


def _schema_csv_stream(fileobj, sep, n=3):
    import pandas as pd
    df = pd.read_csv(fileobj, sep=sep, engine="c", nrows=n)
    return [(c, str(df[c].dtype)) for c in df.columns], None, df


def _report(name, cols, nrows, head, found):
    if nrows is not None:
        print(f"  rows: {nrows:,}   cols: {len(cols)}")
    else:
        print(f"  cols: {len(cols)}  (row count not read)")
    for c, t in cols:
        print(f"    - {c}: {t}")
    _scan_needs(found, name, [c for c, _ in cols])
    if head is not None and len(head):
        print("  head(3):")
        print("    " + head.to_string().replace("\n", "\n    "))


def _scan_needs(found, filename, columns):
    lc = [c.lower() for c in columns]
    for need, subs in NEEDS.items():
        for col, low in zip(columns, lc):
            if any(sub in low for sub in subs):
                found.setdefault(need, []).append(f"{filename}:{col}")


# ---- listing ---------------------------------------------------------------
def _list_hf(repo_id):
    from huggingface_hub import HfApi
    info = HfApi().repo_info(repo_id, repo_type="dataset", files_metadata=True)
    return [(s.rfilename, getattr(s, "size", None)) for s in info.siblings]


def _list_local(root: Path):
    return [(str(p.relative_to(root)), p.stat().st_size)
            for p in sorted(root.rglob("*")) if p.is_file()]


# ---- modes -----------------------------------------------------------------
def _run_files(files, opener, head_max_mb, max_files, found):
    tabular = [(r, s) for r, s in files if r.lower().endswith(TABULAR)]
    print("\n-- file manifest (path : size) --")
    total = 0
    for rel, sz in files:
        total += sz or 0
        tag = "  <-- tabular" if rel.lower().endswith(TABULAR) else ""
        print(f"  {rel}  :  {_fmt_mb(sz)}{tag}")
    print(f"\n  total: {_fmt_mb(total)} across {len(files)} files ({len(tabular)} tabular)")

    print(f"\n{'='*70}\nPER-TABLE SCHEMA\n{'='*70}")
    for i, (rel, sz) in enumerate(tabular):
        if max_files and i >= max_files:
            print(f"\n(stopped after --max-files={max_files})"); break
        print(f"\n### {rel}  ({_fmt_mb(sz)})")
        try:
            low = rel.lower()
            do_head = sz is not None and sz < head_max_mb * 1e6
            if low.endswith(".parquet"):
                with opener(rel, "parquet") as f:
                    cols, nrows, head = _schema_parquet_stream(f)
                _report(rel, cols, nrows, head if do_head else None, found)
            else:
                with opener(rel, "csv") as f0:
                    sep = _sniff_sep(f0.read(65536), low.endswith(".txt"))
                with opener(rel, "csv") as f:
                    cols, nrows, head = _schema_csv_stream(f, sep)
                _report(rel, cols, nrows, head, found)
        except Exception as e:
            print(f"  !! could not read: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-id", default="chufangao/CTO")
    ap.add_argument("--local-dir", type=Path, default=None)
    ap.add_argument("--zip", type=Path, default=None, help="peek inside a .zip in place")
    ap.add_argument("--head-max-mb", type=float, default=25.0)
    ap.add_argument("--member-cap-mb", type=float, default=400.0,
                    help="skip zip members larger than this")
    ap.add_argument("--max-files", type=int, default=0)
    args = ap.parse_args()
    found: dict = {}

    if args.zip is not None:
        print(f"{'='*70}\nCTO AUDIT  (zip:{args.zip})\n{'='*70}")
        with zipfile.ZipFile(args.zip) as zf:
            infos = sorted(zf.infolist(), key=lambda z: z.filename)
            files = [(z.filename, z.file_size) for z in infos if not z.is_dir()]

            def opener(rel, kind):
                zi = zf.getinfo(rel)
                if kind == "parquet":            # parquet needs seek -> full member in RAM
                    return io.BytesIO(zf.read(zi))
                return zf.open(zi)               # csv/txt stream, read_csv stops after nrows
            # apply member cap by pre-filtering the tabular list
            files = [(r, s) for r, s in files
                     if not r.lower().endswith(TABULAR) or s <= args.member_cap_mb * 1e6]
            _run_files(files, opener, args.head_max_mb, args.max_files, found)
    elif args.local_dir is not None:
        print(f"{'='*70}\nCTO AUDIT  (local:{args.local_dir})\n{'='*70}")
        files = _list_local(args.local_dir)

        def opener(rel, kind):
            return open(Path(args.local_dir) / rel, "rb")
        _run_files(files, opener, args.head_max_mb, args.max_files, found)
    else:
        print(f"{'='*70}\nCTO AUDIT  (hf:{args.repo_id})\n{'='*70}")
        files = _list_hf(args.repo_id)
        import fsspec
        fs = fsspec.filesystem("hf")

        def opener(rel, kind):
            return fs.open(f"hf://datasets/{args.repo_id}/{rel}", "rb")
        _run_files(files, opener, args.head_max_mb, args.max_files, found)

    print(f"\n{'='*70}\nFEATURE AVAILABILITY  (what the spine needs -> where found)\n{'='*70}")
    for need in NEEDS:
        hits = found.get(need, [])
        if hits:
            shown = ", ".join(hits[:6]) + (" ..." if len(hits) > 6 else "")
            print(f"  [FOUND]   {need:18s} -> {shown}")
        else:
            print(f"  [MISSING] {need:18s} -> not seen in any column name")
    missing = [n for n in NEEDS if n not in found]
    print(f"\n  verdict: {'all present' if not missing else 'MISSING ' + ', '.join(missing)}")
    if missing:
        print("  -> reconstruct these with the TOP-style maps (drug-name->SMILES/ChEMBL,\n"
              "     condition->MONDO/ICD) so CTO features are built the SAME way as TOP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())