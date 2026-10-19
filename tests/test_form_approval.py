"""Tests for form_approval. Plain asserts so tests/_run_stdlib.py works.

The fixture mirrors cases from audit/probe_form_lineup.py on the owner's data: Doxil vs
doxorubicin, Entyvio IV ('INJECTION') vs SC, a Lopressor injection added by supplement,
Toprol-XL and the Dutoprol combination, and a salt that DrugCentral files as its own
structure (dexamethasone sodium phosphate). Its schema assumptions (Products, Applications,
Submissions, ob_product columns) are the ones probe_source_files_20261001.txt printed.
"""
from __future__ import annotations

from datetime import date

import trial_pos.services.form_approval as fa
from trial_pos.services.drug_dictionary import build_dictionary
from trial_pos.services.drug_names import FORM_CLASS_WORDS, HYDRATE_WORDS, stated_salts
from trial_pos.services.form_approval import (
    APPROVAL_COLUMNS, BRAND_MOIETY, BRAND_PIN, CONFIRMED, DATE_OB_PRODUCT, DATE_ORIGINAL,
    DOSAGE_WORD_TOKENS, EXCLUDED, KIND_DOSAGE, KIND_MODIFIER, KIND_ROUTE, KIND_SALT,
    MODIFIER_TOKENS, POSSIBLE, REQUIREMENT_KINDS, ROUTE_CLASSES, ROUTE_CONFIRMS,
    ROUTE_GENERIC, SET_CONTAINED, SET_EXACT, UNLABELLED_ON_PARENTERAL, AgentApproval, Part,
    Product, ProductIndex, Query, approval_row, approvals, approved_before, build_products,
    check_lineup, first_after, fda_approved_structures, modifier_state, product_state,
    requirement_state, requirements, route_state, split_form, value_states,
    BEFORE_STATUSES, PARENTERAL_OTHER_ROUTES, SALT_MATCH_EQUIVALENT, before_status,
    in_brand_family, is_parenteral, resolve_part, tentative_only, DATE_PURPLE_BOOK,
    PB_PRODUCT_PREFIX, parse_approval_row,
)

DOX, DEXA, DSP, VEDO, METO, HCTZ, NEW = 1, 2, 3, 4, 5, 6, 7


def _raises(fn, *args, exc=ValueError):
    try:
        fn(*args)
    except exc:
        return True
    return False


def _product(form, brand="X", salts=(), ids=frozenset({DOX})):
    dosage, items = split_form(form)
    part = Part("X", tuple(salts), frozenset(ids), frozenset())
    words = frozenset(w for w in brand.lower().replace("-", " ").split())
    return Product("000001", "001", "NDA", form.upper(), dosage, items,
                   frozenset({brand.lower()}), words, (part,), frozenset(ids), frozenset(ids),
                   date(2000, 1, 1), DATE_ORIGINAL)


def _sub(appl, when, kind="ORIG", status="AP"):
    return {"ApplNo": appl, "SubmissionType": kind, "SubmissionStatus": status,
            "SubmissionStatusDate": f"{when} 00:00:00"}


