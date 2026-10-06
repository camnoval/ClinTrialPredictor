"""Stratified hand-labelling samples: allocation, deterministic selection, weights.

WHY THIS IS A MODULE AND NOT A FEW LINES IN THE SAMPLER SCRIPT
==============================================================
Three things here produce or destroy a number somebody will later quote, so none of them
belongs in a script: how many items each stratum gets, which items those are, and the
weight that turns a deliberately unbalanced sample back into a population estimate. Get the
third wrong and the reported kappa describes a sample nobody cares about while looking like
it describes the population.

WHY NOT A PROPORTIONAL SAMPLE
=============================
The cells the endpoint-type decision rests on are small. Pharmacokinetic primary endpoints
are a low single-digit percentage of phase 3 outcomes, and that cell is the ENTIRE argument
for gating per trial rather than per phase -- a proportional sample of 200 would put a
handful of them in front of a human and the decisive question would be answered by four
items.

So allocation is square-root of stratum size with a floor. Square-root sits between equal
allocation (best for per-stratum precision, worst for population estimates) and
proportional allocation (the reverse), and the floor guarantees the small decisive cells
are estimable at all. The consequence is stated wherever the result is printed: the pooled
kappa over the whole sample is NOT the population figure, and the population figure comes
from `agreement.weighted_summary` using the weights this module returns.

WHY SELECTION IS HASH-BASED AND NOT RANDOM
==========================================
`random.sample` with a seed is reproducible only against one Python version's RNG and only
if nobody draws in between. Sorting by a salted hash of a stable natural key is
reproducible against nothing but the key itself: the same sample comes back on any machine,
in any version, whatever else the script did first. The salt is a printed flag, so a second
independent sample is available without touching the code.

The key must be a NATURAL key. AACT regenerates surrogate keys nightly (lesson 1), so
selecting on `design_outcomes.id` would silently return a different sample after any
rebuild. `(nct_id, index)` is what the sampler passes in.

WHY DUPLICATES ARE HIDDEN RATHER THAN MARKED
============================================
A subset of the sample appears TWICE, under different opaque label ids and separated in the
presentation order, so the operator labels the same endpoint twice without knowing. That
measures self-consistency, which is the ceiling on what any classifier can score against
these labels: a rule at kappa 0.62 against labels whose own self-agreement is 0.65 is close
to the ceiling, and the same rule against self-agreement of 0.95 is not. Without it, a
mediocre kappa cannot be attributed to the rule rather than the task.

The pairing lives in the KEY file, which the operator should not read while labelling. That
is a convention, not a guarantee, and it is the one part of this design that trust rather
than code has to hold up.
"""
from __future__ import annotations

import hashlib
import math
from typing import Hashable, Iterable, Optional

from trial_pos.services.eligibility import (
    POST_APPROVAL_PHASES, PIVOTAL_PHASES, normalize_phase,
)
from trial_pos.services.endpoint_type import endpoint_text_key

# ---- defaults, every one a flag on the script -----------------------------
# Rev 7 section 12.9, decisions 5 and 6: about 100 rows and 12 repeats. At this size no
# stratum reaches endpoint_type.MIN_STRATUM_FOR_VERDICT (rev 8 section 12.12); the sampler
# prints how many do rather than asserting it.
DEFAULT_SAMPLE_SIZE = 100

# Every non-empty stratum gets at least this many. Sized so the floors of every
# STRATUM_GROUPS x endpoint-class cell fit inside DEFAULT_SAMPLE_SIZE, which a test pins:
# a floor the budget cannot pay makes `sqrt_allocation` raise on the default run.
DEFAULT_MIN_PER_STRATUM = 5

# How many of the selected items are presented twice for the self-agreement check. A
# smell test at this size, not a ceiling (section 12.9 decision 6).
DEFAULT_DUPLICATE_COUNT = 12

# The salt. A flag rather than a constant so a second, independent sample can be drawn
# without editing code, and so the sample is reproducible from the printed value alone.
DEFAULT_SAMPLE_SALT = "endpoint-type-v1"

