"""Name -> DrugCentral drug dictionaries, from DrugCentral and Drugs@FDA. Pure.

A DRUG here is a DrugCentral STRUCTURE id (`structures.id`), exactly as matched, never
remapped. A dictionary entry maps a name key to a SET of drugs: a combination product names
several at once.

PARENT GROUPS, NOT PARENT IDS (bug found 2026-10-07, docs/Handoff_rev11.md D-19).
`struct2parent.parent_id` references DrugCentral's `parentmol` table, NOT `structures`.
Using it as a drug id conflated unrelated drugs whose structure id happened to equal a
parentmol id ('fludarabine' -> 'amfetamine', every insulin -> one id). It is kept only as a
PARENT GROUP in its own namespace (`PARENT_GROUP_PREFIX`), so salts of one parent can be
grouped without ever being mistaken for a structure.

SOURCES, tried in priority order; the first source with an entry for a key decides:
  synonym            DrugCentral synonyms (rows without a drug id are dropped, counted)
  structure          DrugCentral structure names
  ob_trade           Orange Book trade names, via struct2obprod
  ob_ingredient      Orange Book ingredient strings, via struct2obprod
  fda_brand          Drugs@FDA brand names
  fda_ingredient     Drugs@FDA active-ingredient strings

Drugs@FDA reaches a drug three ways, in this order, each counted:
  1. its (application, product) in ob_product
  2. its application in ob_product, when every Orange Book product of that application
     names the same drugs
  3. its ingredient string, split on ';' and '||' only, each part resolved against the
     DrugCentral names alone. This is the only way for BLAs, which the Orange Book
     does not hold, and for the NDAs it lacks.

DICTIONARY-SIDE SALT (added 2026-10-07, audit/probe_unresolved.py section 1). DrugCentral
sometimes holds only the salt ('fludarabine phosphate') where trials name the moiety
('fludarabine'). After every forward step misses, the query is looked up against
DrugCentral's OWN names (synonyms, structures) with their trailing salt words removed.
Three guards, each from a failure the probe showed:
  - the query spelling must carry no salt word, so one salt never matches another
    ('copper gluconate' must not reach 'copper sulfate')
  - a stripped key that is a bare element is never indexed ('copper', 'barium')
  - unique only: 'fluticasone' naming both the propionate and the furoate is ambiguous
Orange Book and Drugs@FDA names are excluded: pooling them produced 'dalteparin ->
bemiparin' in the probe.

AMBIGUITY IS KEPT, NOT RESOLVED. A key that maps to more than one distinct drug set within
its deciding source is AMBIGUOUS: the resolver stops there and records it, rather than
picking one or falling through to a lower-priority source that happens to be unique.
"""
from __future__ import annotations

import re

from collections import Counter, defaultdict
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_names import (
    ELEMENT_ROOTS, INDEX_COMPACT, INDEX_EXACT, STEP_EXACT, STEP_NO_DOSE_FORM,
    STEP_NO_PARENTHETICALS, STEP_PARENTHETICAL, Variant, compact, drop_dose_and_form,
    drop_parentheticals, drop_salt, has_salt_word, is_abbreviation, key, variants,
)
from trial_pos.services.drugsatfda import INGREDIENT_SEPARATORS, norm_appl, norm_product
from trial_pos.services.purplebook import (
    inn_spelling, purple_book_id, purple_book_identity_key,
)

_WORD = re.compile(r"[a-z0-9]+")

SRC_SYNONYM = "synonym"
SRC_STRUCTURE = "structure"
SRC_OB_TRADE = "ob_trade"
SRC_OB_INGREDIENT = "ob_ingredient"
SRC_FDA_BRAND = "fda_brand"
SRC_FDA_INGREDIENT = "fda_ingredient"
# D-46: last, so a DrugCentral or Drugs@FDA name always wins over a Purple Book identity.
SRC_PURPLE_BOOK = "purple_book"
# D-47: development codes learned from guarded registry resolutions; the very last source.
SRC_LEARNED_CODE = "learned_code"
SOURCES = (SRC_SYNONYM, SRC_STRUCTURE, SRC_OB_TRADE, SRC_OB_INGREDIENT, SRC_FDA_BRAND,
           SRC_FDA_INGREDIENT, SRC_PURPLE_BOOK, SRC_LEARNED_CODE)
