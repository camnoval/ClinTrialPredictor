"""Form-level FDA approval of a tested agent (D-12 to D-14, D-26 to D-31). Pure.

Question answered: which Drugs@FDA NDA/BLA products ARE the tested agent -- its moiety set
and its stated form -- and when was each first approved. ANDAs never count (D-9). The
market label, the readout-to-approval lag (Task 2) and the deferred resolution sample (D-25)
all ask it, the last from a labeller's clean name rather than the resolver's match.

TWO BOUNDS, NEVER A GUESS (D-27). Drugs@FDA often does not record the form dimension a trial
states: 2,608 NDA/BLA products are 'INJECTABLE;INJECTION', silent on IV/SC/IM, and depot or
liposomal injectables are not always labelled so (audit/probe_form_lineup.py sections 1-2;
Eylea is 'INJECTION' and approved intravitreally, Avastin is 'INJECTION' and is not). Each
product therefore meets each stated requirement as

  confirmed  the product's Form, brand or ingredient names it
  possible   the product is silent on that dimension
  excluded   the product names something else on it

and an agent gets two product sets: STRICT (every requirement confirmed) and LOOSE (none
excluded). A question about the agent is answered only when both sets give the same answer;
otherwise it is undeterminable (`approved_before`).

REQUIREMENTS come from the stated WORDS (drug_names.stated_form words), not only the classes
(D-26, amends D-18's "classes, not words"):
  route words          tested on the route half of Products.Form; on the dosage half when the
                       route half says nothing ('VIAL', 'INJECTION;SOLUTION')
  dosage-form words    gel, cream, ointment, lotion, foam, patch, suppository: tested on the
                       DOSAGE half, because they say what the product is, not where it goes
                       ('testosterone gel' is 'GEL;TRANSDERMAL', 'lidocaine patch' is
                       'PATCH;TOPICAL')
  modifiers            release, liposomal, lipid complex, ODT, implant: searched in the whole
                       Form; absent = excluded, except on a parenteral product for the
                       modifiers FDA does not label consistently there (absent = possible)
  salts                compared with the salt words of the ingredient part that is the moiety
A stated word that is a whole word of the product's brand name confirms its requirement
('LUPRON DEPOT', 'ACTHAR GEL'). Several values of one kind (two routes, two salts) mean the
name is ambiguous: confirmed only if the product has all of them, possible if any.

PRODUCT IDENTITY (D-29). A product's moieties are its drug_dictionary link (the same code the
resolver uses, drug_dictionary.fda_product_link) OR its ALTERNATIVE set: each ingredient part
resolved by name with its trailing salt words removed, so 'DEXAMETHASONE SODIUM PHOSPHATE' is
a dexamethasone product even where DrugCentral files the salt as its own structure. The
removal never leaves a bare element or an alkyl fragment (the D-11/D-23 guards).
Set rule (SET_RULES): EXACT, the product's moieties equal the agent's; CONTAINED, they include
them, so combination products count. The owner chose CONTAINED (D-32, 2026-10-13):
"approved" means on the US market for any indication, never indication-specific. The brand
rule is PIN (D-33).

DATES (D-30). A product is dated by its application's first approved original
(Submissions ORIG/AP), or by its Orange Book product date when every Orange Book row of that
(appl_no, product_no) gives the same date and it is not earlier than the original: a form
added to an NDA by supplement is then dated when it was added. BLAs have no product dates.

SOURCE GAP (D-31). A moiety that DrugCentral says FDA approved but that no Drugs@FDA NDA/BLA
product carries (CBER products, pre-1938 drugs, ANDA-only listings) makes the agent
undeterminable, never 'not approved'.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_dictionary import (
    ALKYL_ROOT, DRUGCENTRAL_NAME_SOURCES, HIT_RESOLVED, DrugDictionary, fda_product_link,
    ob_product_index, parse_struct_id,
)
from trial_pos.services.drug_names import (
    BIOSIMILAR_SUFFIX, ELEMENT_ROOTS, FORM_CLASS_WORDS, FORM_CLASSES, FORM_WORD_CLASS,
    drop_parentheticals, drop_salt, key, stated_salts,
)
from trial_pos.services.drug_resolution import AGENT_RESOLVED, resolve_one_name
from trial_pos.services.drugsatfda import (
    APPL_BLA, APPL_NDA, APPROVED, INGREDIENT_SEPARATORS, ORIGINAL, TENTATIVE, norm_appl,
    norm_product, parse_date,
)

INNOVATOR_TYPES = (APPL_NDA, APPL_BLA)            # D-9: ANDAs never count
OB_INNOVATOR_TYPE = "N"                           # ob_product.appl_type vocabulary {A, N}
# Approval dates before the 1938 FD&C Act are placeholders (one ORIG is 1900-01-01,
# probe_source_files_20261001.txt 4c): undated.
EARLIEST_REAL_APPROVAL = date(1939, 1, 1)

CONFIRMED, POSSIBLE, EXCLUDED = "confirmed", "possible", "excluded"
STATES = (EXCLUDED, POSSIBLE, CONFIRMED)
_RANK = {s: i for i, s in enumerate(STATES)}

SET_EXACT = "exact"
SET_CONTAINED = "contained"
SET_RULES = (SET_EXACT, SET_CONTAINED)
BRAND_PIN = "pin"
BRAND_MOIETY = "moiety"
BRAND_RULES = (BRAND_PIN, BRAND_MOIETY)

FORM_SEP = ";"
ROUTE_ITEM_SEP = ","

# ---- the line-up (audit/probe_form_lineup.py, corrected by its run 2026-10-13) -----------
# Modifier classes: tokens searched in the whole Form.
MODIFIER_TOKENS = {
    "extended_release": ("EXTENDED RELEASE",),
    "delayed_release": ("DELAYED RELEASE", "DELAYED REL"),
    # FDA has no 'modified release' dosage form; it is one of the two above.
    "modified_release": ("EXTENDED RELEASE", "DELAYED RELEASE", "DELAYED REL"),
    "orally_disintegrating": ("ORALLY DISINTEGRATING",),
    "liposomal": ("LIPOSOMAL",),
    "lipid_complex": ("LIPID COMPLEX",),
    "implant": ("IMPLANT",),                      # dosage IMPLANT and route IMPLANTATION
}
# On a parenteral product these are not labelled consistently, so their absence is
# 'possible', not 'excluded': Lupron Depot, Somatuline Depot and Sandostatin LAR carry no
# 'EXTENDED RELEASE'; Vyxeos (liposomal) is 'POWDER;INTRAVENOUS' (probe section 2).
UNLABELLED_ON_PARENTERAL = frozenset({"extended_release", "modified_release", "liposomal"})
SILENT_DOSAGE = frozenset({"", "N/A", "UNKNOWN"})

# Dosage-form words: tested on the dosage half (substring), never as a route.
DOSAGE_WORD_TOKENS = {
    "gel": ("GEL",), "cream": ("CREAM",), "ointment": ("OINTMENT",), "lotion": ("LOTION",),
    "foam": ("FOAM",), "patch": ("PATCH", "SYSTEM", "FILM, EXTENDED RELEASE", "TAPE"),
    "suppository": ("SUPPOSITORY",),
}

PARENTERAL = ("intravenous", "subcutaneous", "intramuscular", "intrathecal", "intravitreal")
ROUTE_CONFIRMS = {
    "INTRAVENOUS": ("intravenous",), "IV (INFUSION)": ("intravenous",),
    "IM-IV": ("intramuscular", "intravenous"),
    "SUBCUTANEOUS": ("subcutaneous",), "SUBCUTANEOUS LYOPHILIZED POWER": ("subcutaneous",),
    "INTRAMUSCULAR": ("intramuscular",),
    "ORAL": ("oral",), "ORAL-20": ("oral",), "ORAL-21": ("oral",), "ORAL-28": ("oral",),
    "ORAL SUSPENSION": ("oral",), "ORALLY DISINTEGRATING": ("oral",),
    "TOPICAL": ("topical",), "TRANSDERMAL": ("transdermal",), "NASAL": ("nasal",),
    "INHALATION": ("inhalation",), "OPHTHALMIC": ("ophthalmic",),
    "INTRATHECAL": ("intrathecal",), "SPINAL": ("intrathecal",),
    # An intravitreal injection is ophthalmic ('aflibercept ophthalmic', 'brolucizumab
    # ophthalmic solution' are intravitreal products; probe section 2).
    "INTRAVITREAL": ("intravitreal", "ophthalmic"),
    "INTRAOCULAR": ("ophthalmic",), "INTRACAMERAL": ("ophthalmic",),
    "VAGINAL": ("vaginal",), "RECTAL": ("rectal",),
    "SUBLINGUAL": ("sublingual",), "BUCCAL": ("buccal",),
}
# Route items generic for some classes: possible for those, excluding for the others.
ROUTE_GENERIC = {
    "INJECTION": PARENTERAL + ("ophthalmic",),
    "TRANSMUCOSAL": ("buccal", "sublingual"),
    "INTRAOCULAR": ("intravitreal",),
    "IMPLANTATION": ("subcutaneous",),            # goserelin 'subcutaneously' is Zoladex
    # Applied to the skin either way: 'topical testosterone' is AndroGel, 'GEL;TRANSDERMAL';
    # 'transdermal lidocaine' is Lidoderm, 'PATCH;TOPICAL' (matcher run 2026-10-13).
    "TOPICAL": ("transdermal",),
    "TRANSDERMAL": ("topical",),
    # Ocular injections in the Purple Book (D-41): an ophthalmic product, route unstated.
    "PERIBULBAR": ("ophthalmic",),
    "RETROBULBAR": ("ophthalmic",),
}
# Route items that say nothing: every route class possible.
ROUTE_SILENT = frozenset({"", "N/A", "SINGLE-USE", "SINGLE-DOSE", "MULTIDOSE", "PREFILLED",
                          "FOR RX COMPOUNDING", "SOLUTION", "SUSPENSION", "POWDER",
                          "FOR SOLUTION"})
# Specific routes no class names: they exclude every route class.
ROUTE_OTHER = frozenset({
    "INTRAPERITONEAL", "IRRIGATION", "OTIC", "INTRA-ARTERIAL", "INTRAUTERINE", "DENTAL",
    "URETHRAL", "URETERAL", "INTRATRACHEAL", "INTRAVESICAL", "INTRAPLEURAL", "PERIARTICULAR",
    "EPIDURAL", "PERIODONTAL", "INTRA-ARTICULAR", "INTRACAVITARY", "INTERSTITIAL",
    "IONTOPHORESIS", "ENDOCERVICAL", "ENTERAL", "INFILTRATION", "INTRA-ANAL", "INTRACRANIAL",
    "INTRADERMAL", "INTRALYMPHATIC", "INTRAOSSEOUS", "ENDOTRACHEAL", "INTRAVESICULAR",
    "PERFUSION", "BILIARY", "CARDIAC", "PYELOCALYCEAL",
    # Purple Book routes (D-41)
    "PERCUTANEOUS", "INTRALESIONAL", "INTRAGLANDULAR", "SOFT TISSUE", "INTRADETRUSOR",
    "INTRAVENTRICULAR", "INTRAPUTAMINAL", "INTRACOCHLEAR", "INTRATUMOR",
})
# Specific routes that are injections or instillations: on them, unlabelled modifiers are
# possible (DepoDur is 'INJECTABLE, LIPOSOMAL;EPIDURAL', matcher run 2026-10-13).
PARENTERAL_OTHER_ROUTES = frozenset({
    "EPIDURAL", "INTRA-ARTICULAR", "INTRA-ARTERIAL", "INTRADERMAL", "INTRAOSSEOUS",
    "INFILTRATION", "PERIARTICULAR", "INTRACAVITARY", "INTRAPLEURAL", "INTRAPERITONEAL",
    "INTRALYMPHATIC", "INTRACRANIAL", "INTERSTITIAL", "INTRALESIONAL", "INTRAGLANDULAR",
    "SOFT TISSUE", "INTRADETRUSOR", "INTRAVENTRICULAR", "INTRAPUTAMINAL", "INTRACOCHLEAR",
    "INTRATUMOR"})
# When the route half says nothing, the dosage half can: 'VIAL', 'SYRINGE', 'INJECTION;
# SOLUTION' (halves swapped) are parenteral; 'GAS' is inhaled (probe section 1).
DOSAGE_IMPLIES_GENERIC = {"INJECTION": "INJECTION", "INJECTABLE": "INJECTION",
                          "VIAL": "INJECTION", "SYRINGE": "INJECTION",
                          "LYOPHILIZED": "INJECTION", "GAS": "INHALATION"}
# Dosage tokens that confirm a route whatever the route half says: Nicotrol Inhaler is
# 'INHALANT;ORAL'.
DOSAGE_CONFIRMS_ROUTE = {"INHALANT": ("inhalation",)}
ROUTE_CLASSES = tuple(sorted(set(FORM_CLASSES) - set(MODIFIER_TOKENS)))
# 'oral' beside an inhalation word means oral inhalation ('budesonide/formoterol oral
# inhalation aerosol'): the oral requirement is dropped.
ORAL, INHALATION = "oral", "inhalation"

# Salts Drugs@FDA writes under another name: Novantrone and Orserdu are dihydrochlorides
# listed as 'HYDROCHLORIDE' (matcher run 2026-10-13). Applied to both sides, matching only.
SALT_MATCH_EQUIVALENT = {"dihydrochloride": "hydrochloride"}

KIND_ROUTE, KIND_MODIFIER, KIND_DOSAGE, KIND_SALT = "route", "modifier", "dosage", "salt"
REQUIREMENT_KINDS = (KIND_ROUTE, KIND_MODIFIER, KIND_DOSAGE, KIND_SALT)


def check_lineup() -> None:
    """Fail closed on an inconsistent line-up. Run at import."""
    problems = []
    if not set(MODIFIER_TOKENS) <= set(FORM_CLASSES):
        extra = sorted(set(MODIFIER_TOKENS) - set(FORM_CLASSES))
        problems.append(f"modifiers not form classes: {extra}")
    confirmed = {c for cs in ROUTE_CONFIRMS.values() for c in cs}
    generic = {c for cs in ROUTE_GENERIC.values() for c in cs}
    implied = {c for cs in DOSAGE_CONFIRMS_ROUTE.values() for c in cs}
    for name, got in (("confirms", confirmed), ("generic", generic), ("dosage", implied)):
        if not got <= set(ROUTE_CLASSES):
            extra = sorted(got - set(ROUTE_CLASSES))
            problems.append(f"route {name} names non-route classes {extra}")
    unconfirmable = set(ROUTE_CLASSES) - confirmed - implied
    if unconfirmable:
        problems.append(f"route classes nothing confirms: {sorted(unconfirmable)}")
    for w in DOSAGE_WORD_TOKENS:
        if w not in FORM_WORD_CLASS:
            problems.append(f"dosage word '{w}' is not a drug_names form word")
    if PARENTERAL_OTHER_ROUTES - ROUTE_OTHER:
        extra = sorted(PARENTERAL_OTHER_ROUTES - ROUTE_OTHER)
        problems.append(f"parenteral routes not in ROUTE_OTHER: {extra}")
    if set(UNLABELLED_ON_PARENTERAL) - set(MODIFIER_TOKENS):
        problems.append("UNLABELLED_ON_PARENTERAL names a non-modifier")
    for item in set(ROUTE_CONFIRMS) & set(ROUTE_GENERIC):
        if set(ROUTE_CONFIRMS[item]) & set(ROUTE_GENERIC[item]):
            problems.append(f"route item {item} both confirms and is generic for one class")
    named = set(ROUTE_CONFIRMS) | set(ROUTE_GENERIC)
    for label, table in (("silent", ROUTE_SILENT), ("other", ROUTE_OTHER)):
        if table & named:
            problems.append(f"{label} route items also listed elsewhere: {sorted(table & named)}")
    if ROUTE_SILENT & ROUTE_OTHER:
        problems.append(f"route items both silent and other: {sorted(ROUTE_SILENT & ROUTE_OTHER)}")
    if set(DOSAGE_IMPLIES_GENERIC.values()) - set(ROUTE_GENERIC) - set(ROUTE_CONFIRMS):
        problems.append("DOSAGE_IMPLIES_GENERIC points at an unknown route item")
    if problems:
        raise ValueError("form line-up inconsistent: " + "; ".join(problems))


check_lineup()


# ---- products ------------------------------------------------------------------------------
class Part(NamedTuple):
    text: str
    salts: tuple                  # drug_names.stated_salts of the part
    direct: frozenset             # the part's name resolved against DrugCentral names
    stripped: frozenset           # the part with trailing salt words removed, resolved


class Product(NamedTuple):
    appl: str
    prod: str
    appl_type: str
    form: str                     # upper case
    dosage: str
    route_items: tuple
    brand_keys: frozenset         # key(DrugName), without brackets, Orange Book trade name
    brand_words: frozenset        # whole words of every brand key
    parts: tuple
    link: Optional[frozenset]     # drug_dictionary.fda_product_link drugs
    alternative: Optional[frozenset]
    approved: Optional[date]
    date_source: str


DATE_ORIGINAL = "application_original"
DATE_OB_PRODUCT = "orange_book_product"
DATE_PURPLE_BOOK = "purple_book_product"
DATE_NONE = "undated"
# Purple Book products are keyed apart from Drugs@FDA's (appl, ProductNo), which can share
# the BLA number: they are extra evidence, never a replacement.
PB_PRODUCT_PREFIX = "PB"


def split_form(form: str) -> tuple:
    """'TABLET, EXTENDED RELEASE;ORAL' -> ('TABLET, EXTENDED RELEASE', ('ORAL',))."""
    dosage, _sep, route = (form or "").upper().partition(FORM_SEP)
    items = tuple(sorted({i.strip() for i in route.split(ROUTE_ITEM_SEP)}))
    return dosage.strip(), items


def ingredient_parts(value) -> list:
    parts = [str(value or "")]
    for sep in INGREDIENT_SEPARATORS:
        parts = [p for part in parts for p in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


_WORDS = re.compile(r"[a-z0-9]+")


def resolve_part(d: DrugDictionary, text: str, cache: Optional[dict] = None) -> Part:
    if cache is not None and text in cache:
        return cache[text]
    k = key(text)
    hit, _v = d.resolve_name(k, DRUGCENTRAL_NAME_SOURCES)
    direct = hit.drugs if hit.status == HIT_RESOLVED else frozenset()
    stripped = frozenset()
    root = drop_salt(k)
    if root != k and root not in ELEMENT_ROOTS and not ALKYL_ROOT.search(root):
        h2, _v2 = d.resolve_name(root, DRUGCENTRAL_NAME_SOURCES)
        if h2.status == HIT_RESOLVED:
            stripped = h2.drugs
    if not direct and not stripped:
        # FDA's own four-letter suffix ('SOMATROGON-GHLA'): in a Drugs@FDA ingredient string
        # it is always the suffix, so D-21's biologic-stem guard (for registry names) is not
        # applied here.
        root = BIOSIMILAR_SUFFIX.sub("", k)
        if root != k:
            h3, _v3 = d.resolve_name(root, DRUGCENTRAL_NAME_SOURCES)
            if h3.status == HIT_RESOLVED:
                direct = h3.drugs
    part = Part(text, stated_salts(text), direct, stripped)
    if cache is not None:
        cache[text] = part
    return part


def alternative_set(parts: tuple) -> Optional[frozenset]:
    """Each part's salt-stripped moiety, else its direct one; None if any part has neither."""
    out = set()
    for p in parts:
        ids = p.stripped or p.direct
        if not ids:
            return None
        out |= ids
    return frozenset(out)