# Distinct salts for the three hash uses, derived from the one flag. Reusing a single salt
# would correlate selection with presentation order, which would put every duplicate pair
# adjacent to its twin and destroy the blinding.
SALT_SELECT = "select"
SALT_DUPLICATE = "duplicate"
SALT_PRESENT = "present"
SALT_LABEL_ID = "label-id"

LABEL_ID_HEX = 8
LABEL_ID_PREFIX = "L"

# ---- phase groups for stratification --------------------------------------
# Finer than eligibility's early/pivotal/post_approval, because the endpoint-type question
# is specifically about phase 1 against phase 3 and eligibility pools phase 1 with phase 2.
# Built FROM eligibility's constants where they exist, so the AACT phase vocabulary is not
# written down twice; `test_phase_groups_cover_every_known_phase` fails if eligibility
# learns a phase this module does not place.
PHASE_GROUP_PHASE1 = "phase1"
PHASE_GROUP_PHASE2 = "phase2"
PHASE_GROUP_PIVOTAL = "pivotal"
PHASE_GROUP_POST_APPROVAL = "post_approval"
PHASE_GROUP_UNKNOWN = "unknown"

PHASE_GROUPS = (PHASE_GROUP_PHASE1, PHASE_GROUP_PHASE2, PHASE_GROUP_PIVOTAL,
                PHASE_GROUP_POST_APPROVAL, PHASE_GROUP_UNKNOWN)

# PHASE1/PHASE2 sits in the phase-1 group. Same shape of reading as `fdaaa.py`'s treatment
# of it, and the opposite lean: there it is NOT phase-1-only and so in scope for the
# statute, here it is grouped with phase 1 because its endpoints are the early-phase kind.
# Both are readings, and they are not in conflict -- one is about a legal definition, the
# other about what the outcome text looks like -- but the difference is worth stating.
PHASE_GROUP_MEMBERS = {
    PHASE_GROUP_PHASE1: frozenset({"EARLY_PHASE1", "PHASE1", "PHASE1/PHASE2"}),
    PHASE_GROUP_PHASE2: frozenset({"PHASE2"}),
    PHASE_GROUP_PIVOTAL: frozenset(PIVOTAL_PHASES),
    PHASE_GROUP_POST_APPROVAL: frozenset(POST_APPROVAL_PHASES),
}

PHASE_GROUP_DOC = {
    PHASE_GROUP_PHASE1: ("early phase 1, phase 1, and phase 1/2. The stratum the phase-1 "
                         "pharmacokinetic hypothesis is about"),
    PHASE_GROUP_PHASE2: "phase 2 alone, kept separate as the transition stratum",
    PHASE_GROUP_PIVOTAL: ("phase 3 and phase 2/3. The stratum where a pharmacokinetic "
                          "primary endpoint is the case a phase gate would wave through"),
    PHASE_GROUP_POST_APPROVAL: ("phase 4, which endpoint-met keeps while market and "
                                "advancement exclude it"),
    PHASE_GROUP_UNKNOWN: ("phase absent or 'NA'. Counted apart, because absent is not a "
                          "claim about the phase"),
}


def phase_group(raw) -> str:
    """AACT phase value -> its stratification group. UNKNOWN when absent or 'NA'.

    Goes through `eligibility.normalize_phase` so 'NA', blanks and casing are handled in
    one tested place rather than twice.
    """
    phase = normalize_phase(raw)
    if phase is None:
        return PHASE_GROUP_UNKNOWN
    for group, members in PHASE_GROUP_MEMBERS.items():
        if phase in members:
            return group
    return PHASE_GROUP_UNKNOWN


# ---- stratification groups, SETTLED (rev 8 section 12.12) -------------------
# phase1 / phase2+pivotal / post_approval+unknown, chosen on within-stratum homogeneity of
# endpoint type. Coarser than PHASE_GROUPS, which stays in the key file so pivotal remains
# reportable post hoc.
STRATUM_GROUP_PHASE1 = "phase1"
STRATUM_GROUP_PHASE2_PIVOTAL = "phase2_pivotal"
STRATUM_GROUP_POST_APPROVAL_UNKNOWN = "post_approval_unknown"