# D-46: the word-order-free lookup, Purple Book identities only ('13-valent pneumococcal
# conjugate vaccine' is 'Pneumococcal 13-valent Conjugate Vaccine (Diphtheria CRM197...)').
SRC_PURPLE_BOOK_WORDS = "purple_book_words"
STEP_WORD_SET = "word_set"
PB_IDENTITIES_CREATED = "purple book: identities created"
MIN_WORD_SET = 2                  # one word is an exact key already
DRUGCENTRAL_NAME_SOURCES = (SRC_SYNONYM, SRC_STRUCTURE)
SRC_DRUGCENTRAL_SALT_STRIPPED = "drugcentral_salt_stripped"
STEP_DICTIONARY_NO_SALT = "dictionary_no_salt"
# Query spellings the salt-stripped lookup may use: never the query's own salt-stripped or
# compact forms, so a salt is never swapped and codes are never collapsed.
REVERSE_QUERY_STEPS = (STEP_EXACT, STEP_NO_PARENTHETICALS, STEP_NO_DOSE_FORM)
# Not STEP_PARENTHETICAL: the inside of a bracket in a chemical name ('(dimethylamino)')
# reached 'dimethyl fumarate' that way (D-23).

# A salt-stripped root ending like an alkyl group ('dimethyl', 'myristyl', 'cetyl') names a
# chemical fragment, not a drug: never indexed (D-23).
ALKYL_ROOT = re.compile(r"yl$")


class SourceError(NamedTuple):
    """A link in the sources that is wrong. Names matching `name_pattern` (on the key) never
    reach `wrong_drug` (a DrugCentral structure name). Nothing is remapped: the name is left
    to resolve however else it can, usually not at all."""
    name_pattern: str
    wrong_drug: str
    evidence: str


# D-22 (docs/Handoff_rev11.md). Curated: every entry needs evidence from a run on the
# owner's data, and the run prints how often each entry fired.
KNOWN_SOURCE_ERRORS = (
    SourceError(r"\b(?:dalteparin|fragmin)\b", "bemiparin",
                "resolve_trial_drugs_20261001_v4.txt: 'dalteparin' (19 agents) and "
                "'dalteparin (fragmin)' (2) reach bemiparin, a different heparin, after the "
                "struct2parent fix (D-19), so the link is in the source"),
    SourceError(r"\b(?:monomethyl fumarate|bafiertam)\b", "diroximel fumarate",
                "resolve_trial_drugs_20261001_v4.txt: 'monomethyl fumarate 190 mg' (2) "
                "reaches diroximel fumarate (Vumerity) through the Orange Book ingredient; "
                "monomethyl fumarate is Bafiertam, a separate product"),
)

PARENT_GROUP_PREFIX = "pm"

HIT_RESOLVED = "resolved"
HIT_AMBIGUOUS = "ambiguous"
HIT_NONE = "no_hit"

FDA_BY_OB_PRODUCT = "ob_product"
FDA_BY_OB_APPLICATION = "ob_application"
FDA_BY_INGREDIENT = "ingredient_name"
FDA_UNLINKED = "unlinked"
FDA_AMBIGUOUS = "ambiguous"
FDA_LINKS = (FDA_BY_OB_PRODUCT, FDA_BY_OB_APPLICATION, FDA_BY_INGREDIENT, FDA_UNLINKED,
             FDA_AMBIGUOUS)


class Hit(NamedTuple):
    status: str
    drugs: frozenset          # the drugs when resolved; empty otherwise
    candidates: tuple         # every distinct drug set when ambiguous, sorted
    source: Optional[str]


NO_HIT = Hit(HIT_NONE, frozenset(), (), None)


