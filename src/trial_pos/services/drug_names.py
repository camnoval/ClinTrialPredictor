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


# Commas, LAST RESORT and registry names only (never Drugs@FDA ingredient strings). A comma
# between two digits is a chemical locant ('2,4-dinitrophenol') and is never split.
COMMA_SPLIT = re.compile(r"(?<!\d),|,(?!\d)")


def comma_components(raw) -> list:
    """Active components after splitting on commas as well as the agent separators."""
    if is_placebo(raw):
        return []
    out = []
    for piece in COMMA_SPLIT.split(key(raw)):
        out.extend(c for c in components(piece) if not is_placebo(c))
    return out


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
# ...and never the front of a locant or decimal ('2,4-dinitrophenol', '1,25-dihydroxy').
_NUM_AFTER = r"(?![a-z0-9\-]|[.,]\d)"
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
    # Added 2026-10-07 from audit/probe_unresolved.py section 2: formulation qualifiers on
    # the same DrugCentral drug ('liposomal bupivacaine', 'unfractionated heparin',
    # 'buprenorphine transdermal patch'). 'pegylated' is deliberately absent: pegfilgrastim
    # and filgrastim are different drugs.
    "liposomal", "unfractionated", "transdermal", "low-dose", "high-dose",
    # Inhaler devices, added with D-20 ('albuterol dpi 25 mcg/inh').
    "dpi", "mdi", "pmdi",
})

# Two-word qualifiers, removed as PHRASES so 'low' alone is never stripped ('low molecular
# weight heparin' must stay whole).
QUALIFIER_PHRASES = re.compile(r"\b(?:low|high)[\s\-]dose\b")

SALT_WORDS = frozenset({
    "hydrochloride", "hcl", "dihydrochloride", "hydrobromide", "sodium", "potassium",
    "calcium", "magnesium", "mesylate", "mesilate", "maleate", "sulfate", "sulphate",
    "tartrate", "bitartrate", "citrate", "acetate", "phosphate", "fumarate", "succinate",
    "besylate", "besilate", "bromide", "tosylate", "lactate", "gluconate", "malate",
    "monohydrate", "dihydrate", "trihydrate", "hydrate", "anhydrous", "dipropionate",
    "propionate", "valerate", "free", "base", "disodium", "meglumine", "trometamol",
    "tromethamine",
    # Esters and further counter-ions, added 2026-10-07. Sibling forms must strip to the
    # SAME root so the dictionary-side salt step sees them collide and calls the root
    # ambiguous: without 'furoate', 'fluticasone' reached the propionate alone. Words that
    # ARE the drug ('chloride', 'carbonate', 'oxide') and 'mofetil' stay out.
    "furoate", "acetonide", "hexacetonide", "butyrate", "pivalate", "palmitate",
    "decanoate", "enanthate", "cypionate", "undecanoate", "benzoate", "pamoate", "embonate",
    "nitrate", "oxalate", "hyclate", "xinafoate", "stearate", "ethylsuccinate", "aspartate",
    "napsylate", "esylate", "edisylate", "camsylate", "hemifumarate", "hemitartrate",
})

# Dictionary-side salt stripping (drug_dictionary.REVERSE_SALT) never matches a bare
# element: 'copper' or 'barium' would otherwise reach whichever copper or barium compound
# the dictionary happens to hold (audit/probe_unresolved.py: copper -> copper sulfate).
ELEMENT_ROOTS = frozenset({
    "aluminium", "aluminum", "barium", "bismuth", "calcium", "chromium", "cobalt", "copper",
    "gallium", "gold", "iron", "lithium", "magnesium", "manganese", "platinum", "potassium",
    "selenium", "silver", "sodium", "strontium", "tin", "zinc", "iodine", "fluoride",
})


def has_salt_word(k: str) -> bool:
    return any(w.strip(_EDGE) in SALT_WORDS for w in k.split())