def _fixture(ob_extra=(), fda_approved=()):
    synonyms = [{"id": str(DOX), "lname": "doxorubicin"},
                {"id": str(DOX), "lname": "doxorubicin hydrochloride"},
                {"id": str(DEXA), "lname": "dexamethasone"},
                {"id": str(DSP), "lname": "dexamethasone sodium phosphate"},
                {"id": str(VEDO), "lname": "vedolizumab"},
                {"id": str(METO), "lname": "metoprolol"},
                {"id": str(METO), "lname": "metoprolol succinate"},
                {"id": str(METO), "lname": "metoprolol tartrate"},
                {"id": str(HCTZ), "lname": "hydrochlorothiazide"}]
    structures = [{"id": str(i), "name": n} for i, n in (
        (DOX, "doxorubicin"), (DEXA, "dexamethasone"), (DSP, "dexamethasone sodium phosphate"),
        (VEDO, "vedolizumab"), (METO, "metoprolol"), (HCTZ, "hydrochlorothiazide"),
        (NEW, "newdrug"))]
    ob = [{"id": "10", "ingredient": "METOPROLOL TARTRATE", "trade_name": "LOPRESSOR",
           "appl_no": "017963", "product_no": "001", "appl_type": "N",
           "approval_date": "1978-08-07"},
          {"id": "11", "ingredient": "METOPROLOL TARTRATE", "trade_name": "LOPRESSOR",
           "appl_no": "017963", "product_no": "002", "appl_type": "N",
           "approval_date": "1984-03-30"},
          {"id": "12", "ingredient": "METOPROLOL SUCCINATE", "trade_name": "TOPROL-XL",
           "appl_no": "019962", "product_no": "001", "appl_type": "N",
           "approval_date": "1992-01-10"},
          {"id": "13", "ingredient": "HYDROCHLOROTHIAZIDE; METOPROLOL SUCCINATE",
           "trade_name": "DUTOPROL", "appl_no": "021956", "product_no": "001",
           "appl_type": "N", "approval_date": "2006-08-28"}] + list(ob_extra)
    s2o = [{"struct_id": str(METO), "prod_id": "10"}, {"struct_id": str(METO), "prod_id": "11"},
           {"struct_id": str(METO), "prod_id": "12"}, {"struct_id": str(METO), "prod_id": "13"},
           {"struct_id": str(HCTZ), "prod_id": "13"}]
    s2o += [{"struct_id": str(METO), "prod_id": r["id"]} for r in ob_extra]
    products = [
        {"ApplNo": "050467", "ProductNo": "001", "Form": "INJECTABLE;INJECTION",
         "DrugName": "ADRIAMYCIN", "ActiveIngredient": "DOXORUBICIN HYDROCHLORIDE"},
        {"ApplNo": "050718", "ProductNo": "001", "Form": "INJECTABLE, LIPOSOMAL;INJECTION",
         "DrugName": "DOXIL (LIPOSOMAL)", "ActiveIngredient": "DOXORUBICIN HYDROCHLORIDE"},
        {"ApplNo": "084282", "ProductNo": "001", "Form": "INJECTABLE;INJECTION",
         "DrugName": "DECADRON", "ActiveIngredient": "DEXAMETHASONE SODIUM PHOSPHATE"},
        {"ApplNo": "012345", "ProductNo": "001", "Form": "TABLET;ORAL",
         "DrugName": "DECADRON", "ActiveIngredient": "DEXAMETHASONE"},
        {"ApplNo": "125476", "ProductNo": "001", "Form": "INJECTABLE;INJECTION",
         "DrugName": "ENTYVIO", "ActiveIngredient": "VEDOLIZUMAB"},
        {"ApplNo": "761133", "ProductNo": "001", "Form": "INJECTABLE;SUBCUTANEOUS",
         "DrugName": "ENTYVIO", "ActiveIngredient": "VEDOLIZUMAB"},
        {"ApplNo": "017963", "ProductNo": "001", "Form": "TABLET;ORAL",
         "DrugName": "LOPRESSOR", "ActiveIngredient": "METOPROLOL TARTRATE"},
        {"ApplNo": "017963", "ProductNo": "002", "Form": "INJECTABLE;INJECTION",
         "DrugName": "LOPRESSOR", "ActiveIngredient": "METOPROLOL TARTRATE"},
        {"ApplNo": "019962", "ProductNo": "001", "Form": "TABLET, EXTENDED RELEASE;ORAL",
         "DrugName": "TOPROL-XL", "ActiveIngredient": "METOPROLOL SUCCINATE"},
        {"ApplNo": "021956", "ProductNo": "001", "Form": "TABLET, EXTENDED RELEASE;ORAL",
         "DrugName": "DUTOPROL", "ActiveIngredient": "HYDROCHLOROTHIAZIDE; METOPROLOL SUCCINATE"},
        {"ApplNo": "070001", "ProductNo": "001", "Form": "TABLET;ORAL",
         "DrugName": "GENERIC", "ActiveIngredient": "METOPROLOL TARTRATE"},
        {"ApplNo": "999999", "ProductNo": "001", "Form": "TABLET;ORAL",
         "DrugName": "ORPHAN", "ActiveIngredient": "DOXORUBICIN"},
    ]
    types = {"050467": "NDA", "050718": "NDA", "084282": "NDA", "012345": "NDA",
             "125476": "BLA", "761133": "BLA", "017963": "NDA", "019962": "NDA",
             "021956": "NDA", "070001": "ANDA"}
    apps = [{"ApplNo": a, "ApplType": t} for a, t in types.items()]
    subs = [_sub("050467", "1974-08-07"), _sub("050718", "1995-11-17"),
            _sub("084282", "1960-01-01"), _sub("012345", "1958-10-30"),
            _sub("125476", "2014-05-20"), _sub("761133", "2023-09-27"),
            _sub("017963", "1978-08-07"), _sub("017963", "1984-03-30", "SUPPL"),
            _sub("019962", "1992-01-10"), _sub("021956", "2006-08-28"),
            _sub("070001", "1990-01-01"), _sub("021956", "1900-01-01")]
    d = build_dictionary(synonyms, structures, [], ob, s2o, products, apps)
    prods, audit = build_products(d, products, apps, subs, ob, s2o)
    return ProductIndex(prods, fda_approved), audit, prods


