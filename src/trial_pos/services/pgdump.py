"""Read table data out of a plain-SQL pg_dump file, without a database.

A plain dump holds each table's rows in one block:

    COPY public.approval (id, struct_id, approval, type, applicant, orphan) FROM stdin;
    1\t66\t1983-12-30\tFDA\t...\tf
    \\.

Fields are tab-separated in Postgres's text COPY format: `\\N` is NULL, and backslash
escapes encode tab, newline, carriage return, backslash, octal and hex bytes, so one
physical line is always one row. Everything here is pure and streams; the caller owns I/O.

Fails closed: an unterminated block, a row whose field count is not the header's, a table
appearing twice, a requested column the header lacks, or a malformed escape all raise.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator, Optional

COPY_TERMINATOR = "\\."
NULL_TOKEN = "\\N"
FIELD_SEPARATOR = "\t"

_COPY_RE = re.compile(
    r'^COPY\s+(?:(?P<schema>"[^"]+"|[A-Za-z_][\w$]*)\.)?(?P<table>"[^"]+"|[A-Za-z_][\w$]*)'
    r'\s*\((?P<columns>[^)]*)\)\s+FROM\s+stdin;\s*$')

_SIMPLE_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
                   "\\": "\\"}
_OCTAL = "01234567"
_HEX = "0123456789abcdefABCDEF"


class DumpFormatError(ValueError):
    """The dump does not have the shape this reader relies on."""


def _unquote(identifier: str) -> str:
    ident = identifier.strip()
    if len(ident) >= 2 and ident[0] == ident[-1] == '"':
        return ident[1:-1].replace('""', '"')
    return ident


def parse_copy_header(line: str) -> Optional[tuple]:
    """A COPY line -> (schema or None, table, (columns...)); anything else -> None."""
    match = _COPY_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    schema = match.group("schema")
    columns = tuple(_unquote(c) for c in match.group("columns").split(",") if c.strip())
    return (_unquote(schema) if schema else None, _unquote(match.group("table")), columns)


def unescape_field(raw: str) -> Optional[str]:
    """One text-format COPY field -> its value. NULL is None; an empty field is ''."""
    if raw == NULL_TOKEN:
        return None
    if "\\" not in raw:
        return raw
    out = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            raise DumpFormatError(f"trailing backslash in field {raw!r}")
        nxt = raw[i + 1]
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
        elif nxt in _OCTAL:
            j = i + 1
            while j < n and j < i + 4 and raw[j] in _OCTAL:
                j += 1
            out.append(chr(int(raw[i + 1:j], 8)))
            i = j
        elif nxt == "x" and i + 2 < n and raw[i + 2] in _HEX:
            j = i + 2
            while j < n and j < i + 4 and raw[j] in _HEX:
                j += 1
            out.append(chr(int(raw[i + 2:j], 16)))
            i = j
        else:
            # Postgres takes any other escaped character literally.
            out.append(nxt)
            i += 2
    return "".join(out)


def iter_copy_blocks(lines: Iterable[str], wanted: dict,
                     schema: Optional[str] = None) -> Iterator[tuple]:
    """Stream (table, columns, rows) for each wanted table, in dump order.

    `wanted` maps table name -> tuple of columns to keep, in the order given, or None for
    every column in the header's order. Rows are lists of unescaped values. Tables not in
    `wanted` are read past without unescaping. `schema`, when given, restricts matches to
    that schema; an unqualified COPY line matches any schema.
    """
    seen: set = set()
    it = iter(lines)
    for line in it:
        header = parse_copy_header(line)
        if header is None:
            continue
        block_schema, table, columns = header
        keep = table in wanted and (schema is None or block_schema in (None, schema))
        if keep and table in seen:
            raise DumpFormatError(f"table {table!r} appears twice in the dump")
        if keep:
            seen.add(table)
            selected = wanted[table] if wanted[table] is not None else columns
            missing = [c for c in selected if c not in columns]
            if missing:
                raise DumpFormatError(f"{table}: requested columns absent from the dump "
                                      f"header: {missing}; header has {list(columns)}")
            positions = [columns.index(c) for c in selected]
        rows = []
        terminated = False
        for data in it:
            data = data.rstrip("\n")
            if data.endswith("\r"):
                data = data[:-1]
            if data == COPY_TERMINATOR:
                terminated = True
                break
            if not keep:
                continue
            fields = data.split(FIELD_SEPARATOR)
            if len(fields) != len(columns):
                raise DumpFormatError(f"{table}: row has {len(fields)} fields, header has "
                                      f"{len(columns)}")
            rows.append([unescape_field(fields[p]) for p in positions])
        if not terminated:
            raise DumpFormatError(f"COPY block for {table!r} is not terminated by "
                                  f"{COPY_TERMINATOR!r}")
        if keep:
            yield table, tuple(selected), rows


def column_null_profile(columns: tuple, rows: list) -> dict:
    """{column: {"null": n, "empty": n}}. A column with both cannot round-trip through a
    CSV that writes NULL as blank, so the caller records it."""
    out = {c: {"null": 0, "empty": 0} for c in columns}
    for row in rows:
        for c, v in zip(columns, row):
            if v is None:
                out[c]["null"] += 1
            elif v == "":
                out[c]["empty"] += 1
    return out
