"""Resolve a Trialtrove disease-type label (one of DSAI's 152) to MONDO anchor term(s).

This is the crosswalk half of the "disease-type-152 done properly" build: each label becomes
one or more MONDO anchor ids; later, a unit is tagged with a disease-type iff its own MONDO
term is that anchor OR a descendant of it (rollup via the is_a graph, a separate step).

Trialtrove labels have three quirks the matcher must handle, all pure/deterministic:
  - comma-swap ordering: "Lung, Non-Small Cell" -> "non small cell lung"; "Leukemia, Acute
    Myelogenous" -> "acute myelogenous leukemia".
  - parentheticals: "Pain (neuropathic)" -> try "neuropathic pain"; "(Oncology)"/"(CMV)" noise.
  - oncology organ-site convention (spec-stated): "Breast", "Renal", "Colorectal" etc. mean the
    CANCER of that organ, so every base variant is also tried with " cancer" / " carcinoma".
Nothing here is fuzzy/edit-distance -- matches are exact against normalized MONDO names +
exact synonyms, so a match is auditable. Unresolved labels are surfaced for manual curation
rather than force-matched.
"""
from __future__ import annotations

import re
from typing import Iterable

from trial_pos.services.disease_ta import _norm

_ONC_SUFFIXES = ("cancer", "carcinoma", "neoplasm")


def label_variants(label: str) -> list[str]:
    """Ordered, de-duplicated normalized strings to try (most-literal first)."""
    out: list[str] = []

    def add(s: str):
        n = _norm(s)
        if n and n not in out:
            out.append(n)

    paren = re.findall(r"\(([^)]*)\)", label)
    base = re.sub(r"\([^)]*\)", " ", label)          # label minus parentheticals
    add(base)
    add(label)
    if "," in base:                                   # "A, B" -> "B A"
        parts = [p.strip() for p in base.split(",") if p.strip()]
        add(" ".join(reversed(parts)))
    for p in paren:                                   # e.g. "Pain (neuropathic)" -> "neuropathic pain"
        add(f"{p} {base}")
        add(f"{base} {p}")
    return out


def resolve_label(label: str, name_index: dict) -> tuple[set, str, str]:
    """(mondo_ids, method, matched_variant). name_index: normalized-name -> set(mondo_id).

    Tries literal variants first (method 'exact'), then each variant + an oncology suffix
    (method 'onc-aug'); returns the first hit. Empty set + 'unresolved' if nothing matches.
    """
    variants = label_variants(label)
    for v in variants:                                # literal first
        if v in name_index:
            return set(name_index[v]), "exact", v
    for v in variants:                                # then oncology-augmented
        for suf in _ONC_SUFFIXES:
            vv = f"{v} {suf}"
            if vv in name_index:
                return set(name_index[vv]), "onc-aug", vv
    return set(), "unresolved", ""


def build_name_index(rows: Iterable[dict]) -> dict:
    """mondo_xref rows -> {normalized name-or-synonym: set(mondo_id)}."""
    idx: dict[str, set] = {}
    for r in rows:
        mid = r["mondo_id"]
        for nm in (r.get("name", ""), *str(r.get("exact_synonyms", "")).split("|")):
            n = _norm(nm)
            if n:
                idx.setdefault(n, set()).add(mid)
    return idx