STRATUM_GROUPS = (STRATUM_GROUP_PHASE1, STRATUM_GROUP_PHASE2_PIVOTAL,
                  STRATUM_GROUP_POST_APPROVAL_UNKNOWN)

STRATUM_GROUP_OF_PHASE_GROUP = {
    PHASE_GROUP_PHASE1: STRATUM_GROUP_PHASE1,
    PHASE_GROUP_PHASE2: STRATUM_GROUP_PHASE2_PIVOTAL,
    PHASE_GROUP_PIVOTAL: STRATUM_GROUP_PHASE2_PIVOTAL,
    PHASE_GROUP_POST_APPROVAL: STRATUM_GROUP_POST_APPROVAL_UNKNOWN,
    PHASE_GROUP_UNKNOWN: STRATUM_GROUP_POST_APPROVAL_UNKNOWN,
}


def stratum_group(raw) -> str:
    """AACT phase value -> its sampling stratum group, through `phase_group`."""
    return STRATUM_GROUP_OF_PHASE_GROUP[phase_group(raw)]


# ---- the sampling unit ------------------------------------------------------
# One DISTINCT, non-blank primary-outcome text per trial. A trial can register the same
# text under several design_outcome_index values, usually one per cohort; keyed on the
# index those are separate units, so a class count inflates and the same question can be
# drawn twice. Blank text is not a unit because there is nothing to label. Both are counted.
SKIP_BLANK_TEXT = "blank_text"
SKIP_REPEATED_TEXT = "repeated_text_within_trial"
UNIT_SKIP_KINDS = (SKIP_BLANK_TEXT, SKIP_REPEATED_TEXT)


def sampling_units(by_trial: dict, text_field: str = "text",
                   text_key=endpoint_text_key) -> tuple:
    """{trial: [outcome dicts]} -> ({trial: [one outcome per distinct text]}, {kind: n}).

    The first outcome in input order represents its text. Scoped to the trial: the same
    text in two trials is two units, each with its own context. A trial left with no unit
    is absent from the result.
    """
    skipped = {kind: 0 for kind in UNIT_SKIP_KINDS}
    out: dict = {}
    for trial, outcomes in by_trial.items():
        seen: set = set()
        kept = []
        for outcome in outcomes:
            key = text_key(outcome.get(text_field))
            if not key:
                skipped[SKIP_BLANK_TEXT] += 1
                continue
            if key in seen:
                skipped[SKIP_REPEATED_TEXT] += 1
                continue
            seen.add(key)
            kept.append(outcome)
        if kept:
            out[trial] = kept
    return out, skipped


# ---- deterministic ordering -----------------------------------------------
def stable_hash(key: Hashable, salt: str) -> str:
    """A salted, stable hex digest of a natural key.

    sha256 rather than Python's `hash`, which is randomised per process by default and
    would return a different sample on every run.
    """
    material = f"{salt}|{key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def label_id(key: Hashable, copy_index: int, salt: str) -> str:
    """An opaque, reproducible id for one row of the labelling file.

    Opaque so the labelling file leaks nothing about phase or predicted class -- both of
    which would bias the labeller toward the hypothesis under test. `copy_index`
    distinguishes the two presentations of a duplicated item, so the twins carry unrelated
    ids and cannot be spotted by eye.
    """
    digest = stable_hash(f"{key}#{copy_index}", f"{salt}|{SALT_LABEL_ID}")
    return f"{LABEL_ID_PREFIX}{digest[:LABEL_ID_HEX]}"


def ordered_by_hash(keys: Iterable[Hashable], salt: str) -> list:
    """Keys in a deterministic pseudo-random order.

    Ties broken by the key itself, so two keys with the same digest prefix cannot reorder
    between runs. The digest is used whole, so a collision would need a sha256 collision.
    """
    return sorted(keys, key=lambda k: (stable_hash(k, salt), str(k)))


