"""Tests for the data-bundle registry. Plain asserts so tests/_run_stdlib.py works.

Sizes and hashes are computed from the fixture bytes, never transcribed.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from trial_pos.services.data_registry import (
    BUNDLE, DATA_ROOT, EXCLUDED, SOURCES, excluded_files, exclusion_reason, STATUSES, STATUS_ABSENT, STATUS_HASH_MISMATCH,
    STATUS_SIZE_MISMATCH, STATUS_VERIFIED, TIERS, TIER_REBUILD, TIER_RUN, BundleFile,
    RegistryError, build_registry, file_url, missing_declared, plan, safe_relative_path,
    sha256_file, status_counts, undeclared_files, validate_registry, verify_file,
    zenodo_name,
)


def _raises(fn) -> bool:
    try:
        fn()
    except RegistryError:
        return True
    return False


def _tree(files: dict) -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


_A, _B = b"alpha,beta\n1,2\n", b"rebuild-only bytes"
_SMALL = (BundleFile("data/x/a.csv", TIER_RUN, "aact"),
          BundleFile("data/x/b.bin", TIER_REBUILD, "aact"))


def _registry():
    root = _tree({"data/x/a.csv": _A, "data/x/b.bin": _B})
    return root, build_registry(root, "123", "10.5281/zenodo.123", _SMALL)


# ---- the declaration ------------------------------------------------------------
def test_bundle_paths_are_unique_safe_and_flatten_injectively():
    paths = [f.path for f in BUNDLE]
    assert len(paths) == len(set(paths))
    for p in paths:
        safe_relative_path(p)
    names = [zenodo_name(p) for p in paths]
    assert len(names) == len(set(names))


def test_every_bundle_file_has_a_known_tier_and_source():
    for f in BUNDLE:
        assert f.tier in TIERS and f.source in SOURCES, f


def test_every_source_states_a_license_and_whether_it_was_checked():
    for name, meta in SOURCES.items():
        assert meta["license"].strip() and isinstance(meta["license_checked"], bool), name


def test_the_hand_labels_are_in_the_run_tier():
    # they are the only authoritative reference for the gate and cannot be regenerated
    labels = [f for f in BUNDLE if f.source == "hand_labels"]
    assert labels and all(f.tier == TIER_RUN for f in labels)


# ---- paths and urls ---------------------------------------------------------------
def test_unsafe_paths_raise():
    for bad in ("/etc/passwd", "data/../x", "C:/x", "data\\x", "other/x", ""):
        assert _raises(lambda: safe_relative_path(bad)), bad


def test_zenodo_name_has_no_folder_separator():
    assert "/" not in zenodo_name("data/aact/trial_labels.csv")


def test_file_url_quotes_the_name():
    url = file_url("42", "a b.csv")
    assert "a%20b.csv" in url and "/42/" in url


# ---- verification -----------------------------------------------------------------
def test_verify_statuses():
    root = _tree({"data/f": _A})
    good = hashlib.sha256(_A).hexdigest()
    assert verify_file(root / "data/f", len(_A), good) == STATUS_VERIFIED
    assert verify_file(root / "data/none", len(_A), good) == STATUS_ABSENT
    assert verify_file(root / "data/f", len(_A) + 1, good) == STATUS_SIZE_MISMATCH
    assert verify_file(root / "data/f", len(_A), "0" * len(good)) == STATUS_HASH_MISMATCH


def test_sha256_file_matches_hashlib():
    root = _tree({"data/f": _B})
    assert sha256_file(root / "data/f") == hashlib.sha256(_B).hexdigest()


# ---- the registry ------------------------------------------------------------------
def test_build_records_true_sizes_and_hashes():
    _, reg = _registry()
    by_path = {e["path"]: e for e in reg["files"]}
    assert by_path["data/x/a.csv"]["bytes"] == len(_A)
    assert by_path["data/x/a.csv"]["sha256"] == hashlib.sha256(_A).hexdigest()


def test_build_lists_every_missing_file_at_once():
    root = _tree({})
    try:
        build_registry(root, "1", "d", _SMALL)
    except RegistryError as e:
        assert all(f.path in str(e) for f in _SMALL)
        return
    raise AssertionError("built a registry with files absent")


def test_plan_on_the_built_tree_is_all_verified():
    root, reg = _registry()
    counts = status_counts(plan(reg, root, TIERS))
    assert counts[STATUS_VERIFIED] == len(_SMALL)
    assert sum(counts.values()) == len(_SMALL) and set(counts) == set(STATUSES)


def test_plan_respects_tiers_and_sees_absence_and_tampering():
    root, reg = _registry()
    assert len(plan(reg, root, (TIER_RUN,))) == sum(1 for f in _SMALL if f.tier == TIER_RUN)
    (root / "data/x/a.csv").write_bytes(_A.replace(b"1", b"9"))
    (root / "data/x/b.bin").unlink()
    statuses = {e["path"]: s for e, s in plan(reg, root, TIERS)}
    assert statuses == {"data/x/a.csv": STATUS_HASH_MISMATCH, "data/x/b.bin": STATUS_ABSENT}


def test_plan_rejects_an_unknown_tier():
    root, reg = _registry()
    assert _raises(lambda: plan(reg, root, ("everything",)))


def test_validate_rejects_malformed_registries():
    _, reg = _registry()
    entry = reg["files"][0]
    variants = [
        {**reg, "schema_version": 0},
        {**reg, "extra": 1},
        {**reg, "files": [{**entry, "sha256": "ABC"}]},
        {**reg, "files": [{**entry, "path": "data/../../evil"}]},
        {**reg, "files": [{**entry, "tier": "maybe"}]},
        {**reg, "files": [{**entry, "source": "elsewhere"}]},
        {**reg, "files": [{**entry, "bytes": -1}]},
        {**reg, "files": [{**entry, "zenodo_name": "renamed.csv"}]},
        {**reg, "files": [entry, entry]},
        {**reg, "files": [{**entry, "unexpected": True}]},
    ]
    for v in variants:
        assert _raises(lambda: validate_registry(v)), v


def test_undeclared_and_missing_are_reported():
    root = _tree({"data/x/a.csv": _A, "data/x/stray.csv": b"?"})
    assert undeclared_files(root, _SMALL) == ["data/x/stray.csv"]
    assert missing_declared(root, _SMALL) == ["data/x/b.bin"]
    assert undeclared_files(_tree({}), _SMALL) == []


def test_data_root_is_the_first_path_part_of_every_bundle_file():
    assert all(f.path.split("/")[0] == DATA_ROOT for f in BUNDLE)


# ---- exclusions -------------------------------------------------------------------
def test_no_path_is_both_declared_and_excluded():
    for f in BUNDLE:
        assert exclusion_reason(f.path) is None, f.path


def test_every_exclusion_has_a_reason_and_sits_under_data():
    for pattern, reason in EXCLUDED:
        assert reason.strip() and pattern.split("/")[0] == DATA_ROOT, pattern


def test_an_excluded_file_is_listed_with_its_reason_not_as_undeclared():
    rules = (("data/x/stray.csv", "why"), ("data/smoke/", "disposable"))
    root = _tree({"data/x/a.csv": _A, "data/x/stray.csv": b"?",
                  "data/smoke/deep/f.csv": b"?", "data/x/forgotten.csv": b"?"})
    assert undeclared_files(root, _SMALL, rules) == ["data/x/forgotten.csv"]
    assert excluded_files(root, rules) == [("data/smoke/deep/f.csv", "disposable"),
                                           ("data/x/stray.csv", "why")]


def test_a_prefix_exclusion_needs_its_trailing_slash():
    assert exclusion_reason("data/smokeless.csv", (("data/smoke/", "r"),)) is None
    assert exclusion_reason("data/smoke/a.csv", (("data/smoke/", "r"),)) == "r"


def test_no_aact_file_is_redistributed():
    from trial_pos.services.data_registry import SOURCE_AACT
    assert not [f for f in BUNDLE if f.source == SOURCE_AACT or f.path.startswith("data/aact/")]
    assert exclusion_reason("data/aact/trial_labels.csv") is not None
