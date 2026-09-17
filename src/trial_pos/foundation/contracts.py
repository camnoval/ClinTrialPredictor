"""Source contract: every dataset implements fetch/normalize/to_domain in one file."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Iterable

from trial_pos.domain.records import TrialRecord


class Source(ABC):
    name: str = ""

    @abstractmethod
    def fetch(self) -> Any:
        """Impure: read files / query DB / download."""

    @abstractmethod
    def normalize(self, raw: Any) -> list[dict]:
        """Pure: raw -> tidy dict rows."""

    @abstractmethod
    def to_domain(self, rows: Iterable[dict]) -> list[TrialRecord]:
        """Pure: rows -> TrialRecords (assigns NCTId)."""

    def run(self) -> list[TrialRecord]:
        return self.to_domain(self.normalize(self.fetch()))