def tentative_only(submissions: Iterable[dict]) -> frozenset:
    """Applications whose only originals are TENTATIVE approvals: nothing is approved."""
    tentative, approved = set(), set()
    for r in submissions:
        if r.get("SubmissionType") != ORIGINAL:
            continue
        a = norm_appl(r.get("ApplNo"))
        if r.get("SubmissionStatus") == APPROVED:
            approved.add(a)
        elif r.get("SubmissionStatus") == TENTATIVE:
            tentative.add(a)
    return frozenset(tentative - approved)


def original_dates(submissions: Iterable[dict]) -> dict:
    """{appl: earliest approved ORIG date on or after EARLIEST_REAL_APPROVAL}."""
    out = {}
    for r in submissions:
        if r.get("SubmissionType") != ORIGINAL or r.get("SubmissionStatus") != APPROVED:
            continue
        when = parse_date(r.get("SubmissionStatusDate"))
        if when is None or when < EARLIEST_REAL_APPROVAL:
            continue
        a = norm_appl(r.get("ApplNo"))
        if a not in out or when < out[a]:
            out[a] = when
    return out


def ob_product_dates(ob_product: Iterable[dict], audit: Counter) -> dict:
    """{(appl, product): date} for NDA rows whose every row of that key gives one date."""
    seen = defaultdict(set)
    for r in ob_product:
        if (r.get("appl_type") or "").strip() != OB_INNOVATOR_TYPE:
            continue
        k = (norm_appl(r.get("appl_no")), norm_product(r.get("product_no")))
        d = parse_date(r.get("approval_date"))
        seen[k].add(d if d is None or d >= EARLIEST_REAL_APPROVAL else None)
    out = {}
    for k, ds in seen.items():
        if len(ds) > 1:
            audit["dates: Orange Book rows of one key disagree (not used)"] += 1
        elif None not in ds:
            out[k] = next(iter(ds))
    return out


