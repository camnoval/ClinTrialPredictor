"""Name -> DrugCentral drug dictionaries, from DrugCentral and Drugs@FDA. Pure.

A DRUG here is a DrugCentral structure id, mapped to its parent through `struct2parent`
(salt to parent), so 'imatinib mesylate' and 'imatinib' are the same drug. A dictionary
entry maps a name key to a SET of drugs: a combination product names several at once.

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

AMBIGUITY IS KEPT, NOT RESOLVED. A key that maps to more than one distinct drug set within
its deciding source is AMBIGUOUS: the resolver stops there and records it, rather than
picking one or falling through to a lower-priority source that happens to be unique.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable, NamedTuple, Optional

from trial_pos.services.drug_names import (
    INDEX_COMPACT, INDEX_EXACT, Variant, compact, key, variants,
)
from trial_pos.services.drugsatfda import INGREDIENT_SEPARATORS, norm_appl, norm_product

SRC_SYNONYM = "synonym"
SRC_STRUCTURE = "structure"
SRC_OB_TRADE = "ob_trade"
SRC_OB_INGREDIENT = "ob_ingredient"
SRC_FDA_BRAND = "fda_brand"
SRC_FDA_INGREDIENT = "fda_ingredient"
SOURCES = (SRC_SYNONYM, SRC_STRUCTURE, SRC_OB_TRADE, SRC_OB_INGREDIENT, SRC_FDA_BRAND,
           SRC_FDA_INGREDIENT)
DRUGCENTRAL_NAME_SOURCES = (SRC_SYNONYM, SRC_STRUCTURE)

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
    """{struct: parent}. Chains are followed to their root; a cycle raises."""
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

    def canonical(self, struct_id: int) -> int:
        return self.parents.get(struct_id, struct_id)

    def add(self, source: str, name, drugs: Iterable[int]) -> None:
        if source not in self.index:
            raise KeyError(source)
        ds = frozenset(self.canonical(d) for d in drugs)
        k = key(name)
        if not k or not ds:
            self.audit[f"{source}: skipped, blank name or no drug"] += 1
            return
        self.index[source][INDEX_EXACT][k].add(ds)
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

    def resolve_name(self, name, sources: tuple = SOURCES):
        """First hit over the ordered variants -> (Hit, Variant or None)."""
        for v in variants(name):
            hit = self.lookup(v, sources)
            if hit.status != HIT_NONE:
                return hit, v
        return NO_HIT, None


def _ingredient_parts(value: str) -> list:
    parts = [value or ""]
    for sep in INGREDIENT_SEPARATORS:
        parts = [p for part in parts for p in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def build_dictionary(synonyms, structures, struct2parent, ob_product, struct2obprod,
                     fda_products, fda_applications) -> DrugDictionary:
    """Every argument is an iterable of dict rows as read from the CSV / tab files."""
    d = DrugDictionary(parent_map(struct2parent))
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

    drugs_of_prod = defaultdict(set)
    for r in struct2obprod:
        sid, pid = parse_struct_id(r.get("struct_id")), parse_struct_id(r.get("prod_id"))
        if sid is None or pid is None:
            raise ValueError(f"struct2obprod row with a blank id: {r}")
        drugs_of_prod[pid].add(d.canonical(sid))
    by_product, by_application = {}, defaultdict(set)
    for r in ob_product:
        pid = parse_struct_id(r.get("id"))
        drugs = frozenset(drugs_of_prod.get(pid, ()))
        if not drugs:
            d.audit["ob_product: product with no drug in struct2obprod"] += 1
            continue
        d.add(SRC_OB_TRADE, r.get("trade_name"), drugs)
        d.add(SRC_OB_INGREDIENT, r.get("ingredient"), drugs)
        appl = norm_appl(r.get("appl_no"))
        by_product[(appl, norm_product(r.get("product_no")))] = drugs
        by_application[appl].add(drugs)

    known_appl = {norm_appl(r.get("ApplNo")) for r in fda_applications}
    for r in fda_products:
        appl, prod = norm_appl(r.get("ApplNo")), norm_product(r.get("ProductNo"))
        if appl not in known_appl:
            d.audit["fda_product: application absent from Applications"] += 1
        drugs, how = by_product.get((appl, prod)), FDA_BY_OB_PRODUCT
        if drugs is None and len(by_application.get(appl, ())) == 1:
            drugs, how = next(iter(by_application[appl])), FDA_BY_OB_APPLICATION
        if drugs is None:
            drugs, how = _drugs_from_ingredient(d, r.get("ActiveIngredient"))
        d.audit[f"fda_product linked by: {how}"] += 1
        if drugs:
            d.add(SRC_FDA_BRAND, r.get("DrugName"), drugs)
            d.add(SRC_FDA_INGREDIENT, r.get("ActiveIngredient"), drugs)
    return d


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