def parse_struct_id(raw) -> Optional[int]:
    """'' -> None (counted by the caller); anything else must be an integer."""
    s = str(raw if raw is not None else "").strip()
    if not s:
        return None
    if not s.lstrip("-").isdigit():
        raise ValueError(f"struct id {raw!r} is not an integer")
    return int(s)


def parent_map(struct2parent: Iterable[dict]) -> dict:
    """{structure id: parentmol id}. The values are in the PARENTMOL id space: never compare
    them with structure ids. Chains are followed to their root; a cycle raises."""
    direct = {}
    for r in struct2parent:
        child, parent = parse_struct_id(r.get("struct_id")), parse_struct_id(r.get("parent_id"))
        if child is None or parent is None:
            raise ValueError(f"struct2parent row with a blank id: {r}")
        direct[child] = parent
    out = {}
    for child in direct:
        seen, node = {child}, direct[child]
        while node in direct:
            if node in seen:
                raise ValueError(f"struct2parent cycle through {node}")
            seen.add(node)
            node = direct[node]
        out[child] = node
    return out


class DrugDictionary:
    def __init__(self, parents: dict):
        self.parents = parents
        self.index = {src: {INDEX_EXACT: defaultdict(set), INDEX_COMPACT: defaultdict(set)}
                      for src in SOURCES}
        self.audit = Counter()
        self.salt_stripped = defaultdict(set)
        self.source_errors = []        # [(compiled pattern, wrong structure id, SourceError)]
        self.names_of = defaultdict(set)   # drug id -> every name key indexed for it (D-44)
        self.pb_word_sets = defaultdict(set)   # D-46: frozenset of words -> {Purple Book id}
        self.pb_names = {}                 # D-46: Purple Book id -> identity key
        self.pb_identity_of = {}           # D-46: key(proper, INN spelling) -> Purple Book id

    def set_source_errors(self, errors: Iterable[SourceError], structure_ids: dict) -> None:
        """`structure_ids`: {structure name key: id}. An entry whose drug is not in
        DrugCentral is counted and ignored."""
        self.source_errors = []
        for e in errors:
            sid = structure_ids.get(key(e.wrong_drug))
            if sid is None:
                self.audit[f"known source error: '{e.wrong_drug}' not a structure"] += 1
                continue
            self.source_errors.append((re.compile(e.name_pattern), sid, e))

    def parent_group(self, struct_id: int) -> Optional[str]:
        """The parentmol group of a structure, namespaced; None when it has no parent row."""
        pid = self.parents.get(struct_id)
        return None if pid is None else f"{PARENT_GROUP_PREFIX}{pid}"

    def parent_groups(self, drugs: Iterable[int]) -> frozenset:
        return frozenset(g for g in (self.parent_group(x) for x in drugs) if g)

    def add(self, source: str, name, drugs: Iterable[int]) -> None:
        if source not in self.index:
            raise KeyError(source)
        ds = frozenset(drugs)
        k = key(name)
        for pattern, sid, e in self.source_errors:
            if sid in ds and pattern.search(k):
                ds = ds - {sid}
                self.audit[f"known source error removed: {e.wrong_drug} from '{k}'"] += 1
        if not k or not ds:
            self.audit[f"{source}: skipped, blank name or no drug"] += 1
            return
        self.index[source][INDEX_EXACT][k].add(ds)
        for x in ds:
            self.names_of[x].add(k)
        c = compact(name)
        if c:
            self.index[source][INDEX_COMPACT][c].add(ds)
        self.audit[f"{source}: entries added"] += 1

    def lookup(self, variant: Variant, sources: tuple = SOURCES) -> Hit:
        for src in sources:
            found = self.index[src][variant.index].get(variant.key)
            if not found:
                continue
            if len(found) == 1:
                return Hit(HIT_RESOLVED, next(iter(found)), (), src)
            ordered = tuple(sorted(found, key=lambda s: sorted(s)))
            return Hit(HIT_AMBIGUOUS, frozenset(), ordered, src)
        return NO_HIT

    @staticmethod
    def _usable(v: Variant, allow_short: bool, original: str = "") -> bool:
        """An abbreviation-length key is usable only as the exact whole name, and only when
        the caller says the name IS the whole intervention name (D-20)."""
        return not is_abbreviation(v.key, original) or (allow_short and v.step == STEP_EXACT)

    def resolve_name(self, name, sources: tuple = SOURCES, allow_short: bool = False,
                     original: str = ""):
        """First FORWARD hit over the ordered variants -> (Hit, Variant or None).
        `original`: the registry's spelling of the whole name, for the capitals test of
        D-20 (a component arrives lower-cased); defaults to `name`."""
        original = original or str(name or "")
        for v in variants(name):
            if not self._usable(v, allow_short, original):
                self.audit["abbreviation-length key not looked up"] += 1
                continue
            hit = self.lookup(v, sources)
            if hit.status != HIT_NONE:
                return hit, v
        return NO_HIT, None

    def index_salt_stripped(self) -> None:
        """Build the dictionary-side salt index from DrugCentral's own names."""
        self.salt_stripped.clear()
        for src in DRUGCENTRAL_NAME_SOURCES:
            for k, sets in self.index[src][INDEX_EXACT].items():
                stripped = drop_salt(k)
                if stripped == k:
                    continue
                if stripped in ELEMENT_ROOTS:
                    self.audit["salt-stripped key skipped: bare element"] += 1
                    continue
                if ALKYL_ROOT.search(stripped):
                    self.audit["salt-stripped key skipped: alkyl fragment"] += 1
                    continue
                self.salt_stripped[stripped] |= sets
        self.audit["salt-stripped keys indexed"] = len(self.salt_stripped)

    def resolve_salt_stripped(self, name, original: str = ""):
        """The dictionary-side salt step alone -> (Hit, Variant or None)."""
        original = original or str(name or "")
        for v in variants(name):
            if v.step not in REVERSE_QUERY_STEPS or v.index != INDEX_EXACT:
                continue
            if not self._usable(v, False, original):
                continue
            if has_salt_word(v.key):
                continue
            found = self.salt_stripped.get(v.key)
            if not found:
                continue
            step = Variant(STEP_DICTIONARY_NO_SALT, v.key, v.index)
            if len(found) == 1:
                return Hit(HIT_RESOLVED, next(iter(found)), (),
                           SRC_DRUGCENTRAL_SALT_STRIPPED), step
            ordered = tuple(sorted(found, key=lambda x: sorted(x)))
            return Hit(HIT_AMBIGUOUS, frozenset(), ordered, SRC_DRUGCENTRAL_SALT_STRIPPED), step
        return NO_HIT, None

    def resolve(self, name, allow_short: bool = False, original: str = ""):
        """Every forward step, the dictionary-side salt step, then (D-46) the Purple Book
        word-set step."""
        hit, v = self.resolve_name(name, allow_short=allow_short, original=original)
        if hit.status != HIT_NONE:
            return hit, v
        hit, v = self.resolve_salt_stripped(name, original=original)
        if hit.status != HIT_NONE:
            return hit, v
        return self.resolve_word_set(name)

    def resolve_word_set(self, name):
        """D-46: the name's words, in any order, as those of one Purple Book identity."""
        k = drop_parentheticals(key(name))
        for candidate in (k, drop_dose_and_form(k)):
            words = frozenset(_WORD.findall(candidate))
            if len(words) < MIN_WORD_SET:
                continue
            found = self.pb_word_sets.get(words)
            if not found:
                continue
            v = Variant(STEP_WORD_SET, " ".join(sorted(words)), INDEX_EXACT)
            if len(found) == 1:
                return Hit(HIT_RESOLVED, frozenset(found), (), SRC_PURPLE_BOOK_WORDS), v
            ordered = tuple(frozenset({x}) for x in sorted(found))
            return Hit(HIT_AMBIGUOUS, frozenset(), ordered, SRC_PURPLE_BOOK_WORDS), v
        return NO_HIT, None

    def purple_book_identity(self, proper: str) -> Optional[int]:
        """The D-46 identity created for this exact proper name, or None. The matcher uses
        it instead of re-resolving: another product's alias can share the name
        ('poliovirus vaccine inactivated'), which re-resolution reads as ambiguous."""
        return self.pb_identity_of.get(key(inn_spelling(proper)))

    def add_purple_book(self, rows: Iterable) -> None:
        """D-46: an identity for each Purple Book proper name no other source resolves.
        Proper names that resolve (DrugCentral, Drugs@FDA) are left to that identity."""
        seen = {}
        others = tuple(src for src in SOURCES
                       if src not in (SRC_PURPLE_BOOK, SRC_LEARNED_CODE))
        for r in rows:
            proper = inn_spelling(r.proper)
            # Only DrugCentral and Drugs@FDA names decide: never another Purple Book entry,
            # so 'anti-thymocyte globulin (equine)' is not absorbed by '(rabbit)' and the
            # result does not depend on row order.
            hit, _v = self.resolve_name(proper, sources=others)
            if hit.status == HIT_NONE:
                hit, _v = self.resolve_salt_stripped(proper)
            if hit.status == HIT_RESOLVED:
                self.audit["purple book: proper name already resolves, no new identity"] += 1
                continue
            if hit.status == HIT_AMBIGUOUS:
                self.audit["purple book: proper name ambiguous, no identity"] += 1
                continue
            pk = key(proper)
            ik = purple_book_identity_key(r.proper)
            pid = purple_book_id(ik)
            if seen.setdefault(pid, ik) != ik:
                raise ValueError(f"Purple Book id collision: {seen[pid]!r} and {ik!r}")
            if pid not in self.pb_names:
                self.audit[PB_IDENTITIES_CREATED] += 1
            self.pb_names[pid] = ik
            self.pb_identity_of[pk] = pid
            names = {pk, drop_parentheticals(pk), ik, drop_parentheticals(ik),
                     key(r.proprietary)}
            for n in sorted(x for x in names if x):
                self.add(SRC_PURPLE_BOOK, n, {pid})
            words = frozenset(_WORD.findall(drop_parentheticals(ik)))
            if len(words) >= MIN_WORD_SET:
                self.pb_word_sets[words].add(pid)