def product_date(original: Optional[date], ob: Optional[date], audit: Counter) -> tuple:
    if ob is not None and original is not None and ob < original:
        audit["dates: Orange Book date before the original (original used)"] += 1
        return original, DATE_ORIGINAL
    if ob is not None:
        return ob, DATE_OB_PRODUCT
    if original is not None:
        return original, DATE_ORIGINAL
    return None, DATE_NONE


def purplebook_products(d: DrugDictionary, rows: Iterable, audit: Counter) -> list:
    """purplebook.PurpleBookRow -> Products (D-41). Identity is the proper name resolved the
    way a registry name is (drug_resolution.resolve_one_name, which splits 'X and Y');
    a proper name that does not resolve is counted and not used."""
    out = []
    for r in rows:
        res = resolve_one_name(d, r.proper)
        if res.status != AGENT_RESOLVED or not res.drugs:
            audit[f"purple book: proper name unresolved ({r.center}), not used"] += 1
            continue
        audit[f"purple book: product used ({r.center})"] += 1
        dosage, items = split_form(r.form)
        brand = key(r.proprietary)
        keys = frozenset(k for k in {brand, drop_parentheticals(brand)} if k)
        words = frozenset(w for k in keys for w in _WORDS.findall(k))
        part = Part(r.proper, stated_salts(r.proper), frozenset(res.drugs), frozenset())
        out.append(Product(norm_appl(r.bla), PB_PRODUCT_PREFIX + r.product, APPL_BLA, r.form,
                           dosage, items, keys, words, (part,), frozenset(res.drugs), None,
                           r.approved, DATE_PURPLE_BOOK if r.approved else DATE_NONE))
        audit[f"dates: {DATE_PURPLE_BOOK if r.approved else DATE_NONE}"] += 1
    return out