def _q(moieties, salts=(), words=(), brand=""):
    return Query(frozenset(moieties), tuple(salts), tuple(words), brand)


# ---- the line-up -----------------------------------------------------------------------------
def test_the_line_up_is_consistent_and_its_check_fails_closed():
    check_lineup()
    item = next(iter(ROUTE_CONFIRMS))
    fa.ROUTE_SILENT = fa.ROUTE_SILENT | {item}
    try:
        assert _raises(check_lineup)
    finally:
        fa.ROUTE_SILENT = fa.ROUTE_SILENT - {item}
    check_lineup()


def test_every_form_class_is_a_route_or_a_modifier_and_every_word_is_known():
    assert set(ROUTE_CLASSES) | set(MODIFIER_TOKENS) == set(FORM_CLASS_WORDS)
    assert not set(ROUTE_CLASSES) & set(MODIFIER_TOKENS)
    for c, words in FORM_CLASS_WORDS.items():
        for w in words:
            requirements((), (w,))            # never raises for a drug_names word


def test_each_confirming_route_item_confirms_its_classes_and_excludes_unrelated_ones():
    for item, classes in ROUTE_CONFIRMS.items():
        p = _product(f"SOLUTION;{item}")
        for c in ROUTE_CLASSES:
            got = route_state(c, p)
            if c in classes:
                assert got == CONFIRMED, (item, c)
            elif c not in ROUTE_GENERIC.get(item, ()):
                assert got == EXCLUDED, (item, c, got)


def test_generic_injection_is_possible_for_its_classes_and_excludes_the_rest():
    p = _product("INJECTABLE;INJECTION")
    for c in ROUTE_CLASSES:
        want = POSSIBLE if c in ROUTE_GENERIC["INJECTION"] else EXCLUDED
        assert route_state(c, p) == want, c


def test_a_silent_route_half_reads_the_dosage_half():
    for form in ("VIAL", "VIAL; SINGLE-USE", "INJECTION;SOLUTION", "SYRINGE"):
        p = _product(form)
        assert route_state("intravenous", p) == POSSIBLE, form
        assert route_state("oral", p) == EXCLUDED, form
    assert route_state("oral", _product("LIQUID;N/A")) == POSSIBLE
    assert route_state("inhalation", _product("GAS")) == CONFIRMED
    assert route_state("inhalation", _product("INHALANT;ORAL")) == CONFIRMED


def test_modifiers_absent_exclude_except_where_fda_does_not_label_them():
    lipo = _product("INJECTABLE, LIPOSOMAL;INJECTION")
    plain_inj, tablet = _product("INJECTABLE;INJECTION"), _product("TABLET;ORAL")
    assert modifier_state("liposomal", lipo) == CONFIRMED
    assert modifier_state("liposomal", tablet) == EXCLUDED
    for c in MODIFIER_TOKENS:
        want = POSSIBLE if c in UNLABELLED_ON_PARENTERAL else EXCLUDED
        assert modifier_state(c, plain_inj) == want, c
        assert modifier_state(c, _product("N/A;N/A")) == POSSIBLE, c


