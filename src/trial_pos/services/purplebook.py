"""FDA Purple Book (all licensed biologics, CDER and CBER) as approval evidence. Pure.

Source: purplebooksearch.fda.gov, Advanced Search with no filters, Export data (the owner's
download of 2026-10-13: 2,251 product rows, 864 BLAs, 480 CDER and 384 CBER). NOT the
monthly "Historical Data Changes Report", which lists one month's changes only.

What it adds to Drugs@FDA (D-41):
  - a ROUTE per product. Drugs@FDA lists most biologics as 'INJECTABLE;INJECTION';
    the Purple Book says Intravitreal (Eylea) or Subcutaneous (Entyvio, Orencia).
  - a DATE per product, supplements included. Orencia SC is dated 2011-07-29 by its
    supplement; Drugs@FDA's application original said 2005 and IV only.
  - REVOKED licences (Zenapax 1997). A revoked licence was still an approval.
  - CBER products (vaccines, blood, cell and gene therapy). Drugs@FDA has none.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Iterable, NamedTuple, Optional

REQUIRED_COLUMNS = ("BLA Number", "Product Number", "Proprietary Name", "Proper Name",
                    "Dosage Form", "Route of Administration", "Licensure", "Approval Date",
                    "Submission Type", "Center")
DATE_FORMAT = "%B %d, %Y"             # 'September 27, 2023'
NO_BRAND = "N/A"                      # a blank proprietary name is written 'N/A'
ROUTE_SEP = ","
CENTERS = ("CDER", "CBER")


class PurpleBookRow(NamedTuple):
    bla: str
    product: str
    proprietary: str
    proper: str
    form: str                         # 'DOSAGE;ROUTE, ROUTE', upper case, Drugs@FDA's shape
    approved: Optional[date]
    submission_type: str
    licensure: str
    center: str


def parse_pb_date(raw) -> Optional[date]:
    """Blank is None. Anything else must be DATE_FORMAT: a malformed date raises."""
    s = (raw or "").strip()
    if not s:
        return None
    return datetime.strptime(s, DATE_FORMAT).date()


def pb_form(dosage: str, route: str) -> str:
    """Purple Book dosage form and route -> the 'DOSAGE;ROUTE' string form_approval reads."""
    items = [i.strip().upper() for i in (route or "").split(ROUTE_SEP) if i.strip()]
    return f"{(dosage or '').strip().upper()};{', '.join(items)}"


def read_purplebook(rows: Iterable[dict]) -> tuple:
    """-> (rows, audit). Fails closed on a missing column, a malformed date, an unknown
    centre or a repeated (BLA, product) key."""
    out, audit, seen = [], Counter(), set()
    for i, r in enumerate(rows):
        if i == 0:
            missing = [c for c in REQUIRED_COLUMNS if c not in r]
            if missing:
                raise ValueError(f"Purple Book export lacks {missing}: is it the Advanced "
                                 "Search export, not the monthly changes report?")
        k = (r["BLA Number"].strip(), r["Product Number"].strip())
        if k in seen:
            raise ValueError(f"Purple Book (BLA, product) {k} repeats")
        seen.add(k)
        center = r["Center"].strip()
        if center not in CENTERS:
            raise ValueError(f"Purple Book centre {center!r} not in {CENTERS}")
        try:
            approved = parse_pb_date(r["Approval Date"])
        except ValueError as e:
            raise ValueError(f"Purple Book date {r['Approval Date']!r} for {k}: {e}") from e
        brand = r["Proprietary Name"].strip()
        out.append(PurpleBookRow(k[0], k[1], "" if brand == NO_BRAND else brand,
                                 r["Proper Name"].strip(),
                                 pb_form(r["Dosage Form"], r["Route of Administration"]),
                                 approved, r["Submission Type"].strip(),
                                 r["Licensure"].strip(), center))
        audit[f"purple book rows: {center}"] += 1
        if approved is None:
            audit[f"purple book rows undated: {center}"] += 1
    if not out:
        raise ValueError("Purple Book export is empty")
    return out, audit
