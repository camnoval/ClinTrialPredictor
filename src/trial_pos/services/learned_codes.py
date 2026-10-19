"""Development codes learned from the registry itself (D-47). Pure.

A trial often lists a drug's code beside its name ('LCZ696' with 'sacubitril/valsartan').
Learning such pairs recovers codes of approved drugs that DrugCentral does not hold, which
is what makes "unresolved = not approved" true enough to measure (the market label's
selection-bias fix).

Evidence comes only from agents RESOLVED under the D-44 alias guard:
  (a) resolved through an other name, the intervention's own name being the code;
  (b) resolved by its own name, one of its other names being the code.
A code is kept only when, as a WHOLE name, it has code shape; it does not already resolve;
every piece of evidence names the SAME drug set; and at least MIN_TRIALS distinct trials
give it. The first unguarded attempt (audit/probe_market_fixes.py) learned partner drugs
('camrelizumab -> apatinib'); the guarded report (probe_learned_codes_20261001.txt) kept 664
codes, the 150 shown all correct.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Callable, Iterable, NamedTuple, Optional

from trial_pos.services.drug_names import compact, key

MIN_TRIALS = 2
CODE_PREFIX_MAX = 5
_CODE = re.compile(r"^[a-z]{1,%d}[\s\-]?\d{2,}[a-z]?$" % CODE_PREFIX_MAX)

KIND_OTHER = "other_name"          # drug_resolution.NAME_OTHER
KIND_PRIMARY = "intervention_name"   # drug_resolution.NAME_PRIMARY

LEARNED_KEPT = "learned codes: kept"
LEARNED_ALREADY = "learned codes: already resolve, not needed"
LEARNED_CONFLICT = "learned codes: evidence names several drug sets, refused"
LEARNED_TOO_FEW = f"learned codes: fewer than {MIN_TRIALS} trials, refused"
LEARNED_OUTCOMES = (LEARNED_KEPT, LEARNED_ALREADY, LEARNED_CONFLICT, LEARNED_TOO_FEW)


class Evidence(NamedTuple):
    nct_id: str
    name_kind: str
    own_name: str
    other_names: tuple
    drugs: frozenset


def code_key(name) -> Optional[str]:
    """The compact key of a name that is, as a whole, a development code; else None."""
    k = key(name)
    return compact(k) if _CODE.match(k) else None


def learn_codes(evidence: Iterable[Evidence], already_resolves: Callable[[str], bool]) -> tuple:
    """-> ({code: drugs} kept, {code: trials} for the kept, audit Counter)."""
    by_code = defaultdict(lambda: defaultdict(set))
    for e in evidence:
        if not e.drugs:
            raise ValueError(f"evidence with no drug: {e}")
        if e.name_kind == KIND_OTHER:
            codes = [code_key(e.own_name)]
        elif e.name_kind == KIND_PRIMARY:
            codes = [code_key(o) for o in e.other_names]
        else:
            raise ValueError(f"name kind {e.name_kind!r}")
        for c in codes:
            if c:
                by_code[c][e.drugs].add(e.nct_id)
    kept, trials, audit = {}, {}, Counter()
    for c in sorted(by_code):
        sets = by_code[c]
        if already_resolves(c):
            audit[LEARNED_ALREADY] += 1
        elif len(sets) > 1:
            audit[LEARNED_CONFLICT] += 1
        else:
            (drugs, ncts), = sets.items()
            if len(ncts) < MIN_TRIALS:
                audit[LEARNED_TOO_FEW] += 1
            else:
                kept[c], trials[c] = drugs, len(ncts)
                audit[LEARNED_KEPT] += 1
    return kept, trials, audit
