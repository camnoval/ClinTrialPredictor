"""Drug-name normalisation for trial-to-drug resolution. Pure.

A registry intervention name is free text: "Pembrolizumab 200 mg IV Q3W", "MK-3475
(pembrolizumab)", "Metformin HCl ER tablets", "Drug X or placebo", "adalimumab-aacf". The
dictionaries it is matched against (DrugCentral, Drugs@FDA) hold clean names. This module
turns one name into an ORDERED list of candidate keys, each tagged with the step that made
it, so the resolver can stop at the first key that hits and record which step it took. The
hand-labelled sample then measures precision per step: a step that is not precise enough
is dropped by name, without touching the others.

No fuzzy matching (decided 2026-10-07): every step is a deterministic rewrite.

Placebo, sham, vehicle and dummy are never tested agents. A component that IS one is
dropped; "X or placebo" keeps X.
"""
from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# ---- keys ---------------------------------------------------------------------------------
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")
_EDGE = " .,;:-_*'\"`/+"
_NON_ALNUM = re.compile(r"[^0-9a-z]")


def key(raw) -> str:
    """NFKC, casefold, typographic dashes to '-', whitespace collapsed, edges trimmed."""
    s = unicodedata.normalize("NFKC", str(raw or "")).translate(_DASHES).casefold()
    return " ".join(s.split()).strip(_EDGE)


def compact(raw) -> str:
    """Letters and digits only: 'MK-3475', 'mk 3475' and 'MK3475' share one compact key."""
    return _NON_ALNUM.sub("", key(raw))


# ---- combinations and placebo ---------------------------------------------------------------
# Separators that join DIFFERENT agents in one registry name. Commas are deliberately absent:
# Drugs@FDA writes "ACETIC ACID, GLACIAL" and "POTASSIUM PHOSPHATE, DIBASIC" with them
# (audit/probe_source_files.py section 4e), and registry names do the same.
COMBINATION_SPLIT = re.compile(r"\s*(?:\+|/|;|&|\|\||\bplus\b|\band\b|\bor\b|\bwith\b)\s*")

PLACEBO_PATTERN = re.compile(
    r"^(?:matching\s+|matched\s+|identical\s+)?(?:placebo|sham|vehicle|dummy)\b"
    r"|^(?:normal\s+)?saline\b")


# A component shorter than this is debris from splitting ('B/E' -> 'e'), never a drug name.
MIN_COMPONENT_LENGTH = 2


def _parts(raw, min_length: int) -> list:
    undosed = _DOSE.sub(" ", key(raw))
    parts = (key(part) for part in COMBINATION_SPLIT.split(undosed))
    return [p for p in parts if p and len(p) >= min_length]


def components(raw) -> list:
    """A name split into its agents. Doses are removed FIRST, because units such as
    'nmol/kg' and ratios such as '70/30' carry the same '/' that joins two agents."""
    return _parts(raw, MIN_COMPONENT_LENGTH)


def is_placebo(raw) -> bool:
    """True when the name IS a placebo-type control, not when it merely mentions one."""
    return bool(PLACEBO_PATTERN.search(key(raw)))


def active_components(raw) -> list:
    """Components that are not placebo-type controls. A placebo-LED name ('Placebo for
    AIDSVAX B/E') is placebo as a whole, whatever its parts look like."""
    if is_placebo(raw):
        return []
    return [c for c in components(raw) if not is_placebo(c)]


def is_pure_placebo(raw) -> bool:
    """The name is a placebo-type control: placebo-led, or made only of placebo components.
    A name with no components left at all ('X', a code) is NOT placebo: it stays a
    candidate and shows up as unresolved."""
    if is_placebo(raw):
        return True
    parts = _parts(raw, 1)          # unfiltered: 'X or placebo' must not lose its X here
    return bool(parts) and all(is_placebo(c) for c in parts)


# ---- rewrites -------------------------------------------------------------------------------
_PARENS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_PAREN_CONTENT = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")

DOSE_UNITS = ("mg/kg", "mg/m2", "mg/m\u00b2", "mcg/kg", "ug/kg", "\u00b5g/kg", "nmol/kg",
              "mg/ml", "miu", "mg", "mcg", "\u00b5g", "ug", "ng", "g", "kg", "ml", "l", "iu", "u",
              "units", "unit", "mmol", "meq", "%")
# A number is a dose only when it stands alone: never a digit run inside a code such as
# 'mk-3475', 'covid-19' or 'alfa-2b'.
_NUM_BEFORE = r"(?<![a-z0-9\-.,])"
_NUM_AFTER = r"(?![a-z0-9\-])"
_DOSE = re.compile(
    _NUM_BEFORE + r"\d+(?:[.,]\d+)?\s*(?:" + "|".join(re.escape(u) for u in DOSE_UNITS)
    + r")(?![a-z0-9])|" + _NUM_BEFORE + r"\d+(?:[.,]\d+)?" + _NUM_AFTER)

