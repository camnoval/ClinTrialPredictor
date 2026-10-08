"""Tests for drug_names. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.drug_names import (
    FORM_CLASSES, FORM_CLASS_WORDS, NO_FORM, SALT_CANONICAL, comma_components,
    has_salt_word, is_abbreviation, is_biologic_root, stated_form, stated_salts,
    COMBINATION_SPLIT, INDEX_COMPACT, INDEX_EXACT, MIN_COMPONENT_LENGTH, SALT_WORDS, STEPS,
    STEP_COMPACT, STEP_EXACT, active_components, compact, components, drop_biosimilar_suffix,
    drop_dose_and_form, drop_salt, is_placebo, is_pure_placebo, key, variants,
)


def test_key_folds_case_dashes_and_whitespace():
    assert key("  Pembro\u2013LIZUMAB \t x ") == key("pembro-lizumab x")
    assert key(None) == "" and key("") == ""


def test_compact_unifies_code_spellings():
    assert compact("MK-3475") == compact("mk 3475") == compact("MK3475")


def test_digits_inside_codes_survive_dose_stripping():
    for code in ("mk-3475", "covid-19 vaccine", "interferon alfa-2b", "5-fluorouracil"):
        assert drop_dose_and_form(code) == code, code


def test_doses_and_forms_are_stripped():
    assert drop_dose_and_form(key("Pembrolizumab 200 mg IV")) == "pembrolizumab"
    assert drop_dose_and_form(key("Metformin 500 mg tablets")) == "metformin"


def test_components_split_on_agent_separators_but_not_commas():
    assert components("Alpha + Beta") == ["alpha", "beta"]
    assert components("Alpha/Beta") == components("alpha or beta") == ["alpha", "beta"]
    assert len(components("acetic acid, glacial")) == 1
    assert "," not in COMBINATION_SPLIT.pattern


def test_doses_go_before_splitting_and_debris_is_dropped():
    assert components("Kisspeptin 9.6 nmol/kg") == ["kisspeptin"]
    assert all(len(c) >= MIN_COMPONENT_LENGTH for c in components("Vaccine B/E"))


def test_placebo_is_a_whole_name_property():
    assert is_pure_placebo("Placebo") and is_pure_placebo("Matching placebo tablets")
    assert is_pure_placebo("Placebo for AIDSVAX B/E")
    assert not is_pure_placebo("Drug X or placebo")
    assert active_components("Drug X or placebo") == ["drug x"]
    assert active_components("Kisspeptin + saline") == ["kisspeptin"]
    assert is_placebo("Sham procedure") and not is_placebo("drug x")


def test_a_name_with_no_components_left_is_not_placebo():
    assert not is_pure_placebo("X") and not is_pure_placebo("")


def test_salt_is_dropped_only_from_the_end_and_never_to_nothing():
    salt = sorted(SALT_WORDS)[0]
    assert drop_salt(f"drug {salt}") == "drug"
    assert drop_salt(salt) == salt
    assert drop_salt(f"{salt} drug") == f"{salt} drug"


def test_biosimilar_suffix_is_exactly_four_letters():
    assert drop_biosimilar_suffix("adalimumab-aacf") == "adalimumab"
    assert drop_biosimilar_suffix("interferon alfa-2b") == "interferon alfa-2b"
    assert drop_biosimilar_suffix("drug-abc") == "drug-abc"


def test_variants_start_exact_follow_step_order_and_never_repeat():
    v = variants("MK-3475 (pembrolizumab) 200 mg")
    assert v[0].step == STEP_EXACT and v[0].index == INDEX_EXACT
    order = [STEPS.index(x.step) for x in v]
    assert order == sorted(order)
    assert len({(x.index, x.key) for x in v}) == len(v)
    assert all((x.index == INDEX_COMPACT) == (x.step == STEP_COMPACT) for x in v)
    assert any(x.key == "pembrolizumab" for x in v) and any(x.key == "mk-3475" for x in v)


def test_variants_of_blank_are_empty():
    assert variants("") == [] and variants(None) == []


# ---- 2026-10-07 additions, each from a name in audit/probe_unresolved.py's output -----------
def test_formulation_qualifiers_are_stripped_but_pegylated_and_low_are_not():
    for name, root in (("liposomal bupivacaine", "bupivacaine"),
                       ("unfractionated heparin", "heparin"),
                       ("buprenorphine transdermal patch", "buprenorphine"),
                       ("low-dose aspirin", "aspirin"), ("Low dose aspirin", "aspirin"),
                       ("high dose methotrexate", "methotrexate")):
        assert drop_dose_and_form(key(name)) == root, name
    assert drop_dose_and_form("low molecular weight heparin") == "low molecular weight heparin"
    assert drop_dose_and_form(key("pegylated liposomal doxorubicin")).startswith("pegylated")


def test_locants_and_thousands_survive():
    for name in ("2,4-dinitrophenol", "1,25-dihydroxyvitamin d3"):
        assert drop_dose_and_form(name) == name and components(name) == [name], name
    assert components("Vitamin D 50,000 IU") == ["vitamin d"]


def test_commas_split_only_as_a_separate_step_and_never_a_locant():
    assert comma_components("mk0653, ezetimibe") == ["mk0653", "ezetimibe"]
    assert comma_components("acetic acid, glacial") == ["acetic acid", "glacial"]
    assert comma_components("2,4-dinitrophenol") == ["2,4-dinitrophenol"]
    assert comma_components("Placebo, matching") == []
    assert len(components("mk0653, ezetimibe")) == 1


def test_salt_words_are_detected_anywhere_in_a_key():
    salt = sorted(SALT_WORDS)[0]
    assert has_salt_word(f"copper {salt}") and has_salt_word(f"{salt} x")
    assert not has_salt_word("fludarabine")


# ---- stated form, D-12 to D-15; every name below occurs in the owner's data ---------------
def test_stated_form_reads_salt_and_formulation_from_names():
    f = stated_form(["Metoprolol succinate ER"])
    assert f.salts == ("succinate",) and f.classes == ("extended_release",) and f.stated
    assert stated_form(["Vedolizumab SC"]).classes == ("subcutaneous",)
    assert stated_form(["Liposomal bupivacaine"]).classes == ("liposomal",)
    assert stated_form(["nicotine patch"]).classes == ("transdermal",)
    assert stated_form(["IV acetaminophen"]).classes == ("intravenous",)
    assert stated_form(["Metformin extended release"]).classes == ("extended_release",)
    assert stated_form(["Diclofenac sodium gel"]).salts == ("sodium",)


def test_a_drug_that_is_itself_a_salt_states_no_salt():
    assert stated_salts("Sodium Chloride 0.9%") == ()
    assert stated_form(["Pembrolizumab"]) == NO_FORM and not NO_FORM.stated


def test_salt_spellings_are_canonical_and_free_base_is_not_a_salt():
    assert stated_salts("Bupivacaine HCl") == (SALT_CANONICAL["hcl"],)
    assert stated_salts("Imatinib mesilate") == (SALT_CANONICAL["mesilate"],)
    assert stated_salts("Teriflunomide free base") == ()


def test_the_form_is_read_across_every_name_of_the_intervention():
    f = stated_form(["Invega", "Paliperidone ER"])
    assert f.classes == ("extended_release",)
    assert stated_form(["Drug X", "Drug X tartrate", "Drug X succinate"]).salts == (
        "succinate", "tartrate")


def test_form_vocabulary_is_closed_and_short_tokens_match_whole_words_only():
    assert set(FORM_CLASSES) == set(FORM_CLASS_WORDS)
    words = [w for ws in FORM_CLASS_WORDS.values() for w in ws]
    assert len(words) == len(set(words)), "a word may name one class only"
    assert stated_form(["Ivermectin"]).classes == ()        # 'iv' inside a word
    assert stated_form(["Scopolamine"]).classes == ()       # 'sc' inside a word


# ---- D-20, D-21: errors from the 2026-10-07 route review --------------------------------------
def test_the_biosimilar_suffix_comes_off_biologics_only():
    for name, root in (("adalimumab-aacf", "adalimumab"), ("hyaluronidase-fihj", "hyaluronidase"),
                       ("epoetin alfa-epbx", "epoetin alfa"),
                       ("insulin glargine-yfgn", "insulin glargine"),
                       ("crizanlizumab-tmca", "crizanlizumab")):
        assert drop_biosimilar_suffix(name) == root, name
    for name in ("latanoprost-ppds", "tace-haic", "ibrutinib-rice", "pentoxifylline-teva",
                 "lysine-mdma"):
        assert drop_biosimilar_suffix(name) == name, name
    assert is_biologic_root("insulin aspart") and not is_biologic_root("latanoprost")


def test_abbreviations_by_length_digits_and_capitals():
    for k in ("inh", "bal", "dv", "ats", "mtx"):
        assert is_abbreviation(k), k
    for k in ("5-fu", "s-1"):
        assert not is_abbreviation(k), k
    assert is_abbreviation("tace", "ADCC & TACE")
    assert not is_abbreviation("iron", "Ferumoxytol or oral iron")
    assert not is_abbreviation("tace") and not is_abbreviation("bupivacaine")


# ---- D-23: radiotracers keep their isotope -----------------------------------------------------
def test_isotope_brackets_are_never_dropped_but_other_brackets_are():
    from trial_pos.services.drug_names import drop_parentheticals
    assert drop_parentheticals("[18f]t4") == "[18f]t4"
    assert drop_parentheticals("[99mtc] sestamibi") == "[99mtc] sestamibi"
    assert drop_parentheticals("bevacizumab [avastin]") == "bevacizumab"
    assert "t4" not in [v.key for v in variants("[18F]T4") if v.step != STEP_COMPACT]
