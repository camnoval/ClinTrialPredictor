"""Folds for the mixed-era check (D-67): trials of one drug or one sponsor stay together. Pure.

A plain random split puts sister trials (same drug, same sponsor) on both sides, and one
trial's label then gives away another's; for market several trials of one drug share the
same answer. So trials are joined into GROUPS and every group goes to one fold.

TWO GROUPINGS, tried in this order:
  drug_and_sponsor  trials sharing any tested-agent name key OR the lead sponsor are joined,
                    transitively (union-find). The strictest grouping;
  sponsor           trials sharing the lead sponsor only.
Joining transitively can chain most trials into one cluster (a sponsor links its drugs, a
common drug links sponsors). A grouping is usable only when its largest group holds at most
one fold's share of the trials (1 / folds); otherwise the next is tried, and if none is
usable the check is refused, never forced.

Groups are assigned to folds largest first, each to the fold with the fewest trials so far
(ties to the lowest fold number); groups of equal size are ordered by their smallest nct_id.
Deterministic, no randomness.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple, Optional

GROUPING_DRUG_AND_SPONSOR = "drug_and_sponsor"
GROUPING_SPONSOR = "sponsor"
GROUPINGS = (GROUPING_DRUG_AND_SPONSOR, GROUPING_SPONSOR)


class _UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:                    # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))                    # deterministic root
            self.parent[hi] = lo


def groups(trials: Iterable[str], keys_of: dict) -> list:
    """Connected groups of trials that share any key in keys_of[trial] (a set). A trial with
    no key is its own group. Each group sorted; groups ordered by size desc, then first id."""
    trials = sorted(set(trials))
    uf = _UnionFind(trials)
    first = {}
    for t in trials:
        for k in sorted(keys_of.get(t, ())):
            if k in first:
                uf.union(first[k], t)
            else:
                first[k] = t
    by = {}
    for t in trials:
        by.setdefault(uf.find(t), []).append(t)
    return sorted((sorted(g) for g in by.values()), key=lambda g: (-len(g), g[0]))


class Folds(NamedTuple):
    grouping: Optional[str]       # None when no grouping was usable
    fold_of: dict                 # trial -> fold index
    n_groups: int
    largest_group: int
    tried: tuple                  # (grouping, largest group size) for each attempt


def assign_folds(trials: Iterable[str], drug_keys: dict, sponsor: dict, folds: int) -> Folds:
    """drug_keys: trial -> set of name keys; sponsor: trial -> lead-sponsor key ('' or absent
    for none)."""
    if folds < 2:
        raise ValueError(f"need at least 2 folds, got {folds}")
    trials = sorted(set(trials))
    if len(trials) < folds:
        raise ValueError(f"{len(trials)} trials cannot fill {folds} folds")
    limit = len(trials) / folds
    tried = []
    for grouping in GROUPINGS:
        keys = {}
        for t in trials:
            k = {("sponsor", sponsor[t])} if sponsor.get(t) else set()
            if grouping == GROUPING_DRUG_AND_SPONSOR:
                k |= {("drug", d) for d in drug_keys.get(t, ())}
            keys[t] = k
        gs = groups(trials, keys)
        tried.append((grouping, len(gs[0])))
        if len(gs[0]) > limit:
            continue
        size = [0] * folds
        fold_of = {}
        for g in gs:
            f = min(range(folds), key=lambda i: (size[i], i))
            size[f] += len(g)
            for t in g:
                fold_of[t] = f
        return Folds(grouping, fold_of, len(gs), len(gs[0]), tuple(tried))
    return Folds(None, {}, 0, 0, tuple(tried))
