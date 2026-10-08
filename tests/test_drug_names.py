"""Tests for drug_names. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

from trial_pos.services.drug_names import (
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
