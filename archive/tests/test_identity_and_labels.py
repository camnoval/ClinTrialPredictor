from trial_pos.domain.records import Label, Provenance
from trial_pos.foundation.identity import DrugIndication, NCTId


def test_nctid_validation():
    assert NCTId("NCT00000102").value == "NCT00000102"
    for bad in ("NCT123", "00000102", "nct00000102"):
        try:
            NCTId(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected error for {bad}")


def test_drug_indication_normalizes():
    assert DrugIndication.of("  Aspirin ", "Headache") == DrugIndication.of("aspirin", "headache")


def test_measured_wins():
    r = Label.resolve([Label(0, Provenance.DERIVED), Label(1, Provenance.MEASURED)])
    assert r.approved == 1 and r.provenance is Provenance.MEASURED


def test_all_unlabeled():
    r = Label.resolve([Label(None), Label(None)])
    assert r.approved is None and r.provenance is Provenance.UNKNOWN