def build_products(d: DrugDictionary, fda_products, fda_applications, submissions,
                   ob_product, struct2obprod, purplebook: Iterable = ()) -> tuple:
    """-> (products, audit). Every argument is an iterable of dict rows, except
    `purplebook`: purplebook.PurpleBookRow tuples (D-41)."""
    audit = Counter()
    ob_product = list(ob_product)
    by_product, by_application, _rows = ob_product_index(ob_product, struct2obprod, audit)
    trade = defaultdict(set)
    for r in ob_product:
        trade[(norm_appl(r.get("appl_no")), norm_product(r.get("product_no")))].add(
            key(r.get("trade_name")))
    appl_type = {norm_appl(r.get("ApplNo")): r.get("ApplType") for r in fda_applications}
    submissions = list(submissions)
    originals = original_dates(submissions)
    tentative = tentative_only(submissions)
    ob_dates = ob_product_dates(ob_product, audit)
    cache, products = {}, []
    for r in fda_products:
        a, pn = norm_appl(r.get("ApplNo")), norm_product(r.get("ProductNo"))
        t = appl_type.get(a)
        if t is None:
            audit["products: application absent from Applications (not used)"] += 1
            continue
        if t not in INNOVATOR_TYPES:
            continue
        if a in tentative:
            audit["products: tentative approval only (not an approval, not used)"] += 1
            continue
        link, how = fda_product_link(d, r, by_product, by_application)
        audit[f"products: linked by {how}"] += 1
        parts = tuple(resolve_part(d, p, cache)
                      for p in ingredient_parts(r.get("ActiveIngredient")))
        alt = alternative_set(parts)
        if alt is not None and alt != link:
            audit["products: alternative (salt-stripped) moiety set differs from the link"] += 1
        dosage, items = split_form(r.get("Form"))
        brand = key(r.get("DrugName"))
        keys = {brand, drop_parentheticals(brand)} | trade.get((a, pn), set())
        keys = frozenset(k for k in keys if k)
        words = frozenset(w for k in keys for w in _WORDS.findall(k))
        when, source = product_date(originals.get(a), ob_dates.get((a, pn)), audit)
        audit[f"dates: {source}"] += 1
        products.append(Product(a, pn, t, str(r.get("Form") or "").upper(), dosage, items,
                                keys, words, parts, frozenset(link) if link else None, alt,
                                when, source))
    products.extend(purplebook_products(d, purplebook, audit))
    return products, audit


