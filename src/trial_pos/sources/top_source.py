"""TOP benchmark loader.

Schema confirmed against the TOP benchmark repo
(futianfan/clinical-trial-outcome-prediction, benchmark/README.md). The per-phase
outcome CSVs (phase_{I,II,III}_{train,valid,test}.csv, produced by data_split.py)
carry these columns:

    nctid, status, why_stop, label, phase, diseases, icdcodes, drugs, smiless, criteria

Notes that shape this loader:
- `drugs`, `diseases`, `icdcodes`, `smiless` are *list-valued* strings, e.g. "['a', 'b']".
- There is NO start_date / completion_date column here. TOP keeps dates in a separate
  file, data/nctid_date.txt (produced by nctid2date.py). So the date-based make_split
  drops every TOP row until dates are joined in. For TOP we evaluate on TOP's own
  shipped train/valid/test split instead (R2). See docs/Risks.md R1/R6.

If your download's header differs, run `python scripts/inspect_top.py <csv>` and adjust
_COL. The loader fails loudly (see fetch) if the id column is absent, rather than
silently emitting empty records.
"""
from __future__ import annotations
import ast
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from trial_pos.domain.records import Label, Provenance, TrialRecord
from trial_pos.foundation.contracts import Source
from trial_pos.foundation.identity import NCTId

# Confirmed against benchmark/README.md. Date columns are intentionally absent
# (TOP ships dates separately in nctid_date.txt); keep them here as optional so a
# joined-in file with dates still works, but expect them missing for raw TOP.
_COL = {
    "nct_id": "nctid",
    "phase": "phase",
    "status": "status",
    "why_stop": "why_stop",
    "label": "label",
    "drug": "drugs",
    "indication": "diseases",
    "icdcodes": "icdcodes",
    "smiless": "smiless",
    "criteria": "criteria",
    # optional, only present if you join nctid_date.txt onto the phase files:
    "start_date": "start_date",
    "completion_date": "completion_date",
}


def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%B %d, %Y", "%B %Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _as_list(s) -> list[str]:
    """TOP list columns are Python-literal strings like "['a', 'b']". Parse robustly."""
    if s is None:
        return []
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            val = ast.literal_eval(s)
            if isinstance(val, (list, tuple)):
                return [str(x).strip() for x in val if str(x).strip()]
        except (ValueError, SyntaxError):
            pass
    # fall back to common delimiters
    for sep in (";", "|", ","):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    return [s]


def _first(xs: list[str]):
    return xs[0] if xs else None


def _to_int_label(lbl):
    if lbl in (None, "", "nan", "NaN"):
        return None
    try:
        return int(float(lbl))
    except (TypeError, ValueError):
        return None


class TopSource(Source):
    name = "top"

    def __init__(self, csv_path):
        self.csv_path = Path(csv_path)

    def fetch(self) -> Any:
        import csv
        with self.csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            cols = set(reader.fieldnames or [])
        if _COL["nct_id"] not in cols:
            raise KeyError(
                f"{self.csv_path}: id column {_COL['nct_id']!r} not in header {sorted(cols)}. "
                f"Run scripts/inspect_top.py and update _COL in top_source.py."
            )
        return rows

    def normalize(self, raw: Any) -> list[dict]:
        out = []
        for r in raw:
            drugs = _as_list(r.get(_COL["drug"]))
            diseases = _as_list(r.get(_COL["indication"]))
            icds = _as_list(r.get(_COL["icdcodes"]))
            smiless = _as_list(r.get(_COL["smiless"]))
            why = (r.get(_COL["why_stop"]) or "").strip()
            criteria = (r.get(_COL["criteria"]) or "").strip()
            out.append({
                "nct_id": (r.get(_COL["nct_id"]) or "").strip(),
                "phase": r.get(_COL["phase"]),
                "status": r.get(_COL["status"]),
                "start_date": _parse_date(r.get(_COL["start_date"])),
                "completion_date": _parse_date(r.get(_COL["completion_date"])),
                "label": r.get(_COL["label"]),
                "drug": _first(drugs),
                "indication": _first(diseases),
                # source-derived numeric signals carried through to features:
                "n_drugs": len(drugs),
                "n_diseases": len(diseases),
                "n_icd": len(icds),
                "n_smiles": len(smiless),
                "has_why_stop": int(bool(why)),
                "criteria_len": len(criteria),
            })
        return out

    def to_domain(self, rows: Iterable[dict]) -> list[TrialRecord]:
        out = []
        for row in rows:
            if not row["nct_id"]:
                continue
            rec = TrialRecord(
                nct_id=NCTId(row["nct_id"]),
                phase=row["phase"], status=row["status"],
                start_date=row["start_date"], completion_date=row["completion_date"],
                drug=row["drug"], indication=row["indication"],
                label=Label(_to_int_label(row["label"]), Provenance.MEASURED),
                source=self.name,
            )
            # Stash source-derived features; pipeline.build_records merges these with
            # the generic trial features so they survive into the model matrix.
            rec.features = {
                "n_drugs": row["n_drugs"],
                "n_diseases": row["n_diseases"],
                "n_icd": row["n_icd"],
                "n_smiles": row["n_smiles"],
                "has_why_stop": row["has_why_stop"],
                "criteria_len": row["criteria_len"],
            }
            out.append(rec)
        return out