# FDA's four-letter biosimilar suffix: adalimumab-aacf. Applied only to a final hyphenated
# run of exactly four letters, so 'interferon alfa-2b' is untouched, and only when what is
# left is a biologic (BIOLOGIC_STEMS, or an insulin): 'latanoprost-ppds', 'tace-haic',
# 'ibrutinib-rice' are not biosimilars (resolve_trial_drugs route review, 2026-10-07).
BIOSIMILAR_SUFFIX = re.compile(r"-[a-z]{4}$")
BIOLOGIC_STEMS = ("mab", "cept", "ase", "kin", "stim", "poetin", "vec", "cel", "vedotin",
                  "tecan", "tansine", "tropin", "cog", "ermin", "gene")
BIOLOGIC_WORDS = frozenset({"insulin"})

# Abbreviations ('inh', 'bal', 'dv', 'ats', 'tace') collide with DrugCentral synonyms of
# unrelated drugs ('albuterol ... mcg/inh' -> isoniazid, 'adcc & tace' -> chlorotrianisene).
# A key whose compact form is this short and letters-only may match only as an exact WHOLE
# intervention name (D-20). Digits keep codes usable ('5-fu', 's-1'). Up to
# MAX_ABBREVIATION_LENGTH letters is always an abbreviation; one letter longer only when the
# trial wrote it in capitals ('TACE'), so lowercase drug words ('iron', 'zinc') still match.
MAX_ABBREVIATION_LENGTH = 3
CAPITALISED_ABBREVIATION_LENGTH = 4


def is_abbreviation(k, original: str = "") -> bool:
    """`k` is a candidate key; `original` the name as the registry wrote it."""
    c = compact(k)
    if not c.isalpha() or not c:
        return False
    if len(c) <= MAX_ABBREVIATION_LENGTH:
        return True
    if len(c) == CAPITALISED_ABBREVIATION_LENGTH and original:
        return re.search(rf"(?<![A-Za-z]){re.escape(c.upper())}(?![A-Za-z])",
                         str(original)) is not None
    return False


def is_biologic_root(k: str) -> bool:
    return any(w in BIOLOGIC_WORDS or w.endswith(BIOLOGIC_STEMS) for w in k.split())


# A bracket holding only an isotope ('[18f]', '[68ga]', '[99mtc]', '(177lu)') is part of a
# radiotracer's identity: removing it turned '[18f]t4' into levothyroxine (D-23).
ISOTOPE = re.compile(r"^\s*\d{1,3}\s*m?\s*[a-z]{1,2}\s*$")


def _drop_unless_isotope(m) -> str:
    inner = m.group(0)[1:-1]
    return m.group(0) if ISOTOPE.match(inner) else " "


def drop_parentheticals(k: str) -> str:
    return " ".join(_PARENS.sub(_drop_unless_isotope, k).split())


def parenthetical_contents(k: str) -> list:
    return [key(a or b) for a, b in _PAREN_CONTENT.findall(k) if key(a or b)]


def drop_dose_and_form(k: str) -> str:
    s = QUALIFIER_PHRASES.sub(" ", _DOSE.sub(" ", k))
    return " ".join(w for w in s.split() if w.strip(_EDGE) not in FORM_WORDS).strip(_EDGE)


def drop_salt(k: str) -> str:
    words = k.split()
    while len(words) > 1 and words[-1].strip(_EDGE) in SALT_WORDS:
        words.pop()
    return " ".join(words)


def drop_biosimilar_suffix(k: str) -> str:
    root = BIOSIMILAR_SUFFIX.sub("", k)
    return root if root != k and is_biologic_root(root) else k


# ---- the stated form (D-12 to D-15, docs/Handoff_rev11.md) ---------------------------------
# A trial's tested FORM is the salt and the formulation/route its intervention name and other
# names state (never the description, D-15). Recorded beside the moiety, never instead of it:
# salts and formulations are different products with different approvals (D-12).

# Salt-list words that do not distinguish a product.
NON_DISTINGUISHING_SALT_WORDS = frozenset({"free", "base", "anhydrous"})

# One spelling per salt, as Drugs@FDA ingredient strings write it ('METOPROLOL SUCCINATE',
# 'IMATINIB MESYLATE'), so the market label can compare a stated salt with an ingredient.
SALT_CANONICAL = {"hcl": "hydrochloride", "mesilate": "mesylate", "besilate": "besylate",
                  "sulphate": "sulfate", "embonate": "pamoate"}