# ---- allocation -----------------------------------------------------------
def sqrt_allocation(sizes: dict, total: int = DEFAULT_SAMPLE_SIZE,
                    min_per_stratum: int = DEFAULT_MIN_PER_STRATUM) -> dict:
    """{stratum: population size} -> {stratum: how many to label}.

    Square root of stratum size, with a floor, capped at the stratum's own size, adjusted
    by largest remainder so the allocation sums to `total` exactly.

    Three properties the tests pin, because each of them is a way this could be quietly
    wrong: the allocation sums to `total` (or to the whole population when `total` exceeds
    it); no stratum is allocated more items than it has; and every non-empty stratum gets at
    least `min(min_per_stratum, its size)`.

    Raises when the floors alone exceed the budget, rather than silently dropping strata:
    an allocation that cannot satisfy its own floor is a sample-size decision the operator
    has to make, not one this function should make quietly.
    """
    if total < 0:
        raise ValueError(f"total must be >= 0, got {total}")
    if min_per_stratum < 0:
        raise ValueError(f"min_per_stratum must be >= 0, got {min_per_stratum}")
    strata = sorted(k for k, n in sizes.items() if n > 0)
    if not strata:
        return {}
    population = sum(sizes[k] for k in strata)
    if total >= population:
        # a census. Not an error: it means the budget is larger than the thing being
        # sampled, and labelling everything is the correct response.
        return {k: sizes[k] for k in strata}
    base = {k: min(min_per_stratum, sizes[k]) for k in strata}
    floor_total = sum(base.values())
    if floor_total > total:
        raise ValueError(
            f"floors need {floor_total} items ({len(strata)} strata x up to "
            f"{min_per_stratum}) but total is {total}; raise the sample size or lower "
            f"min_per_stratum -- this is a decision, not something to resolve silently")
    alloc = dict(base)
    remaining = total - floor_total
    while remaining > 0:
        capacity = {k: sizes[k] - alloc[k] for k in strata}
        open_strata = [k for k in strata if capacity[k] > 0]
        if not open_strata:
            break
        weights = {k: math.sqrt(sizes[k]) for k in open_strata}
        weight_total = sum(weights.values())
        ideal = {k: remaining * weights[k] / weight_total for k in open_strata}
        granted = 0
        for k in open_strata:
            take = min(int(ideal[k]), capacity[k])
            alloc[k] += take
            granted += take
        remaining -= granted
        if granted == 0:
            # every ideal share rounded below 1: hand out the rest one at a time, largest
            # fractional claim first, so the loop always terminates.
            order = sorted(open_strata,
                           key=lambda k: (-(ideal[k] - int(ideal[k])), -sizes[k], k))
            for k in order:
                if remaining == 0:
                    break
                if sizes[k] - alloc[k] > 0:
                    alloc[k] += 1
                    remaining -= 1
    return alloc


def stratum_weights(sizes: dict, allocation: dict) -> dict:
    """{stratum: N_h / n_h}, the factor that scales a stratum back to its population.

    This is what `agreement.weighted_summary` multiplies each stratum's confusion matrix by.
    A stratum with no allocation gets no weight key at all rather than a zero or an
    infinity: it contributed no observations, so it has no matrix to scale, and inventing a
    weight for it would imply otherwise.
    """
    out = {}
    for stratum, n in allocation.items():
        if n <= 0:
            continue
        out[stratum] = sizes[stratum] / n
    return out


def select(members: dict, allocation: dict, salt: str = DEFAULT_SAMPLE_SALT) -> dict:
    """{stratum: [keys]} + {stratum: k} -> {stratum: [selected keys]}.

    Deterministic given the keys and the salt. Takes the first k in salted-hash order,
    which is a uniform draw without replacement in expectation and is reproducible without
    an RNG.

    Raises when a stratum is allocated more than it holds, because that means allocation and
    membership were computed from different populations -- the kind of mismatch that
    otherwise shows up later as a weight quietly below 1.
    """
    out = {}
    for stratum, k in sorted(allocation.items()):
        pool = members.get(stratum, [])
        if k > len(pool):
            raise ValueError(f"stratum {stratum!r} allocated {k} items but holds "
                             f"{len(pool)}; allocation and membership disagree")
        out[stratum] = ordered_by_hash(pool, f"{salt}|{SALT_SELECT}")[:k]
    return out


