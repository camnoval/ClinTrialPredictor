"""Resolver tests: variant generation (comma-swap, parenthetical) and exact vs oncology-aug
matching against a normalized MONDO name index."""
from trial_pos.services import disease_type as DT


def test_variants_comma_swap_and_paren():
    v = DT.label_variants("Lung, Non-Small Cell")
    assert "non small cell lung" in v          # comma-swapped
    v2 = DT.label_variants("Pain (neuropathic)")
    assert "pain" in v2 and "neuropathic pain" in v2   # paren stripped + recombined


def test_exact_match_beats_augmentation():
    idx = {"asthma": {"MONDO:A"}, "asthma cancer": {"MONDO:WRONG"}}
    mondo, method, var = DT.resolve_label("Asthma", idx)
    assert mondo == {"MONDO:A"} and method == "exact"


def test_oncology_site_augmentation():
    # "Breast" alone won't match; "breast cancer" should, via onc-aug
    idx = {"breast cancer": {"MONDO:BR"}}
    mondo, method, var = DT.resolve_label("Breast", idx)
    assert mondo == {"MONDO:BR"} and method == "onc-aug" and var == "breast cancer"


def test_comma_swap_resolves():
    idx = {"acute myelogenous leukemia": {"MONDO:AML"}}
    mondo, method, _ = DT.resolve_label("Leukemia, Acute Myelogenous", idx)
    assert mondo == {"MONDO:AML"} and method == "exact"


def test_unresolved_is_flagged_not_forced():
    mondo, method, _ = DT.resolve_label("Bone Fracture Healing", {"asthma": {"MONDO:A"}})
    assert mondo == set() and method == "unresolved"


def test_diacritic_fold_matches():
    # index built from a MONDO name carrying a diacritic; query without it must still hit
    idx = DT.build_name_index([{"mondo_id": "MONDO:SJ", "name": "Sjögren syndrome", "exact_synonyms": ""}])
    assert idx.get("sjogren syndrome") == {"MONDO:SJ"}
    mondo, method, _ = DT.resolve_label("Sjogren's Syndrome", idx)
    assert mondo == {"MONDO:SJ"} and method == "exact"


def test_build_name_index_includes_synonyms():
    rows = [{"mondo_id": "MONDO:BR", "name": "breast carcinoma", "exact_synonyms": "breast cancer|mammary carcinoma"}]
    idx = DT.build_name_index(rows)
    assert idx["breast cancer"] == {"MONDO:BR"} and idx["breast carcinoma"] == {"MONDO:BR"}