def _ingredient_parts(value: str) -> list:
    parts = [value or ""]
    for sep in INGREDIENT_SEPARATORS:
        parts = [p for part in parts for p in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def build_dictionary(synonyms, structures, struct2parent, ob_product, struct2obprod,
                     fda_products, fda_applications, purplebook: Iterable = ()) -> DrugDictionary:
    """Every argument is an iterable of dict rows as read from the CSV / tab files, except
    `purplebook`: purplebook.PurpleBookRow tuples (D-46), added after everything else."""
    d = DrugDictionary(parent_map(struct2parent))
    structures = list(structures)
    d.set_source_errors(KNOWN_SOURCE_ERRORS,
                        {key(r.get("name")): parse_struct_id(r.get("id")) for r in structures
                         if parse_struct_id(r.get("id")) is not None})
    for r in synonyms:
        sid = parse_struct_id(r.get("id"))
        if sid is None:
            d.audit["synonym: row without a drug id, dropped"] += 1
            continue
        d.add(SRC_SYNONYM, r.get("lname") or r.get("name"), (sid,))
    for r in structures:
        sid = parse_struct_id(r.get("id"))
        if sid is None:
            raise ValueError(f"structures row without an id: {r}")
        d.add(SRC_STRUCTURE, r.get("name"), (sid,))

    by_product, by_application, linked_rows = ob_product_index(ob_product, struct2obprod,
                                                               d.audit)
    for r, drugs in linked_rows:
        d.add(SRC_OB_TRADE, r.get("trade_name"), drugs)
        d.add(SRC_OB_INGREDIENT, r.get("ingredient"), drugs)

    known_appl = {norm_appl(r.get("ApplNo")) for r in fda_applications}
    for r in fda_products:
        if norm_appl(r.get("ApplNo")) not in known_appl:
            d.audit["fda_product: application absent from Applications"] += 1
        drugs, how = fda_product_link(d, r, by_product, by_application)
        d.audit[f"fda_product linked by: {how}"] += 1
        if drugs:
            d.add(SRC_FDA_BRAND, r.get("DrugName"), drugs)
            d.add(SRC_FDA_INGREDIENT, r.get("ActiveIngredient"), drugs)
    d.index_salt_stripped()
    d.add_purple_book(purplebook)
    return d


OB_KEY_REPEATED = "ob_product: (appl_no, product_no) on more than one row"
OB_KEY_DRUGS_DIFFER = "ob_product: ... and those rows name different drugs"


def ob_product_index(ob_product, struct2obprod, audit: Optional[Counter] = None) -> tuple:
    """Orange Book products with their DrugCentral drugs ->
    (by_product {(appl, product): drugs}, by_application {appl: {drugs}},
     linked_rows [(row, drugs)] in input order).

    The (appl_no, product_no) pair is NOT unique in ob_product (audit/probe_form_lineup.py
    section 4: 5,531 keys on more than one row). The last row wins, as it always has;
    `audit` counts repeats and repeats that name different drugs, so the cost is visible."""
    audit = audit if audit is not None else Counter()
    drugs_of_prod = defaultdict(set)
    for r in struct2obprod:
        sid, pid = parse_struct_id(r.get("struct_id")), parse_struct_id(r.get("prod_id"))
        if sid is None or pid is None:
            raise ValueError(f"struct2obprod row with a blank id: {r}")
        drugs_of_prod[pid].add(sid)
    by_product, by_application, linked_rows, seen = {}, defaultdict(set), [], {}
    for r in ob_product:
        pid = parse_struct_id(r.get("id"))
        drugs = frozenset(drugs_of_prod.get(pid, ()))
        if not drugs:
            audit["ob_product: product with no drug in struct2obprod"] += 1
            continue
        linked_rows.append((r, drugs))
        appl = norm_appl(r.get("appl_no"))
        k = (appl, norm_product(r.get("product_no")))
        if k in seen:
            audit[OB_KEY_REPEATED] += 1
            if seen[k] != drugs:
                audit[OB_KEY_DRUGS_DIFFER] += 1
        seen[k] = drugs
        by_product[k] = drugs
        by_application[appl].add(drugs)
    return by_product, by_application, linked_rows


def fda_product_link(d: DrugDictionary, row: dict, by_product: dict,
                     by_application: dict) -> tuple:
    """One Drugs@FDA Products row -> (drugs or None, FDA_LINKS entry): its (application,
    product) in ob_product; else its application, when every Orange Book product of it
    names the same drugs; else its ingredient string (each part unique among DrugCentral
    names)."""
    appl, prod = norm_appl(row.get("ApplNo")), norm_product(row.get("ProductNo"))
    drugs = by_product.get((appl, prod))
    if drugs is not None:
        return drugs, FDA_BY_OB_PRODUCT
    if len(by_application.get(appl, ())) == 1:
        return next(iter(by_application[appl])), FDA_BY_OB_APPLICATION
    return _drugs_from_ingredient(d, row.get("ActiveIngredient"))


def _drugs_from_ingredient(d: DrugDictionary, value):
    """Each ingredient part must resolve UNIQUELY against DrugCentral names alone."""
    parts = _ingredient_parts(value)
    if not parts:
        return None, FDA_UNLINKED
    drugs = set()
    for part in parts:
        hit, _v = d.resolve_name(part, DRUGCENTRAL_NAME_SOURCES)
        if hit.status == HIT_AMBIGUOUS:
            return None, FDA_AMBIGUOUS
        if hit.status != HIT_RESOLVED:
            return None, FDA_UNLINKED
        drugs |= hit.drugs
    return frozenset(drugs), FDA_BY_INGREDIENT
