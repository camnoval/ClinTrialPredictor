"""The pinned data bundle: what it contains, where it lives, and how a copy is verified.

Three of the four sources cannot be re-fetched at the version this project used: AACT's
live database is a new snapshot on every pull, DrugCentral's download link always serves
the latest release, and Drugs@FDA is overwritten daily with no archive. So reproducing a
run means downloading a PINNED copy, not re-pulling from the sources.

`BUNDLE` declares every file in it. `build_registry` hashes the local copies into the
committed registry (`data_sources.json`); `plan` compares a checkout's files against that
registry. A file is usable only when its size AND sha256 match: there is no "close enough".

Tiers: RUN files are what every script after the pulls reads; REBUILD files are only the
inputs to re-deriving those, such as the DrugCentral archive and the raw AACT dumps.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import quote

SCHEMA_VERSION = 1
HASH_CHUNK_BYTES = 1 << 20
SHA256_HEX_LENGTH = 64
ZENODO_NAME_SEPARATOR = "__"
ZENODO_FILE_URL = "https://zenodo.org/records/{record}/files/{name}?download=1"
DATA_ROOT = "data"

TIER_RUN = "run"
TIER_REBUILD = "rebuild"
TIERS = (TIER_RUN, TIER_REBUILD)

SOURCE_AACT = "aact"
SOURCE_DRUGCENTRAL = "drugcentral"
SOURCE_DRUGSATFDA = "drugsatfda"
SOURCE_ONTOLOGY = "ontology"
SOURCE_HAND_LABELS = "hand_labels"

# `license_checked` is False until someone has read the source's terms and confirmed the
# bundle may redistribute it. `build_registry` reports every unchecked source.
SOURCES = {
    SOURCE_AACT: {"description": "AACT (CTTI) snapshot of ClinicalTrials.gov, and files "
                                 "derived from it by this project's pulls",
                  "license": "ClinicalTrials.gov data; AACT terms of use",
                  "license_checked": False},
    SOURCE_DRUGCENTRAL: {"description": "DrugCentral release archive and the tables "
                                        "extracted from it",
                         "license": "CC BY-SA 4.0 (to be confirmed at drugcentral.org)",
                         "license_checked": False},
    SOURCE_DRUGSATFDA: {"description": "FDA Drugs@FDA data files, as downloaded",
                        "license": "US Government work, public domain",
                        "license_checked": False},
    SOURCE_ONTOLOGY: {"description": "MONDO cross-references built by the archived "
                                     "build_mondo_edges.py",
                      "license": "MONDO: CC BY 4.0 (to be confirmed)",
                      "license_checked": False},
    SOURCE_HAND_LABELS: {"description": "the project owner's blind hand labels and the "
                                        "keys they are scored against",
                         "license": "project's own",
                         "license_checked": False},
}


@dataclass(frozen=True)
class BundleFile:
    path: str          # POSIX, relative to the repository root
    tier: str
    source: str


def _files(source: str, tier: str, paths: Iterable[str]) -> tuple:
    return tuple(BundleFile(p, tier, source) for p in paths)


_DC_TABLES = ("dbversion", "structures", "approval", "approval_type", "identifier",
              "synonyms", "omop_relationship", "ob_product", "struct2obprod",
              "struct2parent", "doid", "doid_xref")
_DRUGSATFDA_TABLES = ("ActionTypes_Lookup", "ApplicationDocs", "Applications",
                      "ApplicationsDocsType_Lookup", "Join_Submission_ActionTypes_Lookup",
                      "MarketingStatus", "MarketingStatus_Lookup", "Products",
                      "SubmissionClass_Lookup", "SubmissionPropertyType", "Submissions",
                      "TE")

BUNDLE = (
    _files(SOURCE_AACT, TIER_RUN, (
        "data/aact/trial_labels.csv",
        "data/aact/trial_labels.csv.manifest.json",
        "data/aact/trial_entities.csv",
        "data/aact/trial_design_outcomes.csv",
        "data/aact/trial_design_outcomes.csv.manifest.json",
        "data/aact/recon_raw_counts.csv",
        "data/aact/recon_raw_measurements.csv",
        "data/aact/trial_registration_fields.csv",
        "data/aact/trial_registration_text.csv",
        "data/aact/trial_registration_fields.provenance.csv",
        "data/aact/trial_registration_fields.manifest.json",
    ))
    + _files(SOURCE_AACT, TIER_REBUILD, (
        "data/aact/results_raw_studies.csv",
        "data/aact/results_raw_outcomes.csv",
    ))
    + _files(SOURCE_DRUGCENTRAL, TIER_RUN,
             tuple(f"data/drugcentral/dc_{t}.csv" for t in _DC_TABLES)
             + ("data/drugcentral/drugcentral.manifest.json",))
    + _files(SOURCE_DRUGCENTRAL, TIER_REBUILD, (
        "data/drugcentral/Drugcentral_2026-09-25.pgdump",
    ))
    + _files(SOURCE_DRUGSATFDA, TIER_RUN,
             tuple(f"data/drugsatfda/{t}.txt" for t in _DRUGSATFDA_TABLES))
    + _files(SOURCE_ONTOLOGY, TIER_RUN, ("data/ontology/mondo_xref.csv",))
    + _files(SOURCE_HAND_LABELS, TIER_RUN, (
        "data/labels/scheme_sheet.csv", "data/labels/scheme_key.csv",
        "data/labels/scheme_instructions.txt",
        "data/labels/confirm_sheet.csv", "data/labels/confirm_key.csv",
        "data/labels/confirm_instructions.txt",
    ))
)

# Files under data/ deliberately left OUT, each with its reason, so that "not in the bundle"
# is always a decision someone wrote down. A path ending in "/" excludes everything below it.
# drugsatfda.zip is not here because it no longer exists: the twelve tables it unpacked to
# are the pinned copy, and FDA keeps no older version to re-download.
EXCLUDED = (
    ("data/.gitkeep", "placeholder that keeps an empty data/ in git"),
    ("data/aact/smoke/", "4,000-trial smoke run of the fields pull; disposable (rev 9 section 12.21)"),
    ("data/drugcentral/Drugcentral_2026-09-25.tables.sql",
     "intermediate: regenerated from the archive by the pg_restore command "
     "extract_drugcentral.py prints"),
    ("data/ontology/disease_type_anchors.csv",
     "earlier-generation ontology artefact; no current script reads it"),
    ("data/ontology/mondo_edges.csv",
     "earlier-generation ontology artefact; no current script reads it"),
)


def exclusion_reason(path: str, excluded: Iterable[tuple] = EXCLUDED):
    """The reason `path` is excluded, or None."""
    for pattern, reason in excluded:
        if path == pattern or (pattern.endswith("/") and path.startswith(pattern)):
            return reason
    return None


# ---- verification statuses --------------------------------------------------
STATUS_VERIFIED = "verified"
STATUS_ABSENT = "absent"
STATUS_SIZE_MISMATCH = "size_mismatch"
STATUS_HASH_MISMATCH = "hash_mismatch"
STATUSES = (STATUS_VERIFIED, STATUS_ABSENT, STATUS_SIZE_MISMATCH, STATUS_HASH_MISMATCH)

FILE_KEYS = ("path", "zenodo_name", "bytes", "sha256", "tier", "source")
REGISTRY_KEYS = ("schema_version", "zenodo_record", "doi", "sources", "files")


class RegistryError(ValueError):
    """The registry is malformed or would write outside the repository."""


def zenodo_name(path: str) -> str:
    """A repository path -> a flat file name, since a Zenodo record has no folders."""
    return path.replace("/", ZENODO_NAME_SEPARATOR)


def file_url(record: str, name: str, template: str = ZENODO_FILE_URL) -> str:
    """`template` exists for a mirror; it takes {record} and {name}."""
    return template.format(record=quote(str(record), safe=""), name=quote(name, safe=""))


def safe_relative_path(path: str) -> PurePosixPath:
    """Raise unless `path` is relative, inside DATA_ROOT, and free of '..' or a drive."""
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or ":" in path or "\\" in path:
        raise RegistryError(f"unsafe path {path!r}")
    if not p.parts or p.parts[0] != DATA_ROOT:
        raise RegistryError(f"{path!r} is not under {DATA_ROOT}/")
    return p


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected_bytes: int, expected_sha256: str) -> str:
    """One of STATUSES. The size is checked first, so a truncated download is cheap to
    detect; the hash is computed only when the size matches."""
    path = Path(path)
    if not path.is_file():
        return STATUS_ABSENT
    if path.stat().st_size != expected_bytes:
        return STATUS_SIZE_MISMATCH
    if sha256_file(path) != expected_sha256:
        return STATUS_HASH_MISMATCH
    return STATUS_VERIFIED


def validate_registry(registry: dict) -> dict:
    """Return the registry unchanged, or raise on anything malformed."""
    if set(registry) != set(REGISTRY_KEYS):
        raise RegistryError(f"registry keys {sorted(registry)} != {sorted(REGISTRY_KEYS)}")
    if registry["schema_version"] != SCHEMA_VERSION:
        raise RegistryError(f"schema_version {registry['schema_version']!r} is not "
                            f"{SCHEMA_VERSION}")
    paths, names = set(), set()
    for entry in registry["files"]:
        if set(entry) != set(FILE_KEYS):
            raise RegistryError(f"file keys {sorted(entry)} != {sorted(FILE_KEYS)}")
        safe_relative_path(entry["path"])
        if entry["path"] in paths or entry["zenodo_name"] in names:
            raise RegistryError(f"duplicate entry {entry['path']!r}")
        paths.add(entry["path"])
        names.add(entry["zenodo_name"])
        if entry["zenodo_name"] != zenodo_name(entry["path"]):
            raise RegistryError(f"{entry['path']!r}: zenodo_name does not match its path")
        sha = entry["sha256"]
        if (not isinstance(sha, str) or len(sha) != SHA256_HEX_LENGTH
                or any(c not in "0123456789abcdef" for c in sha)):
            raise RegistryError(f"{entry['path']!r}: sha256 {sha!r} is not 64 lower-hex")
        if not isinstance(entry["bytes"], int) or entry["bytes"] < 0:
            raise RegistryError(f"{entry['path']!r}: bytes {entry['bytes']!r}")
        if entry["tier"] not in TIERS:
            raise RegistryError(f"{entry['path']!r}: tier {entry['tier']!r}")
        if entry["source"] not in registry["sources"]:
            raise RegistryError(f"{entry['path']!r}: source {entry['source']!r} undeclared")
    return registry


def plan(registry: dict, root: Path, tiers: Iterable[str]) -> list:
    """[(entry, status)] for every file in the requested tiers, in registry order."""
    wanted = set(tiers)
    unknown = wanted - set(TIERS)
    if unknown:
        raise RegistryError(f"unknown tiers {sorted(unknown)}")
    out = []
    for entry in validate_registry(registry)["files"]:
        if entry["tier"] in wanted:
            local = Path(root) / safe_relative_path(entry["path"])
            out.append((entry, verify_file(local, entry["bytes"], entry["sha256"])))
    return out


def status_counts(planned: list) -> dict:
    counts = {s: 0 for s in STATUSES}
    for _, status in planned:
        counts[status] += 1
    return counts


def missing_declared(root: Path, bundle: Iterable[BundleFile] = BUNDLE) -> list:
    return [f.path for f in bundle if not (Path(root) / f.path).is_file()]


def _found(root: Path) -> list:
    base = Path(root) / DATA_ROOT
    if not base.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())


def undeclared_files(root: Path, bundle: Iterable[BundleFile] = BUNDLE,
                     excluded: Iterable[tuple] = EXCLUDED) -> list:
    """Files under DATA_ROOT that are neither in the bundle nor excluded with a reason:
    each is forgotten until someone declares or excludes it."""
    declared = {f.path for f in bundle}
    excluded = tuple(excluded)
    return [p for p in _found(root)
            if p not in declared and exclusion_reason(p, excluded) is None]


def excluded_files(root: Path, excluded: Iterable[tuple] = EXCLUDED) -> list:
    """[(path, reason)] for files present locally that an exclusion covers."""
    excluded = tuple(excluded)
    out = []
    for p in _found(root):
        reason = exclusion_reason(p, excluded)
        if reason is not None:
            out.append((p, reason))
    return out


def build_registry(root: Path, record: str, doi: str,
                   bundle: Iterable[BundleFile] = BUNDLE, hasher=sha256_file) -> dict:
    """Hash every declared local file into a registry. Raises listing EVERY missing file,
    not the first, so one run tells the publisher everything that is absent."""
    bundle = tuple(bundle)
    missing = missing_declared(root, bundle)
    if missing:
        raise RegistryError(f"declared files absent locally: {missing}")
    files = []
    for f in bundle:
        local = Path(root) / safe_relative_path(f.path)
        files.append({"path": f.path, "zenodo_name": zenodo_name(f.path),
                      "bytes": local.stat().st_size, "sha256": hasher(local),
                      "tier": f.tier, "source": f.source})
    used = {f.source for f in bundle}
    return validate_registry({"schema_version": SCHEMA_VERSION, "zenodo_record": str(record),
                              "doi": doi, "sources": {s: SOURCES[s] for s in sorted(used)},
                              "files": files})