class ProductIndex:
    def __init__(self, products: Iterable[Product], fda_approved: Iterable[int] = (),
                 fda_first: Optional[dict] = None, fda_undated: Iterable[int] = ()):
        self.products = list(products)
        self.fda_approved = frozenset(fda_approved)   # DrugCentral FDA-approved structures
        self.fda_first = dict(fda_first or {})       # D-42: structure -> first FDA date
        self.fda_undated = frozenset(fda_undated)     # D-42: an FDA row with no date
        self.by_moiety = defaultdict(list)
        for p in self.products:
            ids = set(p.link or ()) | set(p.alternative or ())
            for part in p.parts:
                ids |= part.direct | part.stripped
            for x in ids:
                self.by_moiety[x].append(p)

    def carrying(self, moieties: frozenset) -> list:
        """Products naming any of the moieties, in input order, each once."""
        seen, out = set(), []
        for x in sorted(moieties):
            for p in self.by_moiety.get(x, ()):
                if id(p) not in seen:
                    seen.add(id(p))
                    out.append(p)
        return out

    def source_gap(self, moieties: frozenset) -> bool:
        """D-31: a moiety DrugCentral says FDA approved that no NDA/BLA product carries."""
        return any(x in self.fda_approved and not self.by_moiety.get(x) for x in moieties)


def identity_match(p: Product, moieties: frozenset, rule: str) -> bool:
    sets = [s for s in (p.link, p.alternative) if s]
    if rule == SET_EXACT:
        return any(s == moieties for s in sets)
    if rule == SET_CONTAINED:
        return any(moieties <= s for s in sets)
    raise ValueError(f"set rule {rule!r} not in {SET_RULES}")


def product_salts(p: Product, moieties: frozenset) -> Optional[set]:
    """Salts of the parts naming the agent's moieties; None when a moiety's part is unknown."""
    out = set()
    for x in moieties:
        found = [part for part in p.parts if x in part.direct or x in part.stripped]
        if not found and len(p.parts) == 1 and p.link and x in p.link:
            found = list(p.parts)
        if not found:
            return None
        for part in found:
            out.update(part.salts)
    return out