def choose_duplicates(selected: dict, count: int = DEFAULT_DUPLICATE_COUNT,
                      salt: str = DEFAULT_SAMPLE_SALT) -> list:
    """Which selected items get presented twice, spread across strata.

    Allocated across strata by the same square-root rule as the sample itself, so the
    self-agreement figure is not measured entirely on whichever stratum happens to be
    largest. Returned as a flat sorted list of keys.

    Capped at the sample size: asking for more duplicates than there are items is a
    configuration error that would otherwise produce a labelling file where everything
    appears twice.
    """
    sizes = {stratum: len(keys) for stratum, keys in selected.items() if keys}
    available = sum(sizes.values())
    if count > available:
        raise ValueError(f"asked for {count} duplicates from {available} selected items")
    if count <= 0:
        return []
    alloc = sqrt_allocation(sizes, total=count, min_per_stratum=0)
    out = []
    for stratum, k in sorted(alloc.items()):
        pool = ordered_by_hash(selected[stratum], f"{salt}|{SALT_DUPLICATE}")
        out.extend(pool[:k])
    return sorted(out, key=str)


def presentation_rows(selected: dict, duplicates: Iterable[Hashable],
                      salt: str = DEFAULT_SAMPLE_SALT) -> list:
    """Build the labelling rows: one per presentation, shuffled, with opaque ids.

    Returns [{"label_id", "key", "stratum", "copy_index", "duplicate_group"}], in
    presentation order. A duplicated key yields two rows with different label ids, ordered
    independently, so the twins are not adjacent and the operator cannot tell which items
    are repeats. `duplicate_group` is the key itself and lives only in the KEY file.
    """
    dup = set(duplicates)
    rows = []
    for stratum, keys in selected.items():
        for key in keys:
            copies = 2 if key in dup else 1
            for copy_index in range(copies):
                rows.append({
                    "label_id": label_id(key, copy_index, salt),
                    "key": key,
                    "stratum": stratum,
                    "copy_index": copy_index,
                    "duplicate_group": key if copies == 2 else "",
                })
    ids = [row["label_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("label id collision: two presentations share an id, which would "
                         "silently merge two hand labels")
    order = {value: position for position, value in
             enumerate(ordered_by_hash(ids, f"{salt}|{SALT_PRESENT}"))}
    rows.sort(key=lambda row: order[row["label_id"]])
    return rows


def duplicate_pairs(rows: Iterable[dict]) -> list:
    """Presentation rows -> [(label_id_first, label_id_second)] for each duplicated key.

    The self-agreement input. Pairs are ordered by `copy_index`, not by presentation
    position, so which of the two the operator saw first does not change the pairing.
    """
    groups: dict = {}
    for row in rows:
        group = row.get("duplicate_group")
        if not group:
            continue
        groups.setdefault(group, []).append(row)
    out = []
    for group in sorted(groups, key=str):
        members = sorted(groups[group], key=lambda r: r["copy_index"])
        if len(members) != 2:
            raise ValueError(f"duplicate group {group!r} has {len(members)} "
                             f"presentations; expected exactly 2")
        out.append((members[0]["label_id"], members[1]["label_id"]))
    return out


def allocation_report(sizes: dict, allocation: dict,
                      weights: Optional[dict] = None) -> list:
    """Rows for the printed allocation table: stratum, N, n, share, weight.

    Here rather than in the script because the share and the weight are derived values, and
    a derivation in a script is a derivation with no test.
    """
    weights = weights if weights is not None else stratum_weights(sizes, allocation)
    population = sum(sizes.values())
    sampled = sum(allocation.values())
    rows = []
    for stratum in sorted(sizes, key=str):
        size = sizes[stratum]
        n = allocation.get(stratum, 0)
        rows.append({
            "stratum": stratum,
            "population": size,
            "allocated": n,
            "population_share": (size / population) if population else None,
            "sample_share": (n / sampled) if sampled else None,
            "weight": weights.get(stratum),
        })
    rows.sort(key=lambda row: (-row["population"], str(row["stratum"])))
    return rows