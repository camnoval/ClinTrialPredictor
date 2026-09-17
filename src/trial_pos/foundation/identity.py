"""Identity keys: NCTId (one trial) and DrugIndication (the prediction unit)."""
from __future__ import annotations
import re
from dataclasses import dataclass

_NCT_RE = re.compile(r"^NCT\d{8}$")


@dataclass(frozen=True, slots=True)
class NCTId:
    value: str

    def __post_init__(self) -> None:
        if not _NCT_RE.match(self.value):
            raise ValueError(f"invalid NCT id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DrugIndication:
    drug: str
    indication: str

    @classmethod
    def of(cls, drug: str, indication: str) -> "DrugIndication":
        return cls(drug.strip().lower(), indication.strip().lower())

    def __str__(self) -> str:
        return f"{self.drug} :: {self.indication}"
