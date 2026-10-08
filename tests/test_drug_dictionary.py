"""Tests for drug_dictionary. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.drug_dictionary import (
    FDA_BY_INGREDIENT, FDA_BY_OB_APPLICATION, FDA_BY_OB_PRODUCT, HIT_AMBIGUOUS, HIT_NONE,
    HIT_RESOLVED, SOURCES, SRC_FDA_BRAND, SRC_OB_TRADE, SRC_SYNONYM, build_dictionary,
    parent_map, parse_struct_id,
)
from trial_pos.services.drug_names import variants


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


def _first(d, name):
    return d.resolve_name(name)[0]


SALT, PARENT, OTHER, BIO = 11, 10, 20, 30


def _dc(**over):
    base = dict(
        synonyms=[{"id": str(PARENT), "lname": "imatinib"},
                  {"id": str(SALT), "lname": "imatinib mesylate"},
                  {"id": "", "lname": "orphan name"},
                  {"id": str(BIO), "lname": "pembrolizumab"}],
        structures=[{"id": str(OTHER), "name": "metformin"}],
        struct2parent=[{"struct_id": str(SALT), "parent_id": str(PARENT)}],
        ob_product=[{"id": "1", "ingredient": "IMATINIB MESYLATE", "trade_name": "GLEEVEC",
                     "appl_no": "21588", "product_no": "1"},
                    {"id": "2", "ingredient": "METFORMIN", "trade_name": "SHARED",
                     "appl_no": "000002", "product_no": "001"},
                    {"id": "3", "ingredient": "IMATINIB", "trade_name": "SHARED",
                     "appl_no": "000003", "product_no": "001"}],
        struct2obprod=[{"struct_id": str(SALT), "prod_id": "1"},
                       {"struct_id": str(OTHER), "prod_id": "2"},
                       {"struct_id": str(PARENT), "prod_id": "3"}],
        fda_products=[{"ApplNo": "021588", "ProductNo": "001", "DrugName": "GLEEVEC",
                       "ActiveIngredient": "IMATINIB MESYLATE"},
                      {"ApplNo": "021588", "ProductNo": "002", "DrugName": "GLEEVEC HD",
                       "ActiveIngredient": "IMATINIB MESYLATE"},
                      {"ApplNo": "125514", "ProductNo": "001", "DrugName": "KEYTRUDA",
                       "ActiveIngredient": "PEMBROLIZUMAB"},
                      {"ApplNo": "999999", "ProductNo": "001", "DrugName": "MYSTERY",
                       "ActiveIngredient": "NOTHING KNOWN"}],
        fda_applications=[{"ApplNo": "021588"}, {"ApplNo": "125514"}, {"ApplNo": "999999"}])
    base.update(over)
    return build_dictionary(**base)


def test_parent_map_follows_chains_and_refuses_cycles_and_blanks():
    assert parent_map([{"struct_id": "1", "parent_id": "2"},
                       {"struct_id": "2", "parent_id": "3"}]) == {1: 3, 2: 3}
    assert _raises(parent_map, [{"struct_id": "1", "parent_id": "2"},
                                {"struct_id": "2", "parent_id": "1"}])
    assert _raises(parent_map, [{"struct_id": "", "parent_id": "2"}])
    assert _raises(parse_struct_id, "12a") and parse_struct_id(" ") is None


def test_salts_resolve_to_their_parent():
    d = _dc()
    assert _first(d, "Imatinib mesylate").drugs == frozenset({PARENT})
    assert _first(d, "imatinib").drugs == frozenset({PARENT})


def test_synonyms_without_a_drug_are_dropped_and_counted():
    d = _dc()
    assert _first(d, "orphan name").status == HIT_NONE
    assert any("without a drug id" in k and v == 1 for k, v in d.audit.items())


def test_a_name_naming_two_drugs_in_its_deciding_source_is_ambiguous():
    hit = _first(_dc(), "shared")
    assert hit.status == HIT_AMBIGUOUS and hit.source == SRC_OB_TRADE
    assert len(hit.candidates) == 2 and not hit.drugs


def test_a_higher_priority_source_decides_before_a_lower_one():
    d = _dc(synonyms=[{"id": str(OTHER), "lname": "shared"}])
    hit = _first(d, "shared")
    assert hit.status == HIT_RESOLVED and hit.source == SRC_SYNONYM
    assert SOURCES.index(SRC_SYNONYM) < SOURCES.index(SRC_OB_TRADE)


def test_drugs_at_fda_links_by_product_then_application_then_ingredient():
    d = _dc()
    audit = {k.split(": ")[-1]: v for k, v in d.audit.items() if "linked by" in k}
    assert audit.get(FDA_BY_OB_PRODUCT) == 1           # GLEEVEC 001
    assert audit.get(FDA_BY_OB_APPLICATION) == 1       # GLEEVEC HD 002, one drug set
    assert audit.get(FDA_BY_INGREDIENT) == 1           # KEYTRUDA, a BLA
    keytruda = _first(d, "Keytruda")
    assert keytruda.drugs == frozenset({BIO}) and keytruda.source == SRC_FDA_BRAND
    assert _first(d, "mystery").status == HIT_NONE


def test_lookup_uses_the_compact_index_for_compact_variants():
    d = _dc(synonyms=[{"id": "5", "lname": "mk-3475"}])
    compact = [v for v in variants("MK 3475") if v.index == "compact"]
    assert compact and d.lookup(compact[0]).drugs == frozenset({5})