def test_a_stated_word_in_the_brand_name_confirms_it():
    assert modifier_state("extended_release", _product("TABLET;ORAL", "TOPROL-XL"),
                          ("xl",)) == CONFIRMED
    gel = requirements((), ("gel",))[0]
    assert requirement_state(gel, _product("INJECTABLE;INJECTION", "ACTHAR GEL"),
                             frozenset({DOX})) == CONFIRMED
    assert requirement_state(gel, _product("INJECTABLE;INJECTION", "ACTHAR"),
                             frozenset({DOX})) == EXCLUDED


def test_dosage_form_words_are_tested_on_the_dosage_half_not_as_a_route():
    for w, tokens in DOSAGE_WORD_TOKENS.items():
        (r,) = requirements((), (w,))
        assert r.kind == KIND_DOSAGE
        for t in tokens:
            for route in ("TOPICAL", "TRANSDERMAL", "VAGINAL"):
                assert requirement_state(r, _product(f"{t};{route}"), frozenset({DOX})) \
                    == CONFIRMED, (w, t, route)
        assert requirement_state(r, _product("TABLET;ORAL"), frozenset({DOX})) == EXCLUDED


def test_requirements_group_by_kind_drop_oral_beside_inhalation_and_refuse_unknown_words():
    rs = requirements(("succinate",), ("iv", "sc", "liposomal", "gel"))
    assert [r.kind for r in rs] == sorted((r.kind for r in rs), key=REQUIREMENT_KINDS.index)
    assert rs[0] == (KIND_ROUTE, ("intravenous", "subcutaneous"), ("iv", "sc"))
    assert any(r.kind == KIND_MODIFIER and r.values == ("liposomal",) for r in rs)
    assert rs[-1].kind == KIND_SALT
    (r,) = requirements((), ("oral", "inhalation"))
    assert r.values == ("inhalation",) and "oral" not in r.words
    assert _raises(requirements, (), ("not-a-form-word",))
    assert requirements((), ()) == ()


def test_several_values_of_one_kind_are_confirmed_only_together():
    (r,) = requirements((), ("iv", "sc"))
    both, one = _product("SOLUTION;INTRAVENOUS, SUBCUTANEOUS"), _product("SOLUTION;INTRAVENOUS")
    assert requirement_state(r, both, frozenset({DOX})) == CONFIRMED
    assert requirement_state(r, one, frozenset({DOX})) == POSSIBLE
    assert requirement_state(r, _product("TABLET;ORAL"), frozenset({DOX})) == EXCLUDED


def test_salts_compare_with_the_moietys_part_and_hydrates_never_count():
    (r,) = requirements(("succinate",), ())
    m = frozenset({DOX})
    assert requirement_state(r, _product("TABLET;ORAL", salts=("succinate",)), m) == CONFIRMED
    assert requirement_state(r, _product("TABLET;ORAL", salts=("tartrate",)), m) == EXCLUDED
    assert requirement_state(r, _product("TABLET;ORAL", ids={HCTZ}), m) == POSSIBLE
    for w in HYDRATE_WORDS:
        assert stated_salts(f"cyclophosphamide {w}") == (), w


def test_nothing_stated_means_every_product_of_the_moiety_d14():
    assert product_state((), _product("TABLET;ORAL"), frozenset({DOX})) == CONFIRMED


# ---- identity and dates ------------------------------------------------------------------
def test_a_salt_filed_as_its_own_structure_is_still_a_product_of_the_moiety():
    index, _audit, _prods = _fixture()
    base = approvals(index, _q({DEXA}, words=("iv",)), SET_EXACT, BRAND_MOIETY)
    salt = approvals(index, _q({DSP}, words=("iv",)), SET_EXACT, BRAND_MOIETY)
    assert base.n_identity > salt.n_identity        # the base also has its own tablet
    assert base.n_loose == salt.n_loose == 1 and base.n_strict == 0   # the injection


def test_exact_and_contained_set_rules_differ_only_on_combinations():
    index, _audit, _prods = _fixture()
    q = _q({METO}, salts=("succinate",))
    exact = approvals(index, q, SET_EXACT, BRAND_MOIETY)
    contained = approvals(index, q, SET_CONTAINED, BRAND_MOIETY)
    assert contained.n_strict == exact.n_strict + 1                 # Dutoprol
    combo = approvals(index, _q({METO, HCTZ}), SET_EXACT, BRAND_MOIETY)
    assert combo.n_identity == 1
    assert _raises(approvals, index, q, "neither", BRAND_MOIETY)


