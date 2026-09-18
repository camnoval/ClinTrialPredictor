"""Domain records + label provenance (measured beats derived)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from trial_pos.foundation.identity import DrugIndication, NCTId


class Provenance(str, Enum):
    MEASURED = "measured"
    DERIVED = "derived"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Label:
    approved: Optional[int]
    provenance: Provenance = Provenance.UNKNOWN

    @staticmethod
    def resolve(candidates: "list[Label]") -> "Label":
        order = {Provenance.MEASURED: 0, Provenance.DERIVED: 1, Provenance.UNKNOWN: 2}
        labeled = [c for c in candidates if c.approved is not None]
        if not labeled:
            return Label(None, Provenance.UNKNOWN)
        return sorted(labeled, key=lambda c: order[c.provenance])[0]


@dataclass(slots=True)
class TrialRecord:
    nct_id: NCTId
    phase: Optional[str]
    status: Optional[str]
    start_date: Optional[date]
    completion_date: Optional[date]
    drug: Optional[str] = None
    indication: Optional[str] = None
    label: Label = field(default_factory=lambda: Label(None))
    features: dict = field(default_factory=dict)
    source: str = ""

    def drug_indication(self) -> Optional[DrugIndication]:
        if self.drug is None or self.indication is None:
            return None
        return DrugIndication.of(self.drug, self.indication)


@dataclass(slots=True)
class DrugIndicationRecord:
    key: DrugIndication
    trials: list[TrialRecord] = field(default_factory=list)
    label: Label = field(default_factory=lambda: Label(None))
    features: dict = field(default_factory=dict)
