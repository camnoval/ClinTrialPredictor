"""TOP loader tests. Inline fixture in the real TOP schema
(nctid,status,why_stop,label,phase,diseases,icdcodes,drugs,smiless,criteria).
Stdlib only -- runs under scripts/run_checks.py in a bare env.
"""
import csv
import tempfile
from pathlib import Path

from trial_pos.domain.records import Provenance
from trial_pos.sources.top_source import TopSource

_HEADER = ["nctid", "status", "why_stop", "label", "phase",
           "diseases", "icdcodes", "drugs", "smiless", "criteria"]

_ROWS = [
    # completed, approved, two drugs / one disease
    ["NCT00000102", "completed", "", "1", "Phase 2",
     "['asthma']", "['J45']", "['drugA', 'drugB']", "['CCO', 'CCN']",
     "inclusion: adults; exclusion: pregnancy"],
    # terminated, failed, why_stop present
    ["NCT00000104", "terminated", "low accrual", "0", "Phase 1",
     "['migraine', 'cluster headache']", "['G43', 'G44']", "['drugC']", "['CCC']",
     "inclusion: 18+"],
    # missing label -> approved None
    ["NCT00000105", "recruiting", "", "", "Phase 3",
     "['diabetes']", "['E11']", "['drugD']", "['CCO']", "n/a"],
    # blank id -> dropped
    ["", "completed", "", "1", "Phase 2", "['x']", "['A00']", "['d']", "['C']", "c"],
]


def _write_fixture() -> Path:
    tmp = Path(tempfile.mkdtemp()) / "phase_II_train.csv"
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_HEADER)
        w.writerows(_ROWS)
    return tmp


def test_load_shapes_and_fields():
    src = TopSource(_write_fixture())
    recs = src.run()
    # blank-id row dropped -> 3 records
    assert [r.nct_id.value for r in recs] == ["NCT00000102", "NCT00000104", "NCT00000105"]

    r0 = recs[0]
    assert r0.label.approved == 1 and r0.label.provenance is Provenance.MEASURED
    assert r0.drug == "drugA" and r0.indication == "asthma"      # first of each list
    assert r0.features["n_drugs"] == 2 and r0.features["n_diseases"] == 1
    assert r0.features["has_why_stop"] == 0
    # TOP phase files carry no dates
    assert r0.start_date is None and r0.completion_date is None

    r1 = recs[1]
    assert r1.label.approved == 0
    assert r1.features["n_diseases"] == 2 and r1.features["n_icd"] == 2
    assert r1.features["has_why_stop"] == 1

    r2 = recs[2]
    assert r2.label.approved is None   # blank label


def test_missing_id_column_fails_loud():
    tmp = Path(tempfile.mkdtemp()) / "bad.csv"
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "label", "phase"])   # no 'nctid'
        w.writerow(["NCT00000102", "1", "Phase 2"])
    try:
        TopSource(tmp).fetch()
    except KeyError:
        return
    raise AssertionError("expected KeyError when nctid column is absent")