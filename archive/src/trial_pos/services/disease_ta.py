"""DSAI therapeutic-area (TA-9) assignment -- pure, multi-hot, offline-testable.

DSAI's `strtherapeuticarea` is a `|`-separated list: a unit can belong to several TAs at
once (hence `newtanos` = "how many TAs apply, 1-4"). We keep ALL applicable tags -- specific
-> general is a free union later; general -> specific is unrecoverable. The engine multi-hot
upgrade (a following step) consumes these; this step's job is the ASSIGNMENT + a coverage
audit, since open-data TA coverage is the unknown we must measure before investing.

The TA-9 scheme (spec Table S3 ta1..ta9):
  ta1 Autoimmune/Inflammation   ta2 Cardiovascular      ta3 CNS
  ta4 Genitourinary             ta5 Infectious Disease  ta6 Metabolic/Endocrinology
  ta7 Oncology                  ta8 Ophthalmology       ta9 Vaccines (Infectious Disease)

Honesty (R11 / spec "BUILDABLE via mondo_xref", but APPROX):
  - TA-9 is a Novartis-custom scheme; several ICD-10 chapters have NO TA bucket
    (respiratory J, digestive K, most skin L, ENT H60-H95, musculoskeletal-non-inflammatory).
    Those units legitimately get ZERO TAs -- that is the "Other" of TA-9, not a mapping bug.
  - ta9 Vaccines is an INTERVENTION property (drug-name "vaccine"), not a disease property,
    so it cannot come from a disease->TA map; it is assigned on the drug side. We add only a
    weak name-keyword hook here and flag that ta9 is drug-derived.
  - Two levels of evidence, unioned: ICD-10 chapter/sub-range (structured, primary) and
    MONDO name keywords (fallback + catches immune/CNS cases the chapter level misses).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

TA_NAMES = {
    "ta1": "Autoimmune/Inflammation", "ta2": "Cardiovascular", "ta3": "CNS",
    "ta4": "Genitourinary", "ta5": "Infectious Disease", "ta6": "Metabolic/Endocrinology",
    "ta7": "Oncology", "ta8": "Ophthalmology", "ta9": "Vaccines (Infectious Disease)",
}

_ICD = re.compile(r"^([A-Za-z])(\d{2})")


def ta_from_icd10(code) -> set[str]:
    """One ICD-10 code -> TA tokens (may be empty = no TA-9 bucket for that chapter)."""
    m = _ICD.match(str(code).strip())
    if not m:
        return set()
    ch, n = m.group(1).upper(), int(m.group(2))
    if ch in ("A", "B"):
        return {"ta5"}                                   # infectious & parasitic
    if ch == "C":
        return {"ta7"}                                   # malignant neoplasms
    if ch == "D":
        if n <= 48:
            return {"ta7"}                               # D00-D48 in-situ/benign/uncertain neoplasms
        if 80 <= n <= 89:
            return {"ta1"}                               # D80-D89 immune-mechanism disorders
        return set()                                     # D50-D77 blood -> no TA bucket
    if ch == "E":
        return {"ta6"}                                   # endocrine/nutritional/metabolic
    if ch in ("F", "G"):
        return {"ta3"}                                   # mental/behavioural + nervous system
    if ch == "H":
        return {"ta8"} if n <= 59 else set()             # H00-H59 eye; H60-H95 ear -> none
    if ch == "I":
        return {"ta2"}                                   # circulatory
    if ch == "L":
        return {"ta1"} if (n == 40 or 20 <= n <= 30) else set()   # psoriasis, dermatitis/eczema
    if ch == "M":
        return {"ta1"} if (n == 8 or 5 <= n <= 14 or 30 <= n <= 36 or 45 <= n <= 46) else set()
    if ch == "N":
        return {"ta4"}                                   # genitourinary
    return set()                                         # J/K/O/P/Q/R/S-T/V-Z -> no TA bucket


# name-keyword -> TA tokens (substring match on a normalized name; multi-hot by design)
_NAME_RULES: list[tuple[tuple[str, ...], set[str]]] = [
    (("oncolog", "cancer", "carcinoma", "tumor", "tumour", "neoplasm", "sarcoma", "melanoma",
      "leukemia", "leukaemia", "lymphoma", "myeloma", "glioma", "glioblastoma", "blastoma",
      "malignan"), {"ta7"}),
    (("infect", "sepsis", "hepatitis", "tuberculos", "malaria", "hiv", "influenza", "pneumon",
      "bacteri", "viral", "virus"), {"ta5"}),
    (("vaccine", "vaccination"), {"ta9", "ta5"}),
    (("autoimmun", "rheumat", "arthritis", "lupus", "psoriasis", "psoriatic", "crohn", "colitis",
      "inflammat", "scleroderma", "sjogren", "spondyl", "dermatitis", "atopic"), {"ta1"}),
    (("diabet", "obesity", "metabolic", "thyroid", "dyslipidem", "hyperlipid", "endocrin",
      "osteoporos", "gout", "hyperuricem"), {"ta6"}),
    (("cardiac", "cardiovascular", "coronary", "myocard", "heart failure", "hypertension",
      "arrhythm", "atrial", "angina", "thrombo", "atheroscler"), {"ta2"}),
    (("alzheimer", "parkinson", "epilep", "seizure", "depress", "schizophren", "anxiety",
      "migraine", "dementia", "psychos", "bipolar", "neuropath", "huntington"), {"ta3"}),
    (("multiple sclerosis",), {"ta3", "ta1"}),           # demyelinating: CNS + autoimmune
    (("stroke", "cerebrovascular"), {"ta2", "ta3"}),     # vascular + CNS
    (("glaucoma", "retina", "macular", "ophthalm", "ocular", "uveitis", "conjunctiv",
      "dry eye"), {"ta8"}),
    (("renal", "kidney", "nephro", "bladder", "prostate", "urinary", "incontinence",
      "cystitis", "urothel"), {"ta4"}),
]


def _norm(name: str) -> str:
    # fold diacritics (Sjögren -> sjogren, Ménière -> meniere) before stripping punctuation
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['\u2019]s\b", "", s)          # possessive: crohn's -> crohn, sjogren's -> sjogren
    s = re.sub(r"['\u2019]", "", s)             # other apostrophes dropped without splitting
    n = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", n).strip()


def ta_from_name(name) -> set[str]:
    n = _norm(name)
    out: set[str] = set()
    if not n:
        return out
    for keys, tas in _NAME_RULES:
        if any(k in n for k in keys):
            out |= tas
    return out


_MESH_RE = re.compile(r"^(MESH:)?[CD]\d{6}$", re.I)


def build_mondo_resolver(xref_rows: Iterable[dict]):
    """Return resolve(indication) -> (set(mondo_id), path). Shared by the TA and disease-type
    builders so a unit's indication is mapped to MONDO the same way in both. Tries a literal
    MONDO id, then EFO id, then MeSH id, then normalized name/synonym."""
    efo2, mesh2, name2 = {}, {}, {}
    for r in xref_rows:
        mid = r["mondo_id"]
        for x in str(r.get("efo", "")).split("|"):
            if x:
                efo2.setdefault(x.upper(), set()).add(mid)
        for x in str(r.get("mesh", "")).split("|"):
            if x:
                mesh2.setdefault(x.upper(), set()).add(mid)
        for nm in (r.get("name", ""), *str(r.get("exact_synonyms", "")).split("|")):
            n = _norm(nm)
            if n:
                name2.setdefault(n, set()).add(mid)

    def resolve(indication):
        k = str(indication).strip()
        up = k.upper()
        if up.startswith("MONDO:"):
            return {k}, "mondo"
        if up in efo2:
            return set(efo2[up]), "efo"
        if _MESH_RE.match(k):
            mk = up.replace("MESH:", "")
            if mk in mesh2:
                return set(mesh2[mk]), "mesh"
        n = _norm(k)
        if n in name2:
            return set(name2[n]), "name"
        return set(), "unresolved"

    return resolve


def assign_ta(icd_codes: Iterable[str] = (), names: Iterable[str] = ()) -> set[str]:
    """Union of ICD-chapter and name-keyword evidence -> multi-hot TA token set (possibly empty)."""
    out: set[str] = set()
    for c in icd_codes:
        out |= ta_from_icd10(c)
    for nm in names:
        out |= ta_from_name(nm)
    return out