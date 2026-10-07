"""Tests for the Zenodo upload helpers. Plain asserts so tests/_run_stdlib.py works."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from trial_pos.services.zenodo import (
    ACTION_CONFLICT, ACTION_SKIP, ACTION_UPLOAD, MD5_PREFIX, ZenodoError, deposition_url,
    editable, file_delete_url, md5_file, normalise_md5, record_from_doi, remote_file_ids,
    remote_files, upload_plan,
)

_MD5 = hashlib.md5(b"x").hexdigest()
_OTHER = hashlib.md5(b"y").hexdigest()


def _raises(fn) -> bool:
    try:
        fn()
    except ZenodoError:
        return True
    return False


def test_record_from_doi():
    assert record_from_doi("10.5281/zenodo.23197696") == "23197696"
    for bad in ("10.1234/zenodo.1", "10.5281/zenodo.", "10.5281/zenodo.12a", ""):
        assert _raises(lambda: record_from_doi(bad)), bad


def test_deposition_url_requires_a_numeric_record():
    assert deposition_url("42").endswith("/deposit/depositions/42")
    assert _raises(lambda: deposition_url("../42"))


def test_md5_accepts_bare_or_prefixed_and_rejects_others():
    assert normalise_md5(_MD5) == normalise_md5(MD5_PREFIX + _MD5.upper()) == _MD5
    for bad in ("sha256:" + "0" * 64, "abc", None):
        assert _raises(lambda: normalise_md5(bad)), bad


def test_md5_file_matches_hashlib():
    p = Path(tempfile.mkdtemp()) / "f"
    p.write_bytes(b"x")
    assert md5_file(p) == _MD5


def test_only_an_unpublished_draft_with_a_bucket_is_editable():
    editable({"submitted": False, "links": {"bucket": "b"}})
    for dep in ({"submitted": True, "links": {"bucket": "b"}},
                {"links": {"bucket": "b"}}, {"submitted": False, "links": {}}):
        assert _raises(lambda: editable(dep)), dep


def test_remote_files_reads_both_entry_shapes():
    dep = {"files": [{"filename": "a", "filesize": 1, "checksum": _MD5},
                     {"key": "b", "size": 2, "checksum": MD5_PREFIX + _OTHER}]}
    assert remote_files(dep) == {"a": (1, _MD5), "b": (2, _OTHER)}
    assert remote_files({}) == {}
    assert _raises(lambda: remote_files({"files": [{"checksum": _MD5}]}))


def test_plan_uploads_skips_and_flags_conflicts():
    entries = [{"zenodo_name": n, "bytes": 1} for n in ("new", "same", "diff", "size")]
    local = {"new": _MD5, "same": _MD5, "diff": _MD5, "size": _MD5}
    remote = {"same": (1, _MD5), "diff": (1, _OTHER), "size": (9, _MD5), "stray": (1, _MD5)}
    planned, extra = upload_plan(entries, local, remote)
    assert {e["zenodo_name"]: a for e, a in planned} == {
        "new": ACTION_UPLOAD, "same": ACTION_SKIP, "diff": ACTION_CONFLICT,
        "size": ACTION_CONFLICT}
    assert extra == ["stray"]


def test_plan_needs_local_md5_only_for_names_already_on_the_draft():
    planned, _ = upload_plan([{"zenodo_name": "new", "bytes": 1}], {}, {})
    assert [a for _, a in planned] == [ACTION_UPLOAD]


def test_remote_file_ids_and_delete_url():
    dep = {"files": [{"filename": "a", "filesize": 1, "checksum": _MD5, "id": "ab-12"}]}
    assert remote_file_ids(dep) == {"a": "ab-12"}
    assert file_delete_url("42", "ab-12").endswith("/deposit/depositions/42/files/ab-12")
    assert _raises(lambda: file_delete_url("42", "../x"))
    assert _raises(lambda: remote_file_ids({"files": [{"filename": "a"}]}))