FORM_WORDS = frozenset({
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "injection",
    "injectable", "infusion", "oral", "intravenous", "iv", "subcutaneous", "sc", "sq",
    "intramuscular", "im", "topical", "solution", "suspension", "cream", "gel", "ointment",
    "patch", "spray", "inhaler", "inhalation", "nasal", "ophthalmic", "drops", "powder",
    "film", "coated", "extended", "release", "extended-release", "delayed-release",
    "modified-release", "er", "xr", "sr", "xl", "cr", "dr", "dose", "doses", "daily",
    "once", "twice", "bid", "tid", "qd", "qid", "q3w", "q2w", "q4w", "weekly",
})

SALT_WORDS = frozenset({
    "hydrochloride", "hcl", "dihydrochloride", "hydrobromide", "sodium", "potassium",
    "calcium", "magnesium", "mesylate", "mesilate", "maleate", "sulfate", "sulphate",
    "tartrate", "bitartrate", "citrate", "acetate", "phosphate", "fumarate", "succinate",
    "besylate", "besilate", "bromide", "tosylate", "lactate", "gluconate", "malate",
    "monohydrate", "dihydrate", "trihydrate", "hydrate", "anhydrous", "dipropionate",
    "propionate", "valerate", "free", "base", "disodium", "meglumine", "trometamol",
    "tromethamine",
})

# FDA's four-letter biosimilar suffix: adalimumab-aacf. Applied only to a final hyphenated
# run of exactly four letters, so 'interferon alfa-2b' is untouched.
BIOSIMILAR_SUFFIX = re.compile(r"-[a-z]{4}$")


def drop_parentheticals(k: str) -> str:
    return " ".join(_PARENS.sub(" ", k).split())


def parenthetical_contents(k: str) -> list:
    return [key(a or b) for a, b in _PAREN_CONTENT.findall(k) if key(a or b)]


def drop_dose_and_form(k: str) -> str:
    s = _DOSE.sub(" ", k)
    return " ".join(w for w in s.split() if w.strip(_EDGE) not in FORM_WORDS).strip(_EDGE)


def drop_salt(k: str) -> str:
    words = k.split()
    while len(words) > 1 and words[-1].strip(_EDGE) in SALT_WORDS:
        words.pop()
    return " ".join(words)


def drop_biosimilar_suffix(k: str) -> str:
    return BIOSIMILAR_SUFFIX.sub("", k)


# ---- ordered variants -------------------------------------------------------------------------
STEP_EXACT = "exact"
STEP_NO_PARENTHETICALS = "no_parentheticals"
STEP_PARENTHETICAL = "parenthetical_content"
STEP_NO_DOSE_FORM = "no_dose_or_form"
STEP_NO_SALT = "no_salt"
STEP_NO_BIOSIMILAR_SUFFIX = "no_biosimilar_suffix"
STEP_COMPACT = "compact"
STEPS = (STEP_EXACT, STEP_NO_PARENTHETICALS, STEP_PARENTHETICAL, STEP_NO_DOSE_FORM,
         STEP_NO_SALT, STEP_NO_BIOSIMILAR_SUFFIX, STEP_COMPACT)

INDEX_EXACT = "exact"
INDEX_COMPACT = "compact"


class Variant(NamedTuple):
    step: str
    key: str
    index: str          # which dictionary index to look it up in


def variants(raw) -> list:
    """Candidate keys in the order the resolver tries them. Each key appears once, at its
    first step. The compact form is tried last, for every earlier key."""
    base = key(raw)
    out, seen = [], set()

    def add(step, k, index=INDEX_EXACT):
        if k and (index, k) not in seen:
            seen.add((index, k))
            out.append(Variant(step, k, index))

    add(STEP_EXACT, base)
    no_paren = drop_parentheticals(base)
    add(STEP_NO_PARENTHETICALS, no_paren)
    for inner in parenthetical_contents(base):
        add(STEP_PARENTHETICAL, inner)
    stripped = [drop_dose_and_form(k) for k in [base, no_paren] + parenthetical_contents(base)]
    for k in stripped:
        add(STEP_NO_DOSE_FORM, k)
    salted = [drop_salt(k) for k in stripped]
    for k in salted:
        add(STEP_NO_SALT, k)
    for k in salted:
        add(STEP_NO_BIOSIMILAR_SUFFIX, drop_biosimilar_suffix(k))
    for v in list(out):
        add(STEP_COMPACT, compact(v.key), INDEX_COMPACT)
    return out