# ---- requirements ----------------------------------------------------------------------------
class Requirement(NamedTuple):
    kind: str
    values: tuple                 # route classes, a modifier class, dosage words, or salts
    words: tuple                  # the stated words behind it (brand evidence)


def requirements(salts: Iterable[str], words: Iterable[str]) -> tuple:
    """The stated form -> requirements, in REQUIREMENT_KINDS order. Raises on a word that is
    not a drug_names form word: the vocabularies must not drift apart."""
    routes, dosage, modifiers, route_words = set(), set(), defaultdict(set), set()
    for w in sorted(set(words)):
        if w in DOSAGE_WORD_TOKENS:
            dosage.add(w)
            continue
        c = FORM_WORD_CLASS.get(w)
        if c is None:
            raise ValueError(f"stated form word {w!r} is not in drug_names.FORM_CLASS_WORDS")
        if c in MODIFIER_TOKENS:
            modifiers[c].add(w)
        else:
            routes.add(c)
            route_words.add(w)
    if ORAL in routes and INHALATION in routes:
        routes.discard(ORAL)
        route_words -= set(FORM_CLASS_WORDS[ORAL])
    out = []
    if routes:
        out.append(Requirement(KIND_ROUTE, tuple(sorted(routes)), tuple(sorted(route_words))))
    for c in sorted(modifiers):
        out.append(Requirement(KIND_MODIFIER, (c,), tuple(sorted(modifiers[c]))))
    if dosage:
        out.append(Requirement(KIND_DOSAGE, tuple(sorted(dosage)), tuple(sorted(dosage))))
    salts = tuple(sorted(set(salts)))
    if salts:
        out.append(Requirement(KIND_SALT, salts, ()))
    return tuple(out)


def _combine(states: list) -> str:
    """Several values of one requirement: all confirmed -> confirmed; any not excluded ->
    possible; else excluded."""
    if all(s == CONFIRMED for s in states):
        return CONFIRMED
    if any(s != EXCLUDED for s in states):
        return POSSIBLE
    return EXCLUDED


def _route_evidence(p: Product) -> tuple:
    """(informative route items, generic implied by the dosage half or None)."""
    informative = tuple(i for i in p.route_items if i not in ROUTE_SILENT)
    if informative:
        return informative, None
    for token, generic in DOSAGE_IMPLIES_GENERIC.items():
        if token in p.dosage:
            return (), generic
    return (), None


def route_state(klass: str, p: Product, words: Iterable[str] = ()) -> str:
    if any(w in p.brand_words for w in words if FORM_WORD_CLASS.get(w) == klass):
        return CONFIRMED
    if any(t in p.dosage and klass in cs for t, cs in DOSAGE_CONFIRMS_ROUTE.items()):
        return CONFIRMED
    items, implied = _route_evidence(p)
    if not items:
        if implied is None:
            return POSSIBLE               # nothing on either half says where it goes
        items = (implied,)                # read the dosage half's implied route item
    state = EXCLUDED
    for i in items:
        if klass in ROUTE_CONFIRMS.get(i, ()):
            return CONFIRMED
        if i in ROUTE_GENERIC:
            if klass in ROUTE_GENERIC[i]:
                state = POSSIBLE
        elif i not in ROUTE_CONFIRMS and i not in ROUTE_OTHER:
            state = POSSIBLE              # an item in no table: read as silent
    return state


def is_parenteral(p: Product) -> bool:
    items, implied = _route_evidence(p)
    if implied == "INJECTION":
        return True
    return any(i in PARENTERAL_OTHER_ROUTES
               or set(ROUTE_CONFIRMS.get(i, ())) & set(PARENTERAL)
               or set(ROUTE_GENERIC.get(i, ())) & set(PARENTERAL) for i in items)


def modifier_state(klass: str, p: Product, words: Iterable[str] = ()) -> str:
    if any(w in p.brand_words for w in words):
        return CONFIRMED
    if any(t in p.form for t in MODIFIER_TOKENS[klass]):
        return CONFIRMED
    if p.dosage in SILENT_DOSAGE:
        return POSSIBLE
    if klass in UNLABELLED_ON_PARENTERAL and is_parenteral(p):
        return POSSIBLE
    return EXCLUDED


def dosage_word_state(word: str, p: Product) -> str:
    if word in p.brand_words or any(t in p.dosage for t in DOSAGE_WORD_TOKENS[word]):
        return CONFIRMED
    return POSSIBLE if p.dosage in SILENT_DOSAGE else EXCLUDED


def requirement_state(r: Requirement, p: Product, moieties: frozenset) -> str:
    if r.kind == KIND_ROUTE:
        return _combine([route_state(c, p, r.words) for c in r.values])
    if r.kind == KIND_MODIFIER:
        return modifier_state(r.values[0], p, r.words)
    if r.kind == KIND_DOSAGE:
        return _combine([dosage_word_state(w, p) for w in r.values])
    if r.kind == KIND_SALT:
        have = product_salts(p, moieties)
        if not have:
            # unknown part, or a part naming no salt: the US often lists the base for a
            # product other registries name as a salt ('cytarabine hydrochloride')
            return POSSIBLE
        have = {SALT_MATCH_EQUIVALENT.get(x, x) for x in have}
        return _combine([CONFIRMED if SALT_MATCH_EQUIVALENT.get(s, s) in have else EXCLUDED
                         for s in r.values])
    raise ValueError(f"requirement kind {r.kind!r}")


def product_state(reqs: tuple, p: Product, moieties: frozenset) -> str:
    """The weakest state over every requirement; confirmed when nothing is stated (D-14)."""
    states = [requirement_state(r, p, moieties) for r in reqs]
    return min(states, key=_RANK.get) if states else CONFIRMED