# Known gap: a brand whose name contains a form word reads as that form ('Acthar Gel' is an
# injection, read as topical). Not special-cased; the route review shows such cases.

# Formulation and route words -> a FORM CLASS. Classes, not words, are what the market label
# will map onto Drugs@FDA Products.Form (dosage form ; route). Hyphenated entries also match
# the two-word spelling ('extended release'). Short tokens ('iv', 'sc', 'er') are matched as
# whole words only.
FORM_CLASS_WORDS = {
    "extended_release": ("extended-release", "er", "xr", "xl", "sr", "cr", "long-acting",
                         "prolonged-release", "sustained-release", "controlled-release",
                         "depot"),
    "delayed_release": ("delayed-release", "dr", "enteric-coated", "gastro-resistant"),
    "modified_release": ("modified-release",),
    "orally_disintegrating": ("odt", "orally-disintegrating"),
    "liposomal": ("liposomal", "liposome"),
    "lipid_complex": ("lipid-complex",),
    "intravenous": ("iv", "i.v", "intravenous", "intravenously", "infusion"),
    "subcutaneous": ("sc", "sq", "s.c", "subcutaneous", "subcutaneously"),
    "intramuscular": ("im", "i.m", "intramuscular", "intramuscularly"),
    "oral": ("oral", "orally", "po"),
    "topical": ("topical", "cream", "gel", "ointment", "lotion", "foam"),
    "transdermal": ("transdermal", "patch"),
    "nasal": ("nasal", "intranasal"),
    "inhalation": ("inhalation", "inhaled", "inhaler", "nebulised", "nebulized", "dpi",
                   "mdi", "pmdi"),
    "ophthalmic": ("ophthalmic", "eye-drops"),
    "intrathecal": ("intrathecal",),
    "intravitreal": ("intravitreal",),
    "vaginal": ("vaginal", "intravaginal"),
    "rectal": ("rectal", "suppository"),
    "sublingual": ("sublingual",),
    "buccal": ("buccal",),
    "implant": ("implant", "implantable"),
}
FORM_CLASSES = tuple(sorted(FORM_CLASS_WORDS))
_FORM_WORD_CLASS = {w: c for c, ws in FORM_CLASS_WORDS.items() for w in ws}
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


class StatedForm(NamedTuple):
    salts: tuple          # salt words stated, sorted
    classes: tuple        # FORM_CLASSES stated, sorted
    words: tuple          # the formulation/route words found, sorted

    @property
    def stated(self) -> bool:
        return bool(self.salts or self.classes)


NO_FORM = None  # set below, after StatedForm exists


def tokens(k: str) -> list:
    toks = _TOKEN.findall(k)
    return toks + [f"{a}-{b}" for a, b in zip(toks, toks[1:])]


def stated_salts(raw) -> tuple:
    """Salt words a name states: the TRAILING salt words drop_salt removes once brackets,
    doses and forms are gone. 'sodium chloride' states none ('chloride' is the drug); a
    leading counter-ion ('sodium valproate') is not read, a known gap."""
    out = set()
    k = key(raw)
    for base in [drop_parentheticals(k)] + parenthetical_contents(k):
        stripped = drop_dose_and_form(base)
        kept = drop_salt(stripped).split()
        out.update(w.strip(_EDGE) for w in stripped.split()[len(kept):])
    return tuple(sorted({SALT_CANONICAL.get(w, w) for w in out
                         if w and w not in NON_DISTINGUISHING_SALT_WORDS}))


def stated_form(names) -> StatedForm:
    """The form stated across one intervention's names (its name and other names)."""
    salts, classes, words = set(), set(), set()
    for raw in names:
        k = key(raw)
        if not k:
            continue
        salts.update(stated_salts(k))
        for t in tokens(k):
            c = _FORM_WORD_CLASS.get(t)
            if c:
                classes.add(c)
                words.add(t)
    return StatedForm(tuple(sorted(salts)), tuple(sorted(classes)), tuple(sorted(words)))


NO_FORM = StatedForm((), (), ())


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