def test_anda_and_unknown_applications_never_count_and_are_counted():
    index, audit, prods = _fixture()
    assert all(p.appl_type in fa.INNOVATOR_TYPES for p in prods)
    assert audit["products: application absent from Applications (not used)"] == 1


def test_a_product_added_later_is_dated_by_its_orange_book_date():
    _index, _audit, prods = _fixture()
    by = {(p.appl, p.prod): p for p in prods}
    inj, tab = by[("017963", "002")], by[("017963", "001")]
    assert inj.date_source == DATE_OB_PRODUCT and inj.approved > tab.approved
    assert by[("125476", "001")].date_source == DATE_ORIGINAL       # BLAs: no OB rows


def test_orange_book_dates_earlier_than_the_original_or_in_conflict_are_not_used():
    early = {"id": "20", "ingredient": "METOPROLOL TARTRATE", "trade_name": "LOPRESSOR",
             "appl_no": "019962", "product_no": "001", "appl_type": "N",
             "approval_date": "1980-01-01"}
    _index, audit, prods = _fixture(ob_extra=[early])
    p = {(x.appl, x.prod): x for x in prods}[("019962", "001")]
    assert p.date_source == DATE_ORIGINAL
    assert audit["dates: Orange Book rows of one key disagree (not used)"] == 1


def test_a_placeholder_original_never_dates_a_product():
    _index, _audit, prods = _fixture()
    p = {(x.appl, x.prod): x for x in prods}[("021956", "001")]
    assert p.approved is not None and p.approved >= fa.EARLIEST_REAL_APPROVAL


# ---- answers -------------------------------------------------------------------------------
def _a(strict=(), loose=(), su=0, lu=0, gap=False):
    return AgentApproval(len(loose), tuple(strict), tuple(loose), su, lu, len(strict),
                         len(loose), None, gap)


def test_approved_before_is_true_only_on_strict_proof_and_false_only_on_loose_absence():
    r = date(2010, 1, 1)
    early, late = date(2000, 1, 1), date(2020, 1, 1)
    assert approved_before(_a(strict=(early,), loose=(early,)), r) is True
    assert approved_before(_a(strict=(late,), loose=(late,)), r) is False
    assert approved_before(_a(), r) is False
    assert approved_before(_a(strict=(late,), loose=(early, late)), r) is None
    assert approved_before(_a(loose=(late,), lu=1), r) is None
    assert approved_before(_a(strict=(early,), loose=(early,), gap=True), r) is None


def test_first_after_gives_both_bounds_and_loose_is_never_later():
    a = _a(strict=(date(2023, 1, 1),), loose=(date(2014, 1, 1), date(2023, 1, 1)))
    lo, st = first_after(a, date(2010, 1, 1))
    assert lo <= st
    assert first_after(a, date(2030, 1, 1)) == (None, None)


def test_liposomal_doxorubicin_and_vedolizumab_sc_end_to_end():
    index, _audit, _prods = _fixture()
    lipo = approvals(index, _q({DOX}, words=("liposomal",)), SET_EXACT, BRAND_MOIETY)
    doxil = date(1995, 11, 17)
    assert lipo.strict_dates == (doxil,) and len(lipo.loose_dates) == 2
    assert approved_before(lipo, date(2000, 1, 1)) is True
    assert approved_before(lipo, date(1990, 1, 1)) is None     # plain injection is 'possible'
    sc = approvals(index, _q({VEDO}, words=("sc",)), SET_EXACT, BRAND_MOIETY)
    assert approved_before(sc, date(2020, 1, 1)) is None       # Eylea/Avastin: cannot tell
    assert approved_before(sc, date(2024, 1, 1)) is True
    succ = approvals(index, _q({METO}, salts=("succinate",)), SET_EXACT, BRAND_MOIETY)
    assert approved_before(succ, date(1990, 1, 1)) is False


