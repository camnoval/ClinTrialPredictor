"""Pure helpers for uploading the data bundle to a Zenodo DRAFT through its REST API.

Nothing here publishes. Publishing is irreversible, so it stays a deliberate click in the
browser after the uploaded files have been checked.

Zenodo reports a file's checksum as md5, either bare or as "md5:<hex>". The upload plan
compares that against the local md5, so a file already uploaded intact is skipped and one
that differs is a CONFLICT, never silently overwritten.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

API_BASE = "https://zenodo.org/api"
MD5_PREFIX = "md5:"
MD5_HEX_LENGTH = 32
HASH_CHUNK_BYTES = 1 << 20

ACTION_UPLOAD = "upload"
ACTION_SKIP = "skip_identical"
ACTION_CONFLICT = "conflict"
ACTIONS = (ACTION_UPLOAD, ACTION_SKIP, ACTION_CONFLICT)


class ZenodoError(ValueError):
    """The deposition is not in a state, or a shape, this code can safely act on."""


def deposition_url(record: str) -> str:
    if not str(record).isdigit():
        raise ZenodoError(f"record id {record!r} is not numeric")
    return f"{API_BASE}/deposit/depositions/{record}"


def record_from_doi(doi: str) -> str:
    """'10.5281/zenodo.123' -> '123'. Raises on any other shape."""
    prefix = "10.5281/zenodo."
    if not doi.startswith(prefix) or not doi[len(prefix):].isdigit():
        raise ZenodoError(f"{doi!r} is not a Zenodo DOI")
    return doi[len(prefix):]


def normalise_md5(value) -> str:
    v = str(value or "").strip().lower()
    if v.startswith(MD5_PREFIX):
        v = v[len(MD5_PREFIX):]
    if len(v) != MD5_HEX_LENGTH or any(c not in "0123456789abcdef" for c in v):
        raise ZenodoError(f"checksum {value!r} is not an md5")
    return v


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def editable(deposition: dict) -> None:
    """Raise unless the deposition is an unpublished draft with a file bucket."""
    if deposition.get("submitted") is not False:
        raise ZenodoError("deposition is published or its state is unknown; files can "
                          "only be added to an unpublished draft")
    if not (deposition.get("links") or {}).get("bucket"):
        raise ZenodoError("deposition has no bucket link")


def remote_files(deposition: dict) -> dict:
    """{filename: (size, md5)} from a deposition's `files` list."""
    out = {}
    for f in deposition.get("files") or []:
        name = f.get("filename") or f.get("key")
        size = f.get("filesize", f.get("size"))
        if not name or size is None:
            raise ZenodoError(f"unrecognised file entry {sorted(f)}")
        out[name] = (int(size), normalise_md5(f.get("checksum")))
    return out


def upload_plan(entries: list, local_md5: dict, remote: dict) -> tuple:
    """-> ([(entry, action)], [remote names not in the registry]).

    `entries` are registry file entries; `local_md5` maps zenodo_name -> md5 hex."""
    planned = []
    for e in entries:
        name = e["zenodo_name"]
        if name not in remote:
            planned.append((e, ACTION_UPLOAD))
            continue
        size, md5 = remote[name]
        same = size == e["bytes"] and md5 == local_md5[name]
        planned.append((e, ACTION_SKIP if same else ACTION_CONFLICT))
    names = {e["zenodo_name"] for e in entries}
    return planned, sorted(n for n in remote if n not in names)