# ---- the agent -----------------------------------------------------------------------------
class Query(NamedTuple):
    moieties: frozenset
    salts: tuple = ()
    words: tuple = ()
    brand: str = ""               # a brand key the agent was matched through, else ''


class AgentApproval(NamedTuple):
    n_identity: int               # NDA/BLA products that are the moiety set (any form)
    strict_dates: tuple           # distinct approval dates of strict products, sorted
    loose_dates: tuple
    strict_undated: int
    loose_undated: int
    n_strict: int
    n_loose: int
    brand_pinned: Optional[bool]  # None: not matched by brand, or brand rule off
    source_gap: bool
    dc_fda_first: Optional[date] = None   # D-42a: set only for a single unstated moiety
    dc_fda_undated: bool = False          # D-42c: a moiety's DrugCentral FDA row is undated


def _dates(products: list) -> tuple:
    return tuple(sorted({p.approved for p in products if p.approved})), \
        sum(1 for p in products if p.approved is None)


def in_brand_family(brand: str, keys: Iterable[str]) -> bool:
    """The brand or a line extension of it: 'zofran' covers 'zofran odt', 'marcaine' covers
    'marcaine hydrochloride' (matcher run 2026-10-13); 'zofranx' is not 'zofran'."""
    return any(k == brand or k.startswith(brand + " ") for k in keys)


def candidates(index: ProductIndex, q: Query, set_rule: str, brand_rule: str) -> tuple:
    """-> (products that are the moiety set, brand_pinned). A brand the agent was matched
    through restricts them to that brand family's products under BRAND_PIN, when it has any."""
    if brand_rule not in BRAND_RULES:
        raise ValueError(f"brand rule {brand_rule!r} not in {BRAND_RULES}")
    if not q.moieties:
        raise ValueError("a query needs at least one moiety")
    cands = [p for p in index.carrying(q.moieties) if identity_match(p, q.moieties, set_rule)]
    pinned = None
    if q.brand and brand_rule == BRAND_PIN:
        branded = [p for p in cands if in_brand_family(q.brand, p.brand_keys)]
        pinned = bool(branded)
        if branded:
            cands = branded
    return cands, pinned


def value_states(index: ProductIndex, q: Query, set_rule: str, brand_rule: str) -> list:
    """For the audit: [(kind, value, best state over the candidates or None when there are
    none)], each stated value tested on its own."""
    cands, _pinned = candidates(index, q, set_rule, brand_rule)
    out = []
    for r in requirements(q.salts, q.words):
        for v in r.values:
            words = tuple(w for w in r.words
                          if w == v or FORM_WORD_CLASS.get(w) == v)
            single = Requirement(r.kind, (v,), words)
            best = max((requirement_state(single, p, q.moieties) for p in cands),
                       key=_RANK.get, default=None)
            out.append((r.kind, v, best))
    return out


def approvals(index: ProductIndex, q: Query, set_rule: str, brand_rule: str) -> AgentApproval:
    cands, pinned = candidates(index, q, set_rule, brand_rule)
    reqs = requirements(q.salts, q.words)
    states = [(p, product_state(reqs, p, q.moieties)) for p in cands]
    strict = [p for p, s in states if s == CONFIRMED]
    loose = [p for p, s in states if s != EXCLUDED]
    sd, su = _dates(strict)
    ld, lu = _dates(loose)
    dc_first = None
    if len(q.moieties) == 1 and not reqs and not pinned:
        dc_first = index.fda_first.get(next(iter(q.moieties)))
    return AgentApproval(len(cands), sd, ld, su, lu, len(strict), len(loose), pinned,
                         index.source_gap(q.moieties), dc_first,
                         any(x in index.fda_undated for x in q.moieties))


def _before(dates: tuple, undated: int, readout: date) -> Optional[bool]:
    if any(x < readout for x in dates):
        return True
    return None if undated else False


def approved_before(a: AgentApproval, readout: date) -> Optional[bool]:
    """D-9 at form level: an NDA/BLA approval of the agent strictly before the readout.
    True only if a STRICT product proves it; False only if no LOOSE product could; else
    None. A source gap is always None.

    D-42 can only turn False into None, never anything into True: DrugCentral dating an
    FDA approval of a single unstated moiety BEFORE the readout (withdrawn and reapproved
    drugs Drugs@FDA lacks: gemtuzumab ozogamicin, 2000), or holding an UNDATED FDA row for
    any moiety (pre-1938 drugs, medical gases), means the "not approved" reading may be the
    sources' gap, not the drug's history."""
    if a.source_gap:
        return None
    strict = _before(a.strict_dates, a.strict_undated, readout)
    if strict is True:
        return True
    loose = _before(a.loose_dates, a.loose_undated, readout)
    if loose is not False:
        return None
    if a.dc_fda_undated or (a.dc_fda_first is not None and a.dc_fda_first < readout):
        return None
    return False


STATUS_BEFORE = "approved before readout"
STATUS_NOT = "not approved before readout"
STATUS_BOUNDS = "undeterminable: strict and loose disagree"
STATUS_UNDATED = "undeterminable: an undated product"
STATUS_GAP = "undeterminable: moiety absent from Drugs@FDA (D-31)"
STATUS_DC_EARLIER = "undeterminable: DrugCentral dates an earlier FDA approval (D-42a)"
STATUS_DC_UNDATED = "undeterminable: DrugCentral FDA approval undated (D-42c)"
BEFORE_STATUSES = (STATUS_BEFORE, STATUS_NOT, STATUS_BOUNDS, STATUS_UNDATED, STATUS_GAP,
                   STATUS_DC_EARLIER, STATUS_DC_UNDATED)