def test_a_brand_pins_its_own_products_and_falls_back_when_it_has_none():
    index, _audit, _prods = _fixture()
    pinned = approvals(index, _q({DOX}, brand="doxil"), SET_EXACT, BRAND_PIN)
    moiety = approvals(index, _q({DOX}, brand="doxil"), SET_EXACT, BRAND_MOIETY)
    assert pinned.brand_pinned is True and moiety.brand_pinned is None
    assert pinned.n_strict < moiety.n_strict
    lost = approvals(index, _q({DOX}, brand="nosuchbrand"), SET_EXACT, BRAND_PIN)
    assert lost.brand_pinned is False and lost.n_strict == moiety.n_strict
    assert _raises(approvals, index, _q({DOX}), SET_EXACT, "neither")


def test_a_moiety_fda_approved_elsewhere_but_absent_from_drugsatfda_is_undeterminable():
    index, _audit, _prods = _fixture(fda_approved={NEW})
    a = approvals(index, _q({NEW}), SET_EXACT, BRAND_MOIETY)
    assert a.source_gap and approved_before(a, date(2010, 1, 1)) is None
    index2, _a2, _p2 = _fixture()
    b = approvals(index2, _q({NEW}), SET_EXACT, BRAND_MOIETY)
    assert not b.source_gap and approved_before(b, date(2010, 1, 1)) is False


def test_fda_approved_structures_reads_one_agency():
    rows = [{"struct_id": "1", "type": "FDA"}, {"struct_id": "2", "type": "EMA"},
            {"struct_id": "", "type": "FDA"}]
    assert fda_approved_structures(rows, "FDA") == frozenset({1})


def test_approval_rows_carry_every_column_with_sorted_dates():
    index, _audit, _prods = _fixture()
    q = _q({DOX}, words=("liposomal",))
    row = approval_row("NCT1", 9, "1", q, approvals(index, q, SET_EXACT, BRAND_MOIETY),
                       SET_EXACT, BRAND_MOIETY)
    assert tuple(row) == APPROVAL_COLUMNS
    dates = row["loose_dates"].split(fa.DATE_SEP)
    assert dates == sorted(dates)


def test_value_states_test_each_stated_value_alone_and_say_none_without_products():
    index, _audit, _prods = _fixture()
    got = value_states(index, _q({VEDO}, words=("iv", "sc")), SET_EXACT, BRAND_MOIETY)
    assert got == [(KIND_ROUTE, "intravenous", POSSIBLE), (KIND_ROUTE, "subcutaneous", CONFIRMED)]
    none = value_states(index, _q({NEW}, words=("iv",)), SET_EXACT, BRAND_MOIETY)
    assert none == [(KIND_ROUTE, "intravenous", None)]


def test_before_status_names_why_an_answer_is_missing():
    r = date(2010, 1, 1)
    early, late = date(2000, 1, 1), date(2020, 1, 1)
    cases = [_a(strict=(early,), loose=(early,)), _a(strict=(late,), loose=(late,)),
             _a(strict=(late,), loose=(early, late)), _a(loose=(late,), lu=1),
             _a(strict=(early,), loose=(early,), gap=True),
             _a()._replace(dc_fda_first=early), _a()._replace(dc_fda_undated=True)]
    got = [before_status(a, r) for a in cases]
    assert got == list(BEFORE_STATUSES)


# ---- fixes from the matcher run on the owner's data, 2026-10-13 ---------------------------
def test_tentative_only_applications_are_not_approvals_but_a_later_approval_is():
    subs = [_sub("200001", "2015-01-01", status="TA"),
            _sub("200002", "2014-01-01", status="TA"), _sub("200002", "2016-01-01")]
    assert tentative_only(subs) == frozenset({"200001"})
    index, audit, prods = _fixture()
    assert audit["products: tentative approval only (not an approval, not used)"] == 0


def test_a_tentative_product_never_makes_an_agent_undeterminable():
    extra_prod = {"ApplNo": "208000", "ProductNo": "001", "Form": "TABLET;ORAL",
                  "DrugName": "GENERIC DOX", "ActiveIngredient": "DOXORUBICIN"}
    d_sub = [_sub("208000", "2020-01-01", status="TA")]
    apps = [{"ApplNo": "208000", "ApplType": "NDA"}]
    d = build_dictionary([{"id": str(DOX), "lname": "doxorubicin"}],
                         [{"id": str(DOX), "name": "doxorubicin"}], [], [], [], [extra_prod], apps)
    got, a2 = build_products(d, [extra_prod], apps, d_sub, [], [])
    assert got == [] and a2["products: tentative approval only (not an approval, not used)"] == 1


