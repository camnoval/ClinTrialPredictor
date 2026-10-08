"""Drugs@FDA files: decoding, tab parsing and key normalisation. Pure.

The files are tab-delimited text without quoting. Two of them (Submissions, ApplicationDocs)
only decode as cp1252 (audit/probe_drugsatfda.py). A row whose field count differs from the
header's is COUNTED and set aside, never realigned.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from typing import Optional

DELIMITER = "\t"
ENCODINGS = ("utf-8", "cp1252")
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y")
APPL_NO_WIDTH = 6
PRODUCT_NO_WIDTH = 3

# Ingredient separators in Drugs@FDA ActiveIngredient. Not ',', which appears inside names
# ("ACETIC ACID, GLACIAL"); audit/probe_source_files.py section 4e.
INGREDIENT_SEPARATORS = (";", "||")

ORIGINAL = "ORIG"
SUPPLEMENT = "SUPPL"
APPROVED = "AP"
APPL_NDA = "NDA"
APPL_ANDA = "ANDA"
APPL_BLA = "BLA"


def decode(raw: bytes) -> tuple:
    """-> (text, encoding). Raises when no listed encoding works."""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"decodes as none of {ENCODINGS}")


def parse_tab(text: str) -> dict:
    """-> {header, rows (list of dict), malformed}. Blank lines are skipped."""
    reader = csv.reader(text.splitlines(), delimiter=DELIMITER, quoting=csv.QUOTE_NONE)
    header = [h.strip().lstrip("\ufeff") for h in next(reader, [])]
    rows, malformed = [], 0
    for fields in reader:
        if not any(f.strip() for f in fields):
            continue
        if len(fields) != len(header):
            malformed += 1
            continue
        rows.append({h: f.strip() for h, f in zip(header, fields)})
    return {"header": header, "rows": rows, "malformed": malformed}


def require_columns(table: dict, columns: tuple, name: str) -> None:
    missing = [c for c in columns if c not in table["header"]]
    if missing:
        raise ValueError(f"{name} lacks {missing}; header {table['header']}")


def norm_appl(value) -> str:
    """ApplNo is six digits; some rows drop leading zeros."""
    v = str(value or "").strip()
    return v.zfill(APPL_NO_WIDTH) if v.isdigit() else v


def norm_product(value) -> str:
    v = str(value or "").strip()
    return v.zfill(PRODUCT_NO_WIDTH) if v.isdigit() else v


def parse_date(value) -> Optional[date]:
    v = str(value or "").strip()
    if not v:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None