def before_status(a: AgentApproval, readout: date) -> str:
    """approved_before, with the reason when it is None (for audits)."""
    v = approved_before(a, readout)
    if v is True:
        return STATUS_BEFORE
    if v is False:
        return STATUS_NOT
    if a.source_gap:
        return STATUS_GAP
    if any(x < readout for x in a.loose_dates):
        return STATUS_BOUNDS
    if a.loose_undated or a.strict_undated:
        return STATUS_UNDATED
    if a.dc_fda_undated:
        return STATUS_DC_UNDATED
    return STATUS_DC_EARLIER


def first_after(a: AgentApproval, readout: date) -> tuple:
    """(earliest loose, earliest strict) approval on or after the readout; None where the
    set has none. The loose date is never later than the strict one."""
    lo = min((x for x in a.loose_dates if x >= readout), default=None)
    st = min((x for x in a.strict_dates if x >= readout), default=None)
    return lo, st


# ---- serialisation -------------------------------------------------------------------------
DATE_SEP = "|"
APPROVAL_COLUMNS = ("nct_id", "intervention_id", "drugs", "stated_salts", "stated_form_words",
                    "brand", "set_rule", "brand_rule", "n_identity_products", "n_strict",
                    "n_loose", "strict_dates", "loose_dates", "strict_undated",
                    "loose_undated", "brand_pinned", "source_gap", "dc_fda_first",
                    "dc_fda_undated")


def approval_row(nct_id: str, intervention_id, drugs: str, q: Query, a: AgentApproval,
                 set_rule: str, brand_rule: str) -> dict:
    tri = "" if a.brand_pinned is None else str(a.brand_pinned)
    return {"nct_id": nct_id, "intervention_id": intervention_id, "drugs": drugs,
            "stated_salts": "|".join(q.salts), "stated_form_words": "|".join(q.words),
            "brand": q.brand, "set_rule": set_rule, "brand_rule": brand_rule,
            "n_identity_products": a.n_identity, "n_strict": a.n_strict,
            "n_loose": a.n_loose,
            "strict_dates": DATE_SEP.join(x.isoformat() for x in a.strict_dates),
            "loose_dates": DATE_SEP.join(x.isoformat() for x in a.loose_dates),
            "strict_undated": a.strict_undated, "loose_undated": a.loose_undated,
            "brand_pinned": tri, "source_gap": str(a.source_gap),
            "dc_fda_first": a.dc_fda_first.isoformat() if a.dc_fda_first else "",
            "dc_fda_undated": str(a.dc_fda_undated)}


def _parse_dates(raw: str) -> tuple:
    out = []
    for x in (raw or "").split(DATE_SEP):
        if not x:
            continue
        d = parse_date(x)
        if d is None:
            raise ValueError(f"approval date {x!r} does not parse")
        out.append(d)
    if out != sorted(set(out)):
        raise ValueError(f"approval dates not distinct and sorted: {raw!r}")
    return tuple(out)


def _parse_tri(raw: str) -> Optional[bool]:
    if raw in ("", None):
        return None
    if raw in ("True", "False"):
        return raw == "True"
    raise ValueError(f"{raw!r} is not True, False or blank")


def parse_approval_row(row: dict) -> AgentApproval:
    """The inverse of approval_row's approval fields. Fails closed on anything malformed."""
    missing = [c for c in APPROVAL_COLUMNS if c not in row]
    if missing:
        raise ValueError(f"approval row lacks {missing}")
    gap, undated = _parse_tri(row["source_gap"]), _parse_tri(row["dc_fda_undated"])
    if gap is None or undated is None:
        raise ValueError("source_gap and dc_fda_undated must be True or False")
    first = _parse_dates(row["dc_fda_first"])
    if len(first) > 1:
        raise ValueError(f"dc_fda_first holds more than one date: {row['dc_fda_first']!r}")
    return AgentApproval(int(row["n_identity_products"]), _parse_dates(row["strict_dates"]),
                         _parse_dates(row["loose_dates"]), int(row["strict_undated"]),
                         int(row["loose_undated"]), int(row["n_strict"]), int(row["n_loose"]),
                         _parse_tri(row["brand_pinned"]), gap,
                         first[0] if first else None, undated)


def approved_within(a: AgentApproval, readout: date, end: date) -> Optional[bool]:
    """An approval on or after the readout and on or before `end`. True only if a STRICT
    product proves it; False only if no LOOSE product could (no loose date in range and no
    undated loose product); else None. A source gap is None."""
    if end < readout:
        raise ValueError("window end precedes the readout")
    if a.source_gap:
        return None
    if any(readout <= x <= end for x in a.strict_dates):
        return True
    if any(readout <= x <= end for x in a.loose_dates) or a.loose_undated:
        return None
    return False


def fda_approval_dates(approval_rows: Iterable[dict], agency: str) -> tuple:
    """DrugCentral `approval` rows -> ({structure: earliest dated approval by `agency`},
    {structures with an undated row for `agency`}) for D-42."""
    first, undated = {}, set()
    for r in approval_rows:
        if (r.get("type") or "").strip() != agency:
            continue
        sid = parse_struct_id(r.get("struct_id"))
        if sid is None:
            continue
        when = parse_date(r.get("approval"))
        if when is None:
            undated.add(sid)
        elif sid not in first or when < first[sid]:
            first[sid] = when
    return first, frozenset(undated)


def fda_approved_structures(approval_rows: Iterable[dict], agency: str) -> frozenset:
    """DrugCentral `approval` rows -> structure ids with a row for `agency` (dated or not)."""
    out = set()
    for r in approval_rows:
        if (r.get("type") or "").strip() == agency:
            sid = parse_struct_id(r.get("struct_id"))
            if sid is not None:
                out.add(sid)
    return frozenset(out)
