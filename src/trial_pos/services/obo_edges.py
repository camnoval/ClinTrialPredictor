"""Parse MONDO is_a (parent) edges out of mondo.obo -- pure, streaming, offline-testable.

fetch_mondo.py reads the same .obo for xrefs but ignores structure; the disease-type-152
rollup needs the hierarchy, so this extracts (child_mondo_id -> parent_mondo_id) edges from
`is_a:` stanzas. Obsolete terms and their edges are dropped (matching fetch_mondo's term
filter). Buffered per [Term] so an is_obsolete line anywhere in the stanza correctly voids it.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

_PARENT = re.compile(r"(MONDO:\d+)")


def parse_is_a(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield (child_mondo_id, parent_mondo_id) for every non-obsolete MONDO is_a edge."""
    cid = ""
    parents: list[str] = []
    obsolete = False
    in_term = False

    def flush():
        if cid and not obsolete:
            for p in parents:
                yield (cid, p)

    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("["):
            yield from flush()
            cid, parents, obsolete = "", [], False
            in_term = line.strip() == "[Term]"
            continue
        if not in_term or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "id" and val.startswith("MONDO:"):
            cid = val
        elif key == "is_obsolete" and val == "true":
            obsolete = True
        elif key == "is_a":
            m = _PARENT.search(val)                 # "MONDO:0005044 ! hypertensive disorder"
            if m:
                parents.append(m.group(1))
    yield from flush()


def build_parent_map(edges: Iterable[tuple[str, str]]) -> dict:
    """(child, parent) edges -> {child: set(parents)} adjacency for upward walks."""
    up: dict[str, set] = {}
    for child, parent in edges:
        up.setdefault(child, set()).add(parent)
    return up


def ancestors(seeds: Iterable[str], parent_map: dict, max_hops: int = 40) -> set:
    """All MONDO ids reachable UP from seeds, INCLUDING the seeds themselves. Cycle-safe
    (visited set) and depth-capped so a malformed graph can't loop forever."""
    result = set(seeds)
    frontier = set(seeds)
    hops = 0
    while frontier and hops < max_hops:
        nxt = set()
        for c in frontier:
            for p in parent_map.get(c, ()):
                if p not in result:
                    result.add(p)
                    nxt.add(p)
        frontier = nxt
        hops += 1
    return result