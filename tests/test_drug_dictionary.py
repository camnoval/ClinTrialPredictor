"""Tests for drug_dictionary. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.drug_dictionary import (
    FDA_BY_INGREDIENT, FDA_BY_OB_APPLICATION, FDA_BY_OB_PRODUCT, HIT_AMBIGUOUS, HIT_NONE,
    PARENT_GROUP_PREFIX,
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


def test_a_salt_keeps_its_own_structure_id_and_gets_a_namespaced_parent_group():
    d = _dc()
    assert _first(d, "Imatinib mesylate").drugs == frozenset({SALT})
    assert _first(d, "imatinib").drugs == frozenset({PARENT})
    assert d.parent_group(SALT) == f"{PARENT_GROUP_PREFIX}{PARENT}"
    assert d.parent_group(PARENT) is None


def test_a_parentmol_id_equal_to_an_unrelated_structure_id_never_conflates_them():
    """The 2026-10-07 bug: struct2parent's parent ids are parentmol ids. Fludarabine
    phosphate (child) had a parent id equal to amfetamine's structure id."""
    FLUP, AMF = 1189, 77
    d = build_dictionary([{"id": str(AMF), "lname": "amfetamine"},
                          {"id": str(FLUP), "lname": "fludarabine phosphate"}], [],
                         [{"struct_id": str(FLUP), "parent_id": str(AMF)}], [], [], [], [])
    assert d.resolve("fludarabine")[0].drugs == frozenset({FLUP})
    assert d.resolve("fludarabine phosphate")[0].drugs == frozenset({FLUP})
    assert d.resolve("amfetamine")[0].drugs == frozenset({AMF})
    assert d.parent_group(FLUP) != str(AMF) and not isinstance(d.parent_group(FLUP), int)


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


# ---- dictionary-side salt, 2026-10-07 ------------------------------------------------------
from trial_pos.services.drug_dictionary import (  # noqa: E402
    SRC_DRUGCENTRAL_SALT_STRIPPED, STEP_DICTIONARY_NO_SALT,
)

FLU, CU, FLP, FLF, BEM = 50, 60, 70, 71, 80


def _salt_dc(synonyms=(), ob_product=(), struct2obprod=()):
    return build_dictionary(list(synonyms), [], [], list(ob_product), list(struct2obprod), [],
                            [])


def test_a_moiety_reaches_the_only_salt_drugcentral_holds():
    d = _salt_dc([{"id": str(FLU), "lname": "fludarabine phosphate"}])
    hit, v = d.resolve("Fludarabine")
    assert hit.drugs == frozenset({FLU}) and hit.source == SRC_DRUGCENTRAL_SALT_STRIPPED
    assert v.step == STEP_DICTIONARY_NO_SALT
    assert d.resolve("fludarabine (flu)")[0].drugs == frozenset({FLU})
    assert d.resolve_name("fludarabine")[0].status == HIT_NONE    # forward alone misses


def test_a_forward_hit_always_wins_over_the_salt_step():
    d = _salt_dc([{"id": str(FLU), "lname": "fludarabine phosphate"},
                  {"id": "99", "lname": "fludarabine"}])
    assert d.resolve("fludarabine")[0].drugs == frozenset({99})


def test_one_salt_never_reaches_another_and_bare_elements_never_match():
    d = _salt_dc([{"id": str(CU), "lname": "copper sulfate"}])
    assert d.resolve("copper gluconate")[0].status == HIT_NONE
    assert d.resolve("copper")[0].status == HIT_NONE


def test_a_root_naming_two_drugs_is_ambiguous():
    d = _salt_dc([{"id": str(FLP), "lname": "fluticasone propionate"},
                  {"id": str(FLF), "lname": "fluticasone furoate"}])
    hit, _v = d.resolve("fluticasone")
    assert hit.status == HIT_AMBIGUOUS and len(hit.candidates) == 2


def test_ester_siblings_collide_on_their_root():
    d = _salt_dc([{"id": "90", "lname": "testosterone undecanoate"},
                  {"id": "91", "lname": "testosterone cypionate"},
                  {"id": "92", "lname": "triamcinolone acetonide"}])
    assert d.resolve("testosterone")[0].status == HIT_AMBIGUOUS
    assert d.resolve("triamcinolone")[0].drugs == frozenset({92})


def test_orange_book_names_are_never_salt_stripped():
    d = _salt_dc(ob_product=[{"id": "1", "ingredient": "DALTEPARIN SODIUM",
                              "trade_name": "X", "appl_no": "1", "product_no": "1"}],
                 struct2obprod=[{"struct_id": str(BEM), "prod_id": "1"}])
    assert d.resolve("dalteparin")[0].status == HIT_NONE
    assert d.resolve("dalteparin sodium")[0].drugs == frozenset({BEM})   # forward, as before


# ---- D-22 known source errors; D-23 alkyl fragments -------------------------------------------
from trial_pos.services.drug_dictionary import (  # noqa: E402
    ALKYL_ROOT, KNOWN_SOURCE_ERRORS, REVERSE_QUERY_STEPS, SourceError,
)
from trial_pos.services.drug_names import STEP_PARENTHETICAL  # noqa: E402

BEM, DIR, DMF = 61, 62, 63


def test_a_known_source_error_removes_the_wrong_drug_and_counts_it():
    d = build_dictionary(
        [{"id": str(BEM), "lname": "bemiparin"},
         {"id": str(BEM), "lname": "dalteparin sodium"}],
        [{"id": str(BEM), "name": "bemiparin"}], [],
        [{"id": "1", "ingredient": "DALTEPARIN SODIUM", "trade_name": "FRAGMIN",
          "appl_no": "1", "product_no": "1"}],
        [{"struct_id": str(BEM), "prod_id": "1"}], [], [])
    for name in ("dalteparin", "Dalteparin sodium", "dalteparin (fragmin)", "Fragmin"):
        assert d.resolve(name)[0].status == HIT_NONE, name
    assert d.resolve("bemiparin")[0].drugs == frozenset({BEM})
    assert any("known source error removed" in k for k in d.audit)


def test_every_known_source_error_carries_evidence_and_compiles():
    import re
    for e in KNOWN_SOURCE_ERRORS:
        assert isinstance(e, SourceError) and e.evidence and e.wrong_drug
        re.compile(e.name_pattern)


def test_a_source_error_naming_a_missing_drug_is_counted_not_fatal():
    d = build_dictionary([{"id": "1", "lname": "x"}], [], [], [], [], [], [])
    assert any("not a structure" in k for k in d.audit)


def test_alkyl_fragments_are_never_salt_stripped_keys():
    d = build_dictionary([{"id": str(DMF), "lname": "dimethyl fumarate"},
                          {"id": "64", "lname": "sodium myristyl sulfate"}],
                         [], [], [], [], [], [])
    assert d.resolve("dimethyl")[0].status == HIT_NONE
    assert d.resolve("myristyl")[0].status == HIT_NONE
    assert ALKYL_ROOT.search("dimethyl") and not ALKYL_ROOT.search("fludarabine")
    assert STEP_PARENTHETICAL not in REVERSE_QUERY_STEPS
    long_name = "n-(5-((2-(dimethylamino)ethyl)(methyl)amino)phenyl)acrylamide tosylate"
    assert d.resolve(long_name)[0].status == HIT_NONE