def test_a_brand_pins_its_family_not_only_its_exact_name():
    assert in_brand_family("zofran", {"zofran odt"})
    assert in_brand_family("marcaine", {"marcaine hydrochloride"})
    assert in_brand_family("zofran", {"zofran"})
    assert not in_brand_family("zofran", {"zofranx"}) and not in_brand_family("zofran", set())


def test_injection_type_specific_routes_are_parenteral_for_unlabelled_modifiers():
    for route in sorted(PARENTERAL_OTHER_ROUTES):
        p = _product(f"INJECTABLE;{route}")
        assert is_parenteral(p), route
        for c in UNLABELLED_ON_PARENTERAL:
            assert modifier_state(c, p) == POSSIBLE, (route, c)
    assert not is_parenteral(_product("TABLET;ORAL"))


def test_equivalent_salt_names_match_and_a_part_naming_no_salt_is_silent():
    m = frozenset({DOX})
    for stated, written in SALT_MATCH_EQUIVALENT.items():
        (r,) = requirements((stated,), ())
        assert requirement_state(r, _product("TABLET;ORAL", salts=(written,)), m) == CONFIRMED
        (r2,) = requirements((written,), ())
        assert requirement_state(r2, _product("TABLET;ORAL", salts=(stated,)), m) == CONFIRMED
    (r,) = requirements(("hydrochloride",), ())
    assert requirement_state(r, _product("INJECTABLE;INJECTION", salts=()), m) == POSSIBLE
    assert requirement_state(r, _product("TABLET;ORAL", salts=("mesylate",)), m) == EXCLUDED


def test_metoprolol_salts_still_exclude_each_other():
    index, _audit, _prods = _fixture()
    succ = approvals(index, _q({METO}, salts=("succinate",)), SET_EXACT, BRAND_MOIETY)
    assert succ.loose_dates == succ.strict_dates          # tartrate products stay excluded


def test_an_fda_four_letter_suffix_comes_off_a_product_ingredient_whatever_the_stem():
    d = build_dictionary([{"id": "40", "lname": "somatrogon"}],
                         [{"id": "40", "name": "somatrogon"}], [], [], [], [], [])
    part = resolve_part(d, "SOMATROGON-GHLA")
    assert part.direct == frozenset({40})
    assert resolve_part(d, "UNKNOWNDRUG-ABCD").direct == frozenset()


def test_topical_and_transdermal_are_possible_for_each_other():
    assert route_state("topical", _product("GEL;TRANSDERMAL")) == POSSIBLE
    assert route_state("transdermal", _product("PATCH;TOPICAL")) == POSSIBLE
    assert route_state("topical", _product("CREAM;TOPICAL")) == CONFIRMED
    assert route_state("topical", _product("TABLET;ORAL")) == EXCLUDED


# ---- D-41 the Purple Book, D-42 the DrugCentral guards ----------------------------------
from trial_pos.services.purplebook import (  # noqa: E402
    REQUIRED_COLUMNS, parse_pb_date, pb_form, read_purplebook,
)


def _pb(bla="125476", product="001", proper="vedolizumab", route="Intravenous",
        when="May 20, 2014", brand="Entyvio", center="CDER", dosage="Injection"):
    return {"BLA Number": bla, "Product Number": product, "Proprietary Name": brand,
            "Proper Name": proper, "Dosage Form": dosage, "Route of Administration": route,
            "Licensure": "Licensed", "Approval Date": when, "Submission Type": "Original",
            "Center": center}


def test_the_purple_book_reader_fails_closed_and_keeps_routes_and_dates():
    rows, audit = read_purplebook([_pb(), _pb(product="002", route="Intravenous, Subcutaneous",
                                              when="")])
    assert rows[0].approved == date(2014, 5, 20) and rows[1].approved is None
    assert rows[1].form == "INJECTION;INTRAVENOUS, SUBCUTANEOUS"
    assert audit["purple book rows undated: CDER"] == 1
    assert pb_form("Injection", "Subcutaneous") == "INJECTION;SUBCUTANEOUS"
    assert parse_pb_date(" ") is None
    for bad in ([_pb(), _pb()],                                  # repeated key
                [_pb(when="2014-05-20")],                        # wrong date format
                [_pb(center="CDRH")],                            # unknown centre
                [{k: v for k, v in _pb().items() if k != REQUIRED_COLUMNS[0]}],
                []):
        assert _raises(read_purplebook, bad), bad


