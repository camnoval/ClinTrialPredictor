"""TA-9 mapper tests: ICD-chapter rules, name keywords, multi-hot union, and the honest
empty-bucket behaviour (respiratory/digestive/blood have no TA-9 home)."""
from trial_pos.services import disease_ta as D


def test_icd_chapters():
    assert D.ta_from_icd10("C50.9") == {"ta7"}     # breast cancer -> oncology
    assert D.ta_from_icd10("D05") == {"ta7"}        # in-situ neoplasm -> oncology
    assert D.ta_from_icd10("E11") == {"ta6"}        # type 2 diabetes -> metabolic/endo
    assert D.ta_from_icd10("I50") == {"ta2"}        # heart failure -> cardiovascular
    assert D.ta_from_icd10("G20") == {"ta3"}        # parkinson -> CNS
    assert D.ta_from_icd10("F20") == {"ta3"}        # schizophrenia -> CNS
    assert D.ta_from_icd10("H40") == {"ta8"}        # glaucoma -> ophthalmology
    assert D.ta_from_icd10("N18") == {"ta4"}        # chronic kidney -> genitourinary
    assert D.ta_from_icd10("A15") == {"ta5"}        # TB -> infectious
    assert D.ta_from_icd10("M05") == {"ta1"}        # rheumatoid arthritis -> autoimmune


def test_icd_empty_buckets_are_honest():
    # TA-9 has no respiratory / digestive / ear / blood bucket -> empty, by design not bug
    assert D.ta_from_icd10("J45") == set()          # asthma
    assert D.ta_from_icd10("K50") == set()          # crohn's (ICD digestive) -> caught by name, not ICD
    assert D.ta_from_icd10("D55") == set()          # anaemia (blood)
    assert D.ta_from_icd10("H66") == set()          # otitis media (ear)
    assert D.ta_from_icd10("nonsense") == set()


def test_name_keywords_and_multihot():
    assert D.ta_from_name("Non-small cell lung carcinoma") == {"ta7"}
    assert "ta5" in D.ta_from_name("chronic hepatitis B")
    assert D.ta_from_name("multiple sclerosis") == {"ta3", "ta1"}   # multi-hot
    assert D.ta_from_name("influenza vaccine") == {"ta9", "ta5"}
    assert D.ta_from_name("") == set()


def test_assign_unions_icd_and_name():
    # metastatic breast cancer: ICD C50 -> ta7; name adds nothing new -> {ta7}
    assert D.assign_ta(["C50.9"], ["breast cancer"]) == {"ta7"}
    # a unit with a cardiac ICD AND a diabetes name -> both TAs (multi-hot)
    assert D.assign_ta(["I25"], ["type 2 diabetes with cardiac risk"]) == {"ta2", "ta6"}
    # crohn's: no ICD TA bucket (K chapter) but the name rescues it -> ta1
    assert D.assign_ta(["K50.0"], ["crohn's disease"]) == {"ta1"}
    # nothing resolvable -> empty (the zero-TA case the audit must report)
    assert D.assign_ta(["J45"], ["asthma"]) == set()