def test_a_purple_book_route_confirms_what_drugsatfda_left_generic():
    d = build_dictionary([{"id": str(VEDO), "lname": "vedolizumab"}],
                         [{"id": str(VEDO), "name": "vedolizumab"}], [], [], [], [], [])
    pb, _a2 = read_purplebook([_pb(), _pb(bla="761133", route="Subcutaneous",
                                          when="September 27, 2023")])
    prods, audit = build_products(d, [], [], [], [], [], purplebook=pb)
    assert all(p.prod.startswith(PB_PRODUCT_PREFIX) and p.date_source == DATE_PURPLE_BOOK
               for p in prods)
    index = ProductIndex(prods)
    sc = approvals(index, _q({VEDO}, words=("sc",)), SET_EXACT, BRAND_MOIETY)
    assert sc.strict_dates == (date(2023, 9, 27),) and sc.loose_dates == sc.strict_dates
    assert approved_before(sc, date(2020, 1, 1)) is False      # was undeterminable


def test_an_unresolved_purple_book_name_is_counted_and_not_used():
    d = build_dictionary([], [], [], [], [], [], [])
    pb, _a2 = read_purplebook([_pb(proper="Pneumococcal 13-valent Conjugate Vaccine",
                                   center="CBER")])
    prods, audit = build_products(d, [], [], [], [], [], purplebook=pb)
    assert prods == [] and audit["purple book: proper name unresolved (CBER), not used"] == 1


def test_drugcentral_guards_only_ever_turn_not_approved_into_undeterminable():
    r = date(2010, 1, 1)
    never = _a()
    assert approved_before(never, r) is False
    assert approved_before(never._replace(dc_fda_first=date(2000, 5, 17)), r) is None
    assert approved_before(never._replace(dc_fda_first=date(2017, 9, 1)), r) is False
    assert approved_before(never._replace(dc_fda_undated=True), r) is None
    proved = _a(strict=(date(2000, 1, 1),))
    assert approved_before(proved._replace(dc_fda_undated=True), r) is True


def test_guard_a_applies_only_to_a_single_unstated_unpinned_moiety():
    index, _audit, prods = _fixture()
    index = ProductIndex(prods, fda_first={DOX: date(1970, 1, 1), METO: date(1970, 1, 1)})
    assert approvals(index, _q({DOX}), SET_EXACT, BRAND_MOIETY).dc_fda_first is not None
    assert approvals(index, _q({DOX}, words=("liposomal",)), SET_EXACT,
                     BRAND_MOIETY).dc_fda_first is None
    assert approvals(index, _q({DOX}, brand="doxil"), SET_EXACT,
                     BRAND_PIN).dc_fda_first is None
    assert approvals(index, _q({METO, HCTZ}), SET_EXACT, BRAND_MOIETY).dc_fda_first is None


def test_the_guard_fields_round_trip_through_the_approvals_file():
    a = _a(strict=(date(2012, 1, 1),))._replace(dc_fda_first=date(2000, 5, 17),
                                                  dc_fda_undated=True)
    row = approval_row("NCT1", 1, "1", _q({DOX}), a, SET_EXACT, BRAND_MOIETY)
    assert parse_approval_row(row) == a


def test_fda_approval_dates_keep_the_earliest_and_the_undated():
    from trial_pos.services.form_approval import fda_approval_dates
    rows = [{"struct_id": "1", "type": "FDA", "approval": "2005-01-01"},
            {"struct_id": "1", "type": "FDA", "approval": "2000-05-17"},
            {"struct_id": "2", "type": "FDA", "approval": ""},
            {"struct_id": "3", "type": "EMA", "approval": "1990-01-01"}]
    first, undated = fda_approval_dates(rows, "FDA")
    assert first == {1: date(2000, 5, 17)} and undated == frozenset